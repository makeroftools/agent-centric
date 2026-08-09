"""Agent execution backends: in-process and subprocess isolation.

The Manager governs agents through a uniform :class:`AgentSession` interface.
Only the agent's generator loop lives inside a session; every authority —
tool mediation, policy, envelopes, trajectory recording, verification, and
cancellation — stays in the Manager process. Two backends provide sessions:

- :class:`InProcessBackend` — the agent runs in the Manager's own process. This
  is the default and is what unit tests use for speed.
- :class:`SubprocessBackend` — the agent runs in a separate child process,
  communicating over a minimal, explicit, versioned JSON-lines protocol on the
  child's stdin/stdout. Agent crashes, hangs, or misbehaviour cannot corrupt
  Manager state.

Isolation is additive: it never relaxes verification or mediation. A child
crash, non-zero exit, protocol violation, or timeout is an explicit, audited
failure (fail-closed). No verified success is ever produced from an unverified
agent outcome.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from typing import Any, Protocol, cast

from ..agents.interface import (
    Agent,
    AgentResult,
    AgentStep,
    Cancelled,
    ToolContext,
    ToolRequest,
    ToolResult,
)
from ..contracts.manifest import AgentComponentManifest
from ..contracts.tool import ToolDescriptor, ToolVersion

# Version of the Manager<->agent IPC protocol.
IPC_VERSION = "agent-ipc.v1"


class AgentExecutionError(Exception):
    """Raised when an agent session fails (crash, protocol error, agent error).

    The Manager converts this into an explicit, audited failure. It never
    yields a verified success.
    """


class SubprocessTimeoutError(AgentExecutionError):
    """Raised when a subprocess agent does not respond within its deadline.

    This is a distinct subclass so the Manager can map a silent/hung child to an
    explicit ``TIMEOUT`` failure (and force-terminate it as a last resort)
    rather than a generic ``AGENT_ERROR``. It is subprocess-specific: the
    in-process backend never raises it.
    """


# Grace periods for child teardown. ``close()`` first asks the child to exit
# cooperatively and waits up to ``_COOPERATIVE_GRACE``; if it does not, the
# Manager terminates it and, as a last resort, kills it, waiting up to
# ``_FORCED_GRACE`` each time. These bound the child's lifetime so no zombie
# survives a normal test path.
_COOPERATIVE_GRACE = 2.0
_FORCED_GRACE = 2.0


def instantiate_agent(entry_point: str) -> Agent:
    """Resolve and call the agent factory from a manifest entry point."""
    module_path, _, attr = entry_point.rpartition(":")
    if not module_path or not attr:
        raise ValueError(f"Malformed entry point: {entry_point!r}")
    module = importlib.import_module(module_path)
    factory = getattr(module, attr)
    agent = factory()
    if not callable(agent):
        raise TypeError(f"Agent factory {entry_point!r} did not return a callable.")
    return cast(Agent, agent)


# ---------------------------------------------------------------------------
# Session interface
# ---------------------------------------------------------------------------


class AgentSession(Protocol):
    """A single agent execution, driven by the Manager.

    ``next_step(sent)`` advances the agent by delivering ``sent`` (the value of
    the previous ``yield``) and returns the next yielded item — an
    ``AgentStep``, a ``ToolRequest``, or an ``AgentResult`` when the agent
    finishes. ``cancel(reason)`` delivers a cooperative ``Cancelled`` signal.
    ``close()`` releases the session (closing the generator or terminating the
    child process).
    """

    def next_step(self, sent: Any) -> AgentStep | ToolRequest | AgentResult: ...
    def cancel(self, reason: str) -> None: ...
    def close(self) -> None: ...


class ExecutionBackend(Protocol):
    """Creates an agent session for a manifest."""

    def session(
        self,
        manifest: AgentComponentManifest,
        payload: Any,
        step_budget: int,
        tool_context: ToolContext,
        timeout_seconds: float,
    ) -> AgentSession: ...


# ---------------------------------------------------------------------------
# In-process backend
# ---------------------------------------------------------------------------


class InProcessSession:
    """Drives an agent generator in the Manager's own process."""

    def __init__(
        self,
        agent: Agent,
        payload: Any,
        step_budget: int,
        tool_context: ToolContext,
    ) -> None:
        self._gen: Generator[
            AgentStep | ToolRequest, ToolResult | None | Cancelled, AgentResult
        ] = agent(payload, step_budget, tool_context)
        self._closed = False

    def next_step(self, sent: Any) -> AgentStep | ToolRequest | AgentResult:
        if self._closed:
            raise AgentExecutionError("Agent session is closed.")
        try:
            return self._gen.send(sent)
        except StopIteration as stop:
            result = stop.value
            if not isinstance(result, AgentResult):
                raise AgentExecutionError(
                    "Agent returned a non-AgentResult value."
                ) from None
            return result
        except Exception as exc:  # noqa: BLE001 - converted to explicit failure
            raise AgentExecutionError(f"Agent raised an exception: {exc}") from exc

    def cancel(self, reason: str) -> None:
        if self._closed:
            return
        try:
            self._gen.send(Cancelled(reason=reason))
        except StopIteration:
            pass  # the agent cooperated and exited cleanly
        except Exception:  # noqa: BLE001 - cancellation is advisory only
            pass  # a non-cooperative agent does not change the outcome

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort release
            self._gen.close()


