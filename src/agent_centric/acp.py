"""ACP adapter: expose Agent-centric's FBP platform as an External Agent in Zed.

This module bridges the Agent Client Protocol (ACP) — a client-facing transport
used by editors such as Zed — to the **FBP subsystem** (the deterministic
platform that is now the real architecture). ACP is an edge transport only: the
``FbpDriver`` remains the sole authority for policy, verification, and audit.
No ACP path can produce a verified success that bypasses the FBP correctness
chain (every ``run`` is a normal directive, parent re-verified, ledgered,
replayable).

The adapter implements the smallest usable ACP surface:

- ``initialize`` — honest, minimal capabilities (all disabled by default).
- ``session/new`` — a session id is minted; one process owns one driver.
- ``session/prompt`` — a user prompt is mapped to a deterministic FBP run (or
  plan / component network) and the verified result (or a clear fail-closed
  failure) is streamed back as agent text.
- ``session/cancel`` — tracked per session. Mid-run cancellation of the
  synchronous driver call is not pre-emptible in v1; this is documented.

Prompt commands (first whitespace-delimited token):

- ``double <n>`` / ``square <n>`` / ``negate <n>`` — verified arithmetic.
- ``sum <a> <b>`` — verified addition.
- ``model <text>`` — the ``model`` agent (deterministic stub by default; a real
  OpenRouter provider is wired when ``OPENROUTER_API_KEY`` is set).
- ``store set <key> <json>`` / ``store get <key>`` — mediated single-writer
  store access (grant-scoped).
- ``bills intake <json>`` / ``bills accept <json>`` / ``bills calendar <from> <to>``
  — the demonstration bills loop (human-gated accept).
- ``status`` / ``tree`` — read-only operator inspection.
- ``network <json>`` — run a component network as true dataflow.
- anything else fails closed with a clear message (never a guessed success).

Only deterministic, offline-safe paths run by default; a real model provider is
an opt-in env-driven hook. Use the ``agent-centric-acp`` console entry point (or
``python -m agent_centric.acp``) and point an ACP client at it over stdio.

**Threading model.** The ``FbpDriver`` binds a private asyncio event loop and
drives it with ``run_until_complete``. That loop must never be the ACP process
loop (which is already running asyncio), so the driver is hosted on a dedicated
worker thread: the driver's loop is bound to that thread, and every command is
submitted to the worker and awaited. This is the same isolation the landing-page
server needs, generalised to an asyncio caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import queue
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

from acp import (
    PROTOCOL_VERSION,
    Agent,
    PromptResponse,
    run_agent,
    text_block,
    update_agent_message,
)
from acp.exceptions import RequestError
from acp.interfaces import Client
from acp.schema import (
    AudioContentBlock,
    AuthenticateResponse,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    ForkSessionResponse,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    ResourceContentBlock,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    TextContentBlock,
)

from agent_centric.fbp import FbpDriver
from agent_centric.fbp.network import network_from_dict

# The ACP protocol version this agent implements and advertises.
_ACP_VERSION = PROTOCOL_VERSION

# The agent identity advertised during initialize.
_AGENT_NAME = "agent-centric"
_AGENT_VERSION = "0.29.0"

# Content-block types a client may send in ``session/prompt``.
_PromptBlock = (
    TextContentBlock
    | ImageContentBlock
    | AudioContentBlock
    | ResourceContentBlock
    | EmbeddedResourceContentBlock
)


def _block_text(block: Any) -> str:
    """Extract plain text from an ACP content block (or a raw dict)."""
    if isinstance(block, dict):
        text = block.get("text")
        return text if isinstance(text, str) else ""
    return getattr(block, "text", "") if isinstance(block, TextContentBlock) else ""


def _as_text(value: Any) -> str:
    """Render a verified output as plain text for the ACP stream."""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, sort_keys=True)


def _parse_int(text: str, name: str) -> int:
    """Parse a required integer field, failing closed on garbage."""
    try:
        return int(text.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {text!r}") from exc


def _openrouter_http_client(model: str) -> Any:
    """A stdlib ``urllib`` transport for OpenRouter's chat-completions API.

    Mirrors the landing-page client so the fail-closed, secret-redacting,
    timeout-bounded provider path is reused. Builds the JSON request body and
    parses the ``choices``/``message`` shape, returning just the text block.
    Raises on HTTP/transport/parse errors; the provider maps those to
    ``ModelProviderError``.
    """

    def client(endpoint: str, headers: dict[str, str], prompt: str) -> str:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        })
        request = urllib.request.Request(
            endpoint, data=payload.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter request failed: {exc}") from exc
        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected OpenRouter response: {body[:200]}") from exc
        if not isinstance(content, str):
            raise RuntimeError(f"unexpected OpenRouter response: {body[:200]}")
        return content

    return client


def _parse_json(text: str, name: str) -> dict[str, Any]:
    """Parse a required JSON object field, failing closed on garbage."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be valid JSON, got {text!r}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


