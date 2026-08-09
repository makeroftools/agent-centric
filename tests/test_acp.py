"""Tests for the thin ACP adapter (Volley 021).

These tests prove the adapter is a client-facing transport only: every prompt is
routed through a governed ``AgentManager``, and no ACP path can produce a
verified success that bypasses the Manager. They drive the adapter over the
SDK's in-memory transport (``memory_transport_pair``) with raw JSON-RPC — no Zed,
no subprocess, no network is required in CI.

Covered:
- a prompt maps to a Manager run and the verified result is streamed back;
- a fail-closed outcome is reported explicitly, never as a verified success;
- cancellation marks the session so a subsequent prompt is refused;
- the Manager and demo agents are built once and reused across sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from acp._transport import memory_transport_pair
from acp.agent import AgentSideConnection

from meta_harness.acp import MetaHarnessAcpAgent
from meta_harness.contracts.result import Failure, FailureReason
from meta_harness.contracts.trajectory import Trajectory, TrajectoryVersion
from meta_harness.control_plane.manager import Outcome


class _FailingManager:
    """A minimal manager double that always returns a fail-closed Outcome."""

    def run(self, task: Any) -> Outcome:
        return Outcome(
            result=None,
            failure=Failure(
                task_id=task.task_id,
                reason=FailureReason.VERIFICATION_FAILED,
                message="simulated verification failure",
                trajectory=Trajectory(TrajectoryVersion.V1, task.task_id, task.agent_name or "x"),
            ),
            trajectory_id="t0",
        )


async def _drive(agent: MetaHarnessAcpAgent) -> tuple[Any, Any]:
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


async def _run_prompt_for_session(
    right: Any,
    listen_task: asyncio.Task,
    session_id: str,
    text: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    await _req(
        right, 3, "session/prompt",
        {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
    )
    return await _await_response_with_updates(right, 3)


async def _establish_session(agent: MetaHarnessAcpAgent) -> tuple[Any, asyncio.Task, str]:
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


def test_acp_prompt_returns_verified_output() -> None:
    """A prompt maps to a governed run and returns a verified result."""

    async def scenario() -> None:
        agent = MetaHarnessAcpAgent()
        right, listen_task, session_id = await _establish_session(agent)
        try:
            resp, streamed = await _run_prompt_for_session(
                right, listen_task, session_id, "reverse hello"
            )
            assert resp is not None
            assert resp["result"]["stopReason"] == "end_turn"
            # The reverse agent is verified; its output is streamed as text.
            assert any("verified output" in t for t in streamed), streamed
        finally:
            await _close(right, listen_task)

    asyncio.run(scenario())


def test_acp_prompt_uppercase_via_tool() -> None:
    """The ``upper`` path uses a mediated tool and returns a verified result."""

    async def scenario() -> None:
        agent = MetaHarnessAcpAgent()
        right, listen_task, session_id = await _establish_session(agent)
        try:
            resp, streamed = await _run_prompt_for_session(
                right, listen_task, session_id, "upper hi"
            )
            assert resp is not None
            assert resp["result"]["stopReason"] == "end_turn"
            assert any("verified output" in t and "HI" in t for t in streamed), streamed
        finally:
            await _close(right, listen_task)

    asyncio.run(scenario())


def test_acp_prompt_fail_closed_outcome_reported_explicitly() -> None:
    """A governed failure is reported explicitly, never as a verified success."""

    async def scenario() -> None:
        agent = MetaHarnessAcpAgent()
        # Inject a manager that fails closed; the adapter must report the failure
        # explicitly and never present it as a verified success.
        agent._manager = _FailingManager()  # type: ignore[assignment, attr-defined]
        right, listen_task, session_id = await _establish_session(agent)
        try:
            resp, streamed = await _run_prompt_for_session(
                right, listen_task, session_id, "reverse hello"
            )
            assert resp is not None
            assert resp["result"]["stopReason"] == "end_turn"
            assert any(t.startswith("fail-closed") for t in streamed), streamed
            # Never a verified success.
            assert not any("verified output" in t for t in streamed)
        finally:
            await _close(right, listen_task)

    asyncio.run(scenario())


def test_acp_cancelled_session_refuses_prompt() -> None:
    """A cancelled session refuses a prompt with stop_reason cancelled."""

    async def scenario() -> None:
        agent = MetaHarnessAcpAgent()
        right, listen_task = await _drive(agent)
        try:
            await _req(right, 1, "initialize", {"protocolVersion": 1})
            await _await_response(right, 1)
            # Simulate a prior session/cancel for this session id.
            agent._cancelled.add("sess-x")
            resp, streamed = await _run_prompt_for_session(right, listen_task, "sess-x", "hello")
            assert resp is not None
            assert resp["result"]["stopReason"] == "cancelled"
            assert any("cancelled before start" in t for t in streamed), streamed
        finally:
            await _close(right, listen_task)

    asyncio.run(scenario())


def test_acp_manager_is_shared_across_sessions() -> None:
    """The Manager and demo agents are built once (lazy) and reused."""
    a1 = MetaHarnessAcpAgent()
    m1 = a1._ensure_manager()
    assert a1._ensure_manager() is m1
    # The demo agents are registered and a deterministic run succeeds.
    import asyncio as _asyncio

    async def run() -> bool:
        from meta_harness.contracts.task import (
            ResourceEnvelope,
            TaskSpecification,
            TaskSpecVersion,
        )

        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="acp-mgr-check",
            agent_name="reverse",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m1.run(task)
        return outcome.result is not None and outcome.result.output == "cba"

    assert _asyncio.run(run())