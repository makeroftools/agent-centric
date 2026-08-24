"""Tests for the ACP adapter (FBP-backed, Volley 021).

These tests prove the adapter is a client-facing transport only: every prompt is
routed through the FBP ``FbpDriver`` verified spine, and no ACP path can produce
a verified success that bypasses it. They drive the adapter over the SDK's
in-memory transport (``memory_transport_pair``) with raw JSON-RPC — no Zed, no
subprocess, no network is required in CI.

Covered:
- a prompt maps to a governed FBP run and the verified result is streamed back;
- arithmetic (double/square/negate/sum), model, store, bills, status/tree, and
  component-network commands all route through the driver;
- a fail-closed outcome is reported explicitly, never as a verified success;
- cancellation marks the session so a subsequent prompt is refused;
- the driver is built once and reused across sessions, and torn down on close.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from acp._transport import memory_transport_pair
from acp.agent import AgentSideConnection

from agent_centric.acp import FbpAcpAgent


async def _drive(agent: FbpAcpAgent) -> tuple[Any, Any]:
    """Wire the agent to one end of an in-memory transport and start listening."""
    left, right = memory_transport_pair()
    conn = AgentSideConnection(agent, left, listening=False)
    listen_task = asyncio.create_task(conn.listen())
    return right, listen_task


async def _req(transport: Any, req_id: int, method: str, params: dict[str, Any]) -> None:
    await transport.send(
        {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    )


async def _await_response(transport: Any, req_id: int) -> dict[str, Any] | None:
    """Read until the response for ``req_id`` arrives (collecting notifications)."""
    while True:
        msg = await transport.receive()
        if msg is None:
            return None
        if msg.get("id") == req_id:
            return msg


async def _await_response_with_updates(
    transport: Any, req_id: int
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read until the response for ``req_id``, collecting streamed text updates."""
    streamed: list[str] = []
    while True:
        msg = await transport.receive()
        if msg is None:
            return None, streamed
        if msg.get("id") == req_id:
            return msg, streamed
        if msg.get("method") == "session/update":
            content = msg.get("params", {}).get("update", {}).get("content", {})
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                streamed.append(content["text"])


async def _close(right: Any, listen_task: asyncio.Task) -> None:
    listen_task.cancel()
    await right.close()
    with contextlib.suppress(asyncio.CancelledError):
        await listen_task


async def _establish_session(agent: FbpAcpAgent) -> tuple[Any, asyncio.Task, str]:
    """initialize + session/new, returning the transport, listen task, and session id."""
    right, listen_task = await _drive(agent)
    await _req(right, 1, "initialize", {"protocolVersion": 1})
    init_resp = await _await_response(right, 1)
    assert init_resp is not None
    assert init_resp["result"]["protocolVersion"] == 1
    await _req(right, 2, "session/new", {"cwd": "/tmp", "mcpServers": []})
    new_resp = await _await_response(right, 2)
    assert new_resp is not None
    session_id = new_resp["result"]["sessionId"]
    return right, listen_task, session_id