class _DriverHost:
    """Host the ``FbpDriver`` on a dedicated worker thread.

    The driver binds a private asyncio event loop and drives it with
    ``run_until_complete``. That loop must not be the ACP loop (which is already
    running), so the driver is constructed and driven on this worker thread. A
    command is a plain callable ``fn(driver) -> result``; it is submitted to the
    worker and awaited by the caller. Fail-closed: a command that raises is
    surfaced to the caller, never swallowed.
    """

    def __init__(self) -> None:
        self._driver: FbpDriver | None = None
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._ready = threading.Event()
        self._commands: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread and wait until the driver is built."""
        if self._thread is not None:
            self._ready.wait(timeout=5.0)
            if self._driver is None:
                raise RuntimeError("FBP driver failed to start on the worker thread")
            return
        self._thread = threading.Thread(target=self._run, name="fbp-acp-driver", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._driver is None:
            raise RuntimeError("FBP driver failed to start on the worker thread")

    def _run(self) -> None:
        """Worker entry: build the driver, then serve commands until stopped."""
        try:
            driver = self._build_driver()
        except BaseException:  # noqa: BLE001 - surfaced to the caller
            self._driver = None
            self._ready.set()
            raise
        self._driver = driver
        self._ready.set()
        while True:
            item = self._commands.get()
            if item is None:  # stop sentinel
                break
            fn, result_q = item
            try:
                result_q.put((fn(driver), None))
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
                result_q.put((None, exc))

    def _build_driver(self) -> FbpDriver:
        """Build a driver with a deterministic demo tree."""
        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("square", lambda value: value * value, source_url="file:///tasks/square")
        driver.register("negate", lambda value: -value, source_url="file:///tasks/negate")
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        driver.register("odd", lambda value: isinstance(value, int) and value % 2 == 1)
        driver.register("positive", lambda value: value > 0)
        driver.register("sum", lambda a, b: a + b, source_url="file:///tasks/sum")
        driver.configure(
            tasks=("double", "square", "negate", "even", "odd", "positive", "sum"),
            verifiers=("even", "odd", "positive"),
        )
        self._tmpdir = tempfile.TemporaryDirectory(prefix="agent-centric-acp-store-")
        driver.spawn("store", kind="store")
        driver.configure_child(
            "store",
            state=os.path.join(self._tmpdir.name, "store.db"),
            store_keys=("acp-*",),
        )
        driver.spawn("model", kind="model")
        self._wire_real_model(driver)
        driver.spawn("bills", kind="bills")
        driver.run(
            "bills_setup",
            {
                "state": os.path.join(self._tmpdir.name, "bills.db"),
                "store_keys": ["bill-*"],
            },
            child="bills",
        )
        return driver

    def _wire_real_model(self, driver: FbpDriver) -> None:
        """Wire an OpenRouter provider to the model child when a key is set.

        Reads ``OPENROUTER_API_KEY`` (never hardcoded). When absent, the model
        agent serves its deterministic stub (offline, CI-safe). The correctness
        chain is untouched — the parent still re-verifies the model's output.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return
        model = os.environ.get("OPENROUTER_MODEL", "").strip() or "deepseek/deepseek-v4-flash-0731"
        try:
            from agent_centric.providers import build_real_model_provider

            provider = build_real_model_provider(
                endpoint="https://openrouter.ai/api/v1/chat/completions",
                api_key=api_key,
                http_client=_openrouter_http_client(model),
            )
            driver.configure_provider("model", provider, model_id=model)
        except Exception:  # noqa: BLE001 - a real provider is opt-in; fail closed to the stub
            return

    # -- submission ---------------------------------------------------------

    def submit(self, fn: Callable[[FbpDriver], Any]) -> Any:
        """Run ``fn(driver)`` on the worker thread and return its result.

        The driver's loop is bound to the worker thread, so the synchronous
        driver call runs on the correct loop. A raised exception is re-raised on
        the caller's thread (fail-closed: never swallowed).
        """
        self.start()
        result_q: queue.Queue[tuple[Any, BaseException | None]] = queue.Queue(maxsize=1)
        self._commands.put((fn, result_q))
        result, error = result_q.get()
        if error is not None:
            raise error
        return result

    def close(self) -> None:
        """Teardown the driver and stop the worker thread."""
        if self._thread is not None:
            self._commands.put(None)  # stop sentinel
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._driver is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort teardown
                self._driver.close()
            self._driver = None
        if self._tmpdir is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort cleanup
                self._tmpdir.cleanup()
            self._tmpdir = None


