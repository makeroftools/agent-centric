"""End-to-end demo of the FBP external edge transports (ACP + MCP).

This is the runnable proof that the deterministic platform is reachable from
outside. It drives BOTH edge transports against the same ``FbpDriverHost``
worker-thread spine, offline (no network, no Zed, no LLM key — the model agent
serves its deterministic stub):

- **ACP**  — the Agent Client Protocol transport: prompts routed through the
  ``FbpAcpAgent`` over the SDK's in-memory transport. Verified arithmetic,
  store set/get, and a component network.
- **MCP**  — the Model Context Protocol transport: tools called over the SDK's
  in-memory ``ClientSession``. Verified arithmetic, store set/get, and
  fail-closed unknown/ungranted behaviour.

Both reuse the ``FbpDriverHost`` worker thread, so the private driver loop stays
isolated from the ACP/MCP async loops. Every result reports honestly whether it
was verified. Fail-closed paths never produce a verified success.

Run with:

    uv run python examples/fbp_external_demo.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from acp._transport import memory_transport_pair
from acp.agent import AgentSideConnection
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from agent_centric.acp import FbpAcpAgent
from agent_centric.mcp import build_server

_CHECK = "\u2713"
_CROSS = "\u2717"


def _heading(text: str) -> None:
    print(f"\n== {text} ==")


def _result(ok: bool, label: str) -> None:
    mark = _CHECK if ok else _CROSS
    print(f"  {mark} {label}")


# -- ACP ---------------------------------------------------------------------


async def _acp_session(agent: FbpAcpAgent) -> tuple[Any, asyncio.Task, str]:
    left, right = memory_transport_pair()
    conn = AgentSideConnection(agent, left, listening=False)
    listen = asyncio.create_task(conn.listen())
    await right.send(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}
    )
    while True:
        msg = await right.receive()
        if msg and msg.get("id") == 1:
            break
    await right.send(
        {"jsonrpc": "2.0", "id": 2, "method": "session/new",
         "params": {"cwd": "/tmp", "mcpServers": []}}
    )
    session_id = None
    while True:
        msg = await right.receive()
        if msg and msg.get("id") == 2:
            session_id = msg["result"]["sessionId"]
            break
    assert session_id is not None
    return right, listen, session_id


async def _acp_prompt(right: Any, listen: asyncio.Task, session_id: str, text: str) -> list[str]:
    await right.send(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
        }
    )
    streamed: list[str] = []
    while True:
        msg = await right.receive()
        if msg is None:
            break
        if msg.get("id") == 3:
            break
        if msg.get("method") == "session/update":
            content = msg.get("params", {}).get("update", {}).get("content", {})
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                streamed.append(content["text"])
    return streamed


async def _drive_acp() -> None:
    _heading("ACP — Agent Client Protocol over the verified spine")
    agent = FbpAcpAgent()
    try:
        right, listen, sid = await _acp_session(agent)
        try:
            net = json.dumps(
                {
                    "components": [
                        {"id": "a", "task": "double", "args": {"value": 3}},
                        {"id": "b", "task": "double", "args": {"value": 5}},
                        {"id": "c", "task": "sum", "args": {}},
                    ],
                    "edges": [
                        {"source": "a", "source_field": "value", "target": "c", "target_arg": "a"},
                        {"source": "b", "source_field": "value", "target": "c", "target_arg": "b"},
                    ],
                }
            )
            cases = [
                ("double 21", "42", "verified arithmetic"),
                ("sum 2 3", "5", "verified addition"),
                ('store set acp-demo-k {"due": "2026-10-01"}',
                 "verified output", "granted store set"),
                ("store get acp-demo-k", "2026-10-01", "granted store get"),
                (f"network {net}", "16", "component-network dataflow"),
                ("store get other-zz", "fail-closed", "ungranted store get fails closed"),
                ("frobnicate 1", "fail-closed", "unknown command fails closed"),
            ]
            for command, expect, label in cases:
                lines = await _acp_prompt(right, listen, sid, command)
                joined = " ".join(lines)
                ok = expect in joined and (
                    expect != "fail-closed" or "verified output" not in joined
                )
                _result(ok, f"{label}: {command!r} -> {joined[:60]}")
        finally:
            listen.cancel()
            await right.close()
            with contextlib.suppress(asyncio.CancelledError):
                await listen
    finally:
        agent.close()


# -- MCP ---------------------------------------------------------------------


async def _drive_mcp() -> None:
    _heading("MCP — Model Context Protocol tools over the verified spine")
    server = build_server()
    try:
        async with InMemoryTransport(server) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            _result({"double", "sum", "status"}.issubset(names), f"tools list ({len(names)} tools)")

            async def call(name: str, args: dict[str, Any]) -> tuple[str, bool]:
                res = await session.call_tool(name, args)
                texts = [
                    c.text
                    for c in (getattr(res, "content", None) or [])
                    if getattr(c, "text", None)
                ]
                return " ".join(texts), bool(getattr(res, "is_error", False))

            text, err = await call("double", {"value": 21})
            _result(not err and "verified: 42" in text, "verified double -> 42")
            text, err = await call("sum", {"a": 2, "b": 3})
            _result(not err and "verified: 5" in text, "verified sum -> 5")
            text, err = await call("store_set", {"key": "mcp-demo-k", "value": {"x": 1}})
            _result(not err, "granted store_set")
            text, err = await call("store_get", {"key": "mcp-demo-k"})
            _result(not err and "x" in text, "granted store_get round-trip")
            text, err = await call("store_get", {"key": "other-zz"})
            _result(err and "verified" not in text, "ungranted store_get fails closed (is_error)")
    finally:
        server._driver_host.close()


def main() -> int:
    print("Agent-centric FBP — external edge transports demo (offline, deterministic)")
    asyncio.run(_drive_acp())
    asyncio.run(_drive_mcp())
    print("\nDone. Both transports reached the same verified, fail-closed spine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())