async def _run_prompt(
    right: Any, listen_task: asyncio.Task, session_id: str, text: str
) -> tuple[dict[str, Any] | None, list[str]]:
    await _req(
        right, 3, "session/prompt",
        {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
    )
    return await _await_response_with_updates(right, 3)


def _run_scenario(prompt_text: str) -> tuple[list[str], str]:
    """Run one prompt through a fresh agent; return (streamed lines, stop_reason)."""

    async def scenario() -> tuple[list[str], str]:
        agent = FbpAcpAgent()
        right, listen_task, session_id = await _establish_session(agent)
        try:
            resp, streamed = await _run_prompt(right, listen_task, session_id, prompt_text)
            assert resp is not None
            return streamed, resp["result"]["stopReason"]
        finally:
            await _close(right, listen_task)
            agent.close()

    return asyncio.run(scenario())


def _run_session_shared(*prompt_texts: str) -> list[tuple[str, str]]:
    """Run several prompts through one shared agent; return (streamed, stop) each.

    One ACP process owns one agent (and one driver/store), so multi-step flows
    (store set -> get, bills intake -> accept -> calendar) must reuse the same
    agent to observe persisted state across prompts. Each prompt still routes
    through the FBP verified spine.
    """

    async def scenario() -> list[tuple[str, str]]:
        agent = FbpAcpAgent()
        right, listen_task, session_id = await _establish_session(agent)
        try:
            results: list[tuple[str, str]] = []
            for text in prompt_texts:
                resp, streamed = await _run_prompt(right, listen_task, session_id, text)
                assert resp is not None
                results.append((streamed, resp["result"]["stopReason"]))
            return results
        finally:
            await _close(right, listen_task)
            agent.close()

    return asyncio.run(scenario())
    """The ``double`` command routes through the verified spine."""
    streamed, stop = _run_scenario("double 21")
    assert stop == "end_turn"
    assert any("verified output" in t and "42" in t for t in streamed), streamed


def test_acp_sum_verified() -> None:
    """The ``sum`` command routes through the verified spine."""
    streamed, stop = _run_scenario("sum 2 3")
    assert stop == "end_turn"
    assert any("verified output" in t and "5" in t for t in streamed), streamed


def test_acp_square_negate_verified() -> None:
    """``square`` and ``negate`` route through the verified spine."""
    s1, _ = _run_scenario("square 4")
    assert any("verified output" in t and "16" in t for t in s1), s1
    s2, _ = _run_scenario("negate 7")
    assert any("verified output" in t and "-7" in t for t in s2), s2


def test_acp_model_stub_verified() -> None:
    """The ``model`` command uses the deterministic stub by default."""
    streamed, stop = _run_scenario("model hello")
    assert stop == "end_turn"
    assert any("verified output" in t and "stub response" in t for t in streamed), streamed


def test_acp_store_set_get() -> None:
    """``store set`` then ``store get`` round-trips through one store agent."""
    (s1, _), (s2, _) = _run_session_shared(
        'store set acp-k1 {"due": "2026-10-01"}', "store get acp-k1"
    )
    assert any("verified output" in t for t in s1), s1
    assert any("verified output" in t and "2026-10-01" in t for t in s2), s2


def test_acp_store_ungranted_fails_closed() -> None:
    """A key outside the store grant fails closed, never a verified success."""
    streamed, _ = _run_scenario("store get acp-zz")
    assert any(t.startswith("fail-closed") for t in streamed), streamed
    assert not any("verified output" in t for t in streamed)


def test_acp_bills_loop() -> None:
    """The bills loop persists through one agent (intake/accept/calendar)."""
    draft = '{"id": "bill-a1", "vendor": "GasCo", "amount_cents": 12345, "due_date": "2026-10-01"}'
    (s1, _), (s2, _), (s3, _) = _run_session_shared(
        f"bills intake {draft}",
        f"bills accept {draft}",
        "bills calendar 2026-10-01 2026-10-31",
    )
    assert any("verified output" in t for t in s1), s1
    assert any("verified output" in t for t in s2), s2
    assert any("verified output" in t and "12345" in t for t in s3), s3


def test_acp_status_and_tree() -> None:
    """``status`` and ``tree`` return read-only operator snapshots."""
    s1, _ = _run_scenario("status")
    assert any("store" in t and "model" in t for t in s1), s1
    s2, _ = _run_scenario("tree")
    assert any("store" in t and "model" in t for t in s2), s2


def test_acp_network_dataflow() -> None:
    """A component network runs as true dataflow through the spine."""
    net = (
        '{"components": ['
        '{"id": "a", "task": "double", "args": {"value": 3}},'
        '{"id": "b", "task": "double", "args": {"value": 5}},'
        '{"id": "c", "task": "sum", "args": {}}],'
        '"edges": ['
        '{"source": "a", "source_field": "value", "target": "c", "target_arg": "a"},'
        '{"source": "b", "source_field": "value", "target": "c", "target_arg": "b"}]}'
    )
    streamed, _ = _run_scenario(f"network {net}")
    assert any("network ok (3 step(s))" in t for t in streamed), streamed
    assert any("c (sum)" in t and "16" in t for t in streamed), streamed


def test_acp_unknown_command_fails_closed() -> None:
    """An unknown command is reported explicitly, never as a verified success."""
    streamed, _ = _run_scenario("frobnicate 1")
    assert any(t.startswith("fail-closed") for t in streamed), streamed
    assert not any("verified output" in t for t in streamed)


def test_acp_cancelled_session_refuses_prompt() -> None:
    """A cancelled session refuses a prompt with stop_reason cancelled."""

    async def scenario() -> None:
        agent = FbpAcpAgent()
        right, listen_task = await _drive(agent)
        try:
            await _req(right, 1, "initialize", {"protocolVersion": 1})
            await _await_response(right, 1)
            agent._cancelled.add("sess-x")
            resp, streamed = await _run_prompt(right, listen_task, "sess-x", "double 2")
            assert resp is not None
            assert resp["result"]["stopReason"] == "cancelled"
            assert any("cancelled before start" in t for t in streamed), streamed
        finally:
            await _close(right, listen_task)
            agent.close()

    asyncio.run(scenario())


def test_acp_driver_is_shared_and_closed() -> None:
    """The driver host is built once (lazy) and torn down on close."""
    a1 = FbpAcpAgent()
    h1 = a1._host
    assert a1._host is h1
    a1.close()
    assert a1._host._driver is None