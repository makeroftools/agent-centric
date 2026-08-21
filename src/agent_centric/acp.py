"""Thin ACP adapter: expose Agent-centric as an External Agent in Zed (Volley 021).

This module bridges the Agent Client Protocol (ACP) — a client-facing transport
used by editors such as Zed — to the governed ``AgentManager``. ACP is an edge
transport only: the Agent Manager remains the sole authority for policy, tool
mediation, envelopes, verification, and audit. No ACP path can produce a
verified success that bypasses the Manager.

The adapter implements the smallest usable ACP surface:

- ``initialize`` — honest, minimal capabilities (all disabled by default).
- ``session/new`` — a session id is minted; one process owns one Manager.
- ``session/prompt`` — a user prompt is mapped to a ``TaskSpecification``, run
  through ``AgentManager.run(...)``, and the verified result (or a clear
  fail-closed failure message) is streamed back as agent text.
- ``session/cancel`` — tracked per session. Mid-run cancellation of the
  synchronous ``manager.run`` is not pre-emptible in v1; this is documented.

Only the demo agents and the deterministic stub model provider are used, so
trajectories are replayable and no network or credentials are required. Use the
``agent-centric-acp`` console entry point (or ``python -m agent_centric.acp``) and
point an ACP client at it over stdio.
"""

from __future__ import annotations

import asyncio
import contextlib
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

from agent_centric import AgentManager, StubModelProvider
from agent_centric.contracts.capability import Capability
from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.tools import ToolRegistry

# The ACP protocol version this adapter implements and advertises.
_ACP_VERSION = PROTOCOL_VERSION

# Default task envelope for ACP-driven runs (generous but bounded).
_DEFAULT_ENVELOPE = ResourceEnvelope(timeout_seconds=30.0, max_steps=200)

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


_COUNTER_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="counter",
    entry_point="agent_centric.agents.counter:create_counter_agent",
    description="Counts occurrences of a target character in a string.",
    declared_capabilities=frozenset({Capability(name="count", version="1")}),
)

_REVERSE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="reverse",
    entry_point="agent_centric.agents.reverse:create_reverse_agent",
    description="Reverses a string.",
    declared_capabilities=frozenset({Capability(name="reverse", version="1")}),
)

_CASE_TOOL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="case_tool",
    entry_point="agent_centric.agents.case_tool:create_case_tool_agent",
    description="Uppercases a string via a mediated tool.",
    declared_capabilities=frozenset(),
)

_MODEL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="model",
    entry_point="agent_centric.agents.model_agent:create_model_agent",
    description="Answers a constrained prompt via a mediated language model.",
    declared_capabilities=frozenset({Capability(name="llm", version="1")}),
)

_DEMO_MANIFESTS = (
    _COUNTER_MANIFEST,
    _REVERSE_MANIFEST,
    _CASE_TOOL_MANIFEST,
    _MODEL_MANIFEST,
)


def _block_text(block: Any) -> str:
    """Extract plain text from an ACP content block (or a raw dict)."""
    if isinstance(block, dict):
        text = block.get("text")
        return text if isinstance(text, str) else ""
    return getattr(block, "text", "") if isinstance(block, TextContentBlock) else ""


def _build_manager() -> AgentManager:
    """Build a Manager owning the deterministic demo agents.

    The stub model provider makes the ``model`` path deterministic and replayable
    without any network access or credentials.
    """
    manager = AgentManager(tools=ToolRegistry(model_provider=StubModelProvider()))
    for manifest in _DEMO_MANIFESTS:
        manager.register(manifest)
    return manager


def _acp_task_from_prompt(prompt: str) -> tuple[TaskSpecification, str]:
    """Map a user prompt to a deterministic demo ``TaskSpecification``.

    The first whitespace-delimited token selects the agent path:

    - ``upper <text>``        -> uppercase via the mediated ``to_upper`` tool.
    - ``model <text>``        -> stub model (deterministic ``stub response``).
    - ``counter <text> <ch>`` -> count occurrences of a single character.
    - anything else            -> reverse the text (the default).

    Returns ``(task, label)`` where ``label`` is a short human summary used in
    the streamed update.
    """
    parts = prompt.strip().split(maxsplit=2)
    command = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if command == "upper":
        return TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id=f"acp-upper-{uuid4().hex[:8]}",
            agent_name="case_tool",
            payload={"text": rest or " "},
            envelope=_DEFAULT_ENVELOPE,
            granted_tools=("to_upper",),
        ), f"uppercased {rest!r}"
    if command == "model":
        return TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id=f"acp-model-{uuid4().hex[:8]}",
            agent_name="model",
            payload={"prompt": rest or "", "expected": "stub response"},
            envelope=_DEFAULT_ENVELOPE,
            granted_tools=("llm_complete",),
        ), f"model reply for {rest!r}"
    if command == "counter":
        target = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "o"
        if len(target) != 1:
            target = target[0]
        return TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id=f"acp-counter-{uuid4().hex[:8]}",
            agent_name="counter",
            payload={"text": rest, "target": target},
            envelope=_DEFAULT_ENVELOPE,
        ), f"count of {target!r} in {rest!r}"
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=f"acp-reverse-{uuid4().hex[:8]}",
        agent_name="reverse",
        payload={"text": prompt.strip()},
        envelope=_DEFAULT_ENVELOPE,
    ), f"reversed {prompt.strip()!r}"


def _as_text(value: Any) -> str:
    """Render a verified output as plain text for the ACP stream."""
    if isinstance(value, str):
        return value
    return str(value)


class MetaHarnessAcpAgent(Agent):
    """An ACP agent that runs every prompt through a governed AgentManager.

    One process owns one Manager (built once, reused across sessions). Sessions
    are tracked for cancellation state; each prompt produces its own governed
    run with a durable trajectory.
    """

    def __init__(self) -> None:
        self._manager: AgentManager | None = None
        self._conn: Client | None = None
        self._cancelled: set[str] = set()

    def _ensure_manager(self) -> AgentManager:
        if self._manager is None:
            self._manager = _build_manager()
        return self._manager

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
                title="Agent-centric (governed deterministic harness)",
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

        manager = self._ensure_manager()
        try:
            task, label = _acp_task_from_prompt(prompt=text)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear failure
            await self._stream(session_id, f"could not build a task from the prompt: {exc}")
            return PromptResponse(stop_reason="end_turn")

        await self._stream(session_id, f"running governed task: {label}")

        # Fail-closed: manager.run never raises for agent/tool/verification
        # failures; it returns a sealed Outcome. Any failure is reported
        # explicitly, never as a verified success.
        outcome = manager.run(task)
        if outcome.result is not None:
            text_out = f"verified output: {_as_text(outcome.result.output)}"
            if outcome.trajectory_id is not None:
                text_out += f"  (trajectory {outcome.trajectory_id})"
        else:
            assert outcome.failure is not None
            text_out = (
                f"fail-closed: {outcome.failure.reason.value} — "
                f"{outcome.failure.message}"
            )
        await self._stream(session_id, text_out)
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
        # v1 tracks cancellation per session. The synchronous ``manager.run`` is
        # not pre-emptible mid-run; a cancel arriving before the next prompt
        # causes it to be refused at start (documented limitation).
        self._cancelled.add(session_id)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._unsupported_extension()

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    @staticmethod
    async def _unsupported_extension() -> dict[str, Any]:
        raise RequestError.method_not_found("extension")


async def _serve_agent() -> None:
    """Run the ACP agent over stdio (blocking until the client disconnects)."""
    await run_agent(cast(Agent, MetaHarnessAcpAgent()))


def main() -> int:
    """Console entry point for the ACP agent (``agent-centric-acp``)."""
    asyncio.run(_serve_agent())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())