class FbpAcpAgent(Agent):
    """An ACP agent that runs every prompt through the FBP verified chain.

    One process owns one ``_DriverHost`` (built lazily, reused across sessions).
    Each prompt maps to a deterministic run / plan / network executed through
    ``driver.run`` / ``run_plan`` / ``run_network`` — every step a normal
    directive, parent re-verified, ledgered, replayable. Fail-closed: an
    unparseable command, an unknown task, or an unverified step is reported
    explicitly, never as a verified success.
    """

    def __init__(self) -> None:
        self._host = _DriverHost()
        self._conn: Client | None = None
        self._cancelled: set[str] = set()

    def close(self) -> None:
        """Teardown the driver host (and its temp stores)."""
        self._host.close()

    # -- ACP surface --------------------------------------------------------

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def _stream(self, session_id: str, text: str) -> None:
        """Emit a streamed agent text update to the client (best effort)."""
        conn = self._conn
        if conn is None:
            return
        chunk = update_agent_message(text_block(text))
        with contextlib.suppress(Exception):  # noqa: BLE001 - streaming is best-effort
            await conn.session_update(session_id=session_id, update=chunk)

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        # Honest minimal capabilities: the schema defaults already disable model
        # images/audio, MCP, terminal, and fs access. Advertise the ACP version
        # we implement so unsupported features are not used by the client.
        return InitializeResponse(
            protocol_version=_ACP_VERSION,
            agent_info=Implementation(
                name=_AGENT_NAME,
                title="Agent-centric (FBP deterministic verified chain)",
                version=_AGENT_VERSION,
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        return NewSessionResponse(session_id=uuid4().hex)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        return None

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        return ListSessionsResponse(sessions=[])

    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        return None

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        return None

    async def authenticate(
        self, method_id: str, **kwargs: Any
    ) -> AuthenticateResponse | None:
        return None

    async def prompt(
        self,
        session_id: str,
        prompt: list[_PromptBlock],
        **kwargs: Any,
    ) -> PromptResponse:
        text = " ".join(_block_text(block) for block in prompt).strip()
        if session_id in self._cancelled:
            await self._stream(session_id, "cancelled before start")
            return PromptResponse(stop_reason="cancelled")

        try:
            label, lines = await asyncio.to_thread(_run_command, self._host, text)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear failure
            await self._stream(session_id, f"fail-closed: {exc}")
            return PromptResponse(stop_reason="end_turn")

        await self._stream(session_id, f"running governed task: {label}")
        for line in lines:
            await self._stream(session_id, line)
        return PromptResponse(stop_reason="end_turn")

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        raise RequestError.method_not_found("session/fork")

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        return ResumeSessionResponse()

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        self._cancelled.discard(session_id)
        return None

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        # v1 tracks cancellation per session. The synchronous driver call is not
        # pre-emptible mid-run; a cancel arriving before the next prompt causes
        # it to be refused at start (documented limitation).
        self._cancelled.add(session_id)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._unsupported_extension()

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    @staticmethod
    async def _unsupported_extension() -> dict[str, Any]:
        raise RequestError.method_not_found("extension")


# -- command dispatch ---------------------------------------------------------


def _run_command(host: _DriverHost, text: str) -> tuple[str, list[str]]:
    """Dispatch a prompt to a deterministic FBP run; return (label, lines).

    Runs on the ACP caller's thread (via ``asyncio.to_thread``) and submits the
    actual driver call to the worker thread that owns the driver's loop. This
    keeps the driver's private loop isolated from the ACP loop. Fail-closed: an
    unknown command raises ``ValueError`` with a clear message.
    """
    parts = text.split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if command == "double":
        value = _parse_int(rest, "double")
        return _run_verified(host, "double", {"value": value}, f"double {value}")
    if command == "square":
        value = _parse_int(rest, "square")
        return _run_verified(host, "square", {"value": value}, f"square {value}")
    if command == "negate":
        value = _parse_int(rest, "negate")
        return _run_verified(host, "negate", {"value": value}, f"negate {value}")
    if command == "sum":
        a, b = _two_ints(rest, "sum")
        return _run_verified(host, "sum", {"a": a, "b": b}, f"sum {a} {b}")
    if command == "model":
        return _run_verified(
            host, "model", {"prompt": rest}, f"model reply for {rest!r}", child="model"
        )
    if command == "store":
        return _run_store(host, rest)
    if command == "bills":
        return _run_bills(host, rest)
    if command == "status":
        return _run_readonly(host, "status", "status")
    if command == "tree":
        return _run_readonly(host, "tree", "tree")
    if command == "network":
        return _run_network(host, rest)
    raise ValueError(
        f"unknown command {command!r}. Try: double/square/negate/sum <n>, "
        "model <text>, store set|get, bills intake|accept|calendar, "
        "status, tree, network <json>."
    )


def _two_ints(text: str, name: str) -> tuple[int, int]:
    """Split two whitespace-delimited integers, failing closed on garbage."""
    a_str, _, b_str = text.strip().partition(" ")
    if not a_str or not b_str:
        raise ValueError(f"{name} requires two integers, got {text!r}")
    return _parse_int(a_str, name), _parse_int(b_str, name)


def _run_verified(
    host: _DriverHost,
    task: str,
    args: dict[str, Any],
    label: str,
    *,
    child: str | None = None,
) -> tuple[str, list[str]]:
    """Run a single task through the verified chain and render the result."""

    def _fn(driver: FbpDriver) -> Any:
        return driver.run(task, args, child=child)

    resp = host.submit(_fn)
    if resp.verified:
        return label, [f"verified output: {_as_text(resp.value)}"]
    return label, [f"fail-closed: {resp.error or 'unverified'}"]


def _run_readonly(host: _DriverHost, kind: str, label: str) -> tuple[str, list[str]]:
    """Run a read-only inspection (status/tree) through the driver."""

    def _fn(driver: FbpDriver) -> Any:
        return driver.status() if kind == "status" else driver.tree()

    value = host.submit(_fn)
    return label, [_as_text(value)]


def _run_store(host: _DriverHost, rest: str) -> tuple[str, list[str]]:
    """Handle ``store set <key> <json>`` / ``store get <key>`` (grant-scoped)."""
    op, _, tail = rest.strip().partition(" ")
    if op == "set":
        key, _, val_text = tail.strip().partition(" ")
        if not key:
            raise ValueError("store set requires a key")
        value = _parse_json(val_text, "store value")
        return _run_verified(
            host, "store_set", {"key": key, "value": value}, f"store set {key}", child="store"
        )
    if op == "get":
        key = tail.strip()
        if not key:
            raise ValueError("store get requires a key")
        return _run_verified(host, "store_get", {"key": key}, f"store get {key}", child="store")
    raise ValueError("store requires 'set <key> <json>' or 'get <key>'")


def _run_bills(host: _DriverHost, rest: str) -> tuple[str, list[str]]:
    """Handle the bills demonstration loop (intake/accept/calendar)."""
    op, _, tail = rest.strip().partition(" ")
    if op == "intake":
        draft = _parse_json(tail, "bills intake draft")
        return _run_verified(host, "bills_intake", {"draft": draft}, "bills intake", child="bills")
    if op == "accept":
        draft = _parse_json(tail, "bills accept draft")
        return _run_verified(host, "bills_accept", {"draft": draft}, "bills accept", child="bills")
    if op == "calendar":
        from_date, _, to_date = tail.strip().partition(" ")
        if not from_date or not to_date:
            raise ValueError("bills calendar requires <from_date> <to_date>")
        return _run_verified(
            host, "bills_calendar", {"from_date": from_date, "to_date": to_date},
            f"bills calendar {from_date}..{to_date}", child="bills",
        )
    raise ValueError("bills requires 'intake <json>', 'accept <json>', or 'calendar <from> <to>'")


def _run_network(host: _DriverHost, rest: str) -> tuple[str, list[str]]:
    """Run a component network (JSON) as true dataflow through the chain."""
    data = _parse_json(rest, "network")
    network = network_from_dict(data)

    def _fn(driver: FbpDriver) -> Any:
        from agent_centric.fbp.network import run_network

        return run_network(driver, network)

    result = host.submit(_fn)
    if not result.get("ok"):
        return "network run", [f"fail-closed: {result.get('error') or 'unverified step'}"]
    lines = [f"network ok ({result['completed']} step(s))"]
    for r in result["results"]:
        lines.append(
            f"  {r['id']} ({r['task']}): verified={r['verified']} "
            f"value={_as_text(r['value'])}"
        )
    return "network run", lines


async def _serve_agent() -> None:
    """Run the ACP agent over stdio (blocking until the client disconnects)."""
    agent = FbpAcpAgent()
    try:
        await run_agent(cast(Agent, agent))
    finally:
        agent.close()


def main() -> int:
    """Console entry point for the ACP agent (``agent-centric-acp``)."""
    asyncio.run(_serve_agent())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())