class InProcessBackend:
    """Runs agents in the Manager's own process (default)."""

    def session(
        self,
        manifest: AgentComponentManifest,
        payload: Any,
        step_budget: int,
        tool_context: ToolContext,
        timeout_seconds: float,
    ) -> AgentSession:
        # The in-process backend blocks on the agent's generator directly; the
        # Manager's envelope loop is the authority for timeouts. ``timeout_seconds``
        # is accepted for interface clarity only and is not used here.
        agent = instantiate_agent(manifest.entry_point)
        return InProcessSession(agent, payload, step_budget, tool_context)


# ---------------------------------------------------------------------------
# IPC serialisation (versioned, explicit, JSON-lines)
# ---------------------------------------------------------------------------


def _encode_sent(value: Any) -> dict[str, Any]:
    """Encode a value delivered into the agent (None, ToolResult, Cancelled)."""
    if value is None:
        return {"kind": "none"}
    if isinstance(value, ToolResult):
        return {
            "kind": "tool_result",
            "success": value.success,
            "output": value.output,
            "error": value.error,
        }
    if isinstance(value, Cancelled):
        return {"kind": "cancelled", "reason": value.reason}
    raise AgentExecutionError(f"Cannot encode sent value: {value!r}")


def _decode_sent(data: dict[str, Any]) -> Any:
    kind = data.get("kind")
    if kind == "none":
        return None
    if kind == "tool_result":
        return ToolResult(
            success=bool(data.get("success")),
            output=data.get("output"),
            error=data.get("error"),
        )
    if kind == "cancelled":
        return Cancelled(reason=data.get("reason"))
    raise AgentExecutionError(f"Unknown sent value kind: {kind!r}")


def _encode_yield(item: AgentStep | ToolRequest) -> dict[str, Any]:
    if isinstance(item, AgentStep):
        return {
            "kind": "agent_step",
            "description": item.description,
            "detail": item.detail,
        }
    if isinstance(item, ToolRequest):
        return {"kind": "tool_request", "name": item.name, "args": item.args}
    raise AgentExecutionError(f"Cannot encode yielded item: {item!r}")


def _decode_yield(data: dict[str, Any]) -> AgentStep | ToolRequest:
    kind = data.get("kind")
    if kind == "agent_step":
        return AgentStep(description=str(data.get("description", "")), detail=data.get("detail"))
    if kind == "tool_request":
        return ToolRequest(name=str(data.get("name", "")), args=data.get("args") or {})
    raise AgentExecutionError(f"Unknown yielded item kind: {kind!r}")


def _encode_tool_context(tool_context: ToolContext) -> list[dict[str, Any]]:
    return [
        {
            "version": tool.version.value,
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "execution_semantics": tool.execution_semantics,
        }
        for tool in tool_context.tools
    ]


def _decode_tool_context(data: list[dict[str, Any]]) -> ToolContext:
    descriptors: list[ToolDescriptor] = []
    for d in data:
        descriptors.append(
            ToolDescriptor(
                version=ToolVersion(d.get("version", ToolVersion.V1.value)),
                name=str(d.get("name", "")),
                description=str(d.get("description", "")),
                input_schema=dict(d.get("input_schema") or {}),
                output_schema=str(d.get("output_schema", "")),
                execution_semantics=str(d.get("execution_semantics", "")),
            )
        )
    return ToolContext(tools=tuple(descriptors))


# ---------------------------------------------------------------------------
# Subprocess backend
# ---------------------------------------------------------------------------


class SubprocessSession:
    """Drives an agent in a separate child process over JSON-lines IPC.

    The child runs only the agent's generator loop. All authority remains in
    the Manager. A child crash, non-zero exit, protocol violation, or silent
    hang surfaces as an :class:`AgentExecutionError` (fail-closed).

    Reads are bounded by ``timeout_seconds`` (the envelope deadline): a child
    that stops responding raises :class:`SubprocessTimeoutError`, which the
    Manager maps to an explicit ``TIMEOUT`` failure and force-terminates as a
    last resort. ``termination`` records how the child ended (``completed``,
    ``cooperative``, or ``forced``) so the Manager can audit it honestly.
    """

    def __init__(
        self,
        manifest: AgentComponentManifest,
        payload: Any,
        step_budget: int,
        tool_context: ToolContext,
        timeout_seconds: float,
    ) -> None:
        self._stderr: list[str] = []
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "meta_harness.control_plane.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        # Drain stderr in a daemon thread so a chatty child cannot deadlock the
        # pipe; the captured text is surfaced on failure for diagnostics.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()
        # Read stdout in a daemon thread into a queue so ``_read`` can wait with
        # a bounded timeout instead of blocking forever on a silent child.
        self._stdout_queue: queue.Queue[str | None] = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, daemon=True
        )
        self._stdout_thread.start()
        self._closed = False
        # How the child ended: None (still running), ``completed`` (produced a
        # result and exited), ``cooperative`` (exited on a cancel/close signal),
        # or ``forced`` (had to be terminated/killed as a last resort).
        self.termination: str | None = None
        # The absolute monotonic deadline for reads, derived from the envelope
        # timeout so the Manager remains the authority on time.
        self._deadline = time.monotonic() + timeout_seconds
        self._write(
            {
                "type": "start",
                "version": IPC_VERSION,
                "entry_point": manifest.entry_point,
                "payload": payload,
                "step_budget": step_budget,
                "tools": _encode_tool_context(tool_context),
            }
        )

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip("\n"))

    def _drain_stdout(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._stdout_queue.put(line)
        self._stdout_queue.put(None)  # EOF sentinel

    def _write(self, message: dict[str, Any]) -> None:
        if self._proc.stdin is None or self._proc.stdin.closed:
            raise AgentExecutionError("Agent process stdin is closed.")
        try:
            self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AgentExecutionError("Agent process terminated unexpectedly.") from exc

    def _read(self) -> dict[str, Any] | None:
        """Read the next IPC message, bounded by the session deadline.

        Returns ``None`` on EOF (child exited). Raises
        :class:`SubprocessTimeoutError` if the child does not respond before
        the envelope deadline, so a silent/hung child cannot block the Manager
        indefinitely.
        """
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise SubprocessTimeoutError(
                "Agent subprocess did not respond within the envelope timeout."
            )
        try:
            line = self._stdout_queue.get(timeout=remaining)
        except queue.Empty:
            raise SubprocessTimeoutError(
                "Agent subprocess did not respond within the envelope timeout."
            ) from None
        if line is None:
            return None
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentExecutionError(f"Malformed agent IPC message: {exc}") from exc
        if not isinstance(data, dict):
            raise AgentExecutionError("Agent IPC message is not an object.")
        return data

    def _raise_child_failure(self) -> None:
        """Raise a fail-closed error describing a dead or misbehaving child."""
        code = self._proc.poll()
        detail = f" (exit code {code})" if code is not None else ""
        stderr = "\n".join(self._stderr[-20:]).strip()
        suffix = f" stderr: {stderr}" if stderr else ""
        raise AgentExecutionError(
            f"Agent subprocess terminated unexpectedly{detail}.{suffix}"
        )

    def next_step(self, sent: Any) -> AgentStep | ToolRequest | AgentResult:
        if self._closed:
            raise AgentExecutionError("Agent session is closed.")
        try:
            self._write({"type": "send", "value": _encode_sent(sent)})
        except AgentExecutionError:
            self._raise_child_failure()
            raise
        message = self._read()
        if message is None:
            self._raise_child_failure()
            raise AssertionError("unreachable")  # pragma: no cover
        msg_type = message.get("type")
        if msg_type == "step":
            return _decode_yield(message.get("value") or {})
        if msg_type == "result":
            value = message.get("value") or {}
            return AgentResult(output=value.get("output"))
        if msg_type == "error":
            raise AgentExecutionError(str(message.get("message", "agent error")))
        self._raise_child_failure()
        raise AssertionError("unreachable")  # pragma: no cover

    def cancel(self, reason: str) -> None:
        if self._closed:
            return
        # Cooperative cancellation: deliver a Cancelled value as the next send.
        # If the child ignores it, the Manager enforces the envelope by calling
        # close() (which terminates the child) as a last resort.
        with contextlib.suppress(AgentExecutionError):  # child already gone
            self._write({"type": "send", "value": _encode_sent(Cancelled(reason=reason))})

    def close(self) -> None:
        """Release the child process, reaping it and recording how it ended.

        If the child already exited (e.g. produced a result), it is reaped and
        ``termination`` is ``completed``. Otherwise the child is asked to exit
        cooperatively and given a short grace period; if it does not, it is
        terminated and, as a last resort, killed (``termination`` is
        ``cooperative`` or ``forced`` respectively). This bounds the child's
        lifetime so no zombie survives a normal test path.
        """
        if self._closed:
            return
        self._closed = True
        if self._proc.poll() is not None:
            self.termination = "completed"
            return
        try:
            if self._proc.stdin is not None and not self._proc.stdin.closed:
                self._proc.stdin.write(json.dumps({"type": "close"}) + "\n")
                self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            self._proc.wait(timeout=_COOPERATIVE_GRACE)
            self.termination = "cooperative"
            return
        except subprocess.TimeoutExpired:
            pass
        # Forced termination as a last resort.
        self._proc.terminate()
        try:
            self._proc.wait(timeout=_FORCED_GRACE)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=_FORCED_GRACE)
        self.termination = "forced"


class SubprocessBackend:
    """Runs agents in a separate child process (isolated)."""

    def session(
        self,
        manifest: AgentComponentManifest,
        payload: Any,
        step_budget: int,
        tool_context: ToolContext,
        timeout_seconds: float,
    ) -> AgentSession:
        return SubprocessSession(
            manifest, payload, step_budget, tool_context, timeout_seconds
        )