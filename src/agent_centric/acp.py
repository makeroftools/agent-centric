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
from agent_centric.fbp.driverhost import FbpDriverHost
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

# Hard upper bound on the total size of a single prompt (across all content
# blocks). Mirrors the landing server's request-body bound so a client cannot
# feed an arbitrarily large string through the verified spine / into memory.
# Exceeding it fails closed with a clear message.
_MAX_PROMPT_CHARS = 1 << 16


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


def _parse_json(text: str, name: str) -> dict[str, Any]:
    """Parse a required JSON object field, failing closed on garbage."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be valid JSON, got {text!r}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


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
        self._host = FbpDriverHost(store_keys="acp-*")
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

    def _prompt_text(self, prompt: list[_PromptBlock]) -> str:
        """Concatenate the prompt's text blocks, bounded and fail-closed.

        Checks each block's contribution against the hard ``_MAX_PROMPT_CHARS``
        limit *before* building the joined string, so an oversized prompt is
        refused rather than allocated. Returns the trimmed, space-joined text.
        """
        total = 0
        parts: list[str] = []
        for block in prompt:
            text = _block_text(block)
            if not text:
                continue
            total += len(text)
            if total > _MAX_PROMPT_CHARS:
                raise ValueError(
                    f"prompt exceeds the {_MAX_PROMPT_CHARS}-char bound; "
                    "refusing (fail-closed)"
                )
            parts.append(text)
        return " ".join(parts).strip()

    async def prompt(
        self,
        session_id: str,
        prompt: list[_PromptBlock],
        **kwargs: Any,
    ) -> PromptResponse:
        try:
            text = self._prompt_text(prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear failure
            await self._stream(session_id, f"fail-closed: {exc}")
            return PromptResponse(stop_reason="end_turn")
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


def _run_command(host: FbpDriverHost, text: str) -> tuple[str, list[str]]:
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
    host: FbpDriverHost,
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


def _run_readonly(host: FbpDriverHost, kind: str, label: str) -> tuple[str, list[str]]:
    """Run a read-only inspection (status/tree) through the driver."""

    def _fn(driver: FbpDriver) -> Any:
        return driver.status() if kind == "status" else driver.tree()

    value = host.submit(_fn)
    return label, [_as_text(value)]


def _run_store(host: FbpDriverHost, rest: str) -> tuple[str, list[str]]:
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


def _run_bills(host: FbpDriverHost, rest: str) -> tuple[str, list[str]]:
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


def _run_network(host: FbpDriverHost, rest: str) -> tuple[str, list[str]]:
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