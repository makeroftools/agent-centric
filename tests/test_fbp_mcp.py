"""Tests for the MCP adapter (operator runs them, per Law 12).

These tests prove the MCP adapter is an edge transport only: every tool call
routes through the FBP ``FbpDriver`` verified spine, and no MCP path can
produce a verified success that bypasses it. They drive the server over the
SDK's in-memory transport (``InMemoryTransport`` + ``ClientSession``) — no
network, no subprocess.

Covered:
- tools list / call route through the verified driver;
- arithmetic (double/square/negate/sum), model, store set/get;
- a fail-closed outcome is reported (is_error=True), never as a verified success;
- the shared worker-thread driver is reused and torn down.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from agent_centric.mcp import build_server


def _scenario(server: Any, name: str, args: dict[str, Any]) -> tuple[list[str], bool]:
    """Connect over the in-memory transport and call one tool; return (texts, is_error)."""

    async def scenario() -> tuple[list[str], bool]:
        async with InMemoryTransport(server) as (read_stream, write_stream), ClientSession(
            read_stream, write_stream
        ) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            texts = [
                c.text for c in (getattr(result, "content", None) or []) if getattr(c, "text", None)
            ]
            return texts, bool(getattr(result, "is_error", False))

    return asyncio.run(scenario())


async def _list_tools(server: Any) -> list[str]:
    async with InMemoryTransport(server) as (read_stream, write_stream), ClientSession(
        read_stream, write_stream
    ) as session:
        await session.initialize()
        tools = await session.list_tools()
        return [t.name for t in tools.tools]


def _build_server() -> Any:
    return build_server()


def test_mcp_tools_list_and_double() -> None:
    """Tools list and a verified arithmetic call route through the spine."""
    server = _build_server()
    try:
        names = asyncio.run(_list_tools(server))
        assert "double" in names and "sum" in names and "status" in names, names
        texts, is_error = _scenario(server, "double", {"value": 21})
        assert not is_error, texts
        assert any("verified: 42" in t for t in texts), texts
    finally:
        server._driver_host.close()


def test_mcp_sum_and_square() -> None:
    """sum + square return verified results."""
    server = _build_server()
    try:
        texts, is_error = _scenario(server, "sum", {"a": 2, "b": 3})
        assert not is_error and any("verified: 5" in t for t in texts), texts
        texts, is_error = _scenario(server, "square", {"value": 4})
        assert not is_error and any("verified: 16" in t for t in texts), texts
    finally:
        server._driver_host.close()


def test_mcp_store_set_get_roundtrip() -> None:
    """store_set then store_get round-trips through the shared store agent."""
    server = _build_server()
    try:
        texts, is_error = _scenario(server, "store_set", {"key": "mcp-k1", "value": {"x": 1}})
        assert not is_error, texts
        texts, is_error = _scenario(server, "store_get", {"key": "mcp-k1"})
        assert not is_error and any('"x": 1' in t for t in texts), texts
    finally:
        server._driver_host.close()


def test_mcp_model_stub(monkeypatch) -> None:
    """model uses the deterministic stub.

    Force the stub by clearing OpenRouter env (the shell may set a key), so the
    test is deterministic in any environment — the same convention the web-route
    tests use.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    server = _build_server()
    try:
        texts, is_error = _scenario(server, "model", {"prompt": "hi"})
        assert not is_error and any("stub response" in t for t in texts), texts
    finally:
        server._driver_host.close()


def test_mcp_fail_closed_unknown_tool() -> None:
    """An unknown tool is absent; an ungranted store key fails closed."""
    server = _build_server()
    try:
        names = asyncio.run(_list_tools(server))
        assert "does_not_exist" not in names, names
        texts, is_error = _scenario(server, "status", {})
        assert not is_error and any("summary" in t for t in texts), texts
        texts, is_error = _scenario(server, "store_get", {"key": "other-zz"})
        assert is_error, texts
        assert all("verified" not in t for t in texts), texts
    finally:
        server._driver_host.close()


def test_mcp_oversized_args_fail_closed() -> None:
    """A tool call with oversized args is refused, never fed through the spine."""
    from agent_centric.mcp import _MAX_CALL_BYTES

    server = _build_server()
    try:
        big = "x" * (_MAX_CALL_BYTES + 1)
        texts, is_error = _scenario(
            server, "store_set", {"key": "mcp-big", "value": {"data": big}}
        )
        assert is_error, texts
        assert any("exceed" in t and "fail-closed" in t for t in texts), texts
        assert all("verified:" not in t for t in texts), texts
    finally:
        server._driver_host.close()