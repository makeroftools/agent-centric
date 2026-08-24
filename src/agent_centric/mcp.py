"""MCP adapter: expose Agent-centric FBP as Model Context Protocol tools.

This module bridges the **Model Context Protocol (MCP)** — the tool-calling transport
used by LLM hosts (Claude Desktop, agent runtimes, etc.) — to the **FBP subsystem**
(the deterministic platform that is now the real architecture). MCP is an edge transport
only: the ``FbpDriver`` remains the sole authority for policy, verification, and audit.
No MCP path can produce a verified success that bypasses the FBP correctness chain (every
``run`` is a normal directive, parent re-verified, ledgered, replayable).

The server exposes the deterministic FBP capabilities as **tools** an external model can
call — the "model proposes, the deterministic tool verifies" seam. Tools:

- ``double`` / ``square`` / ``negate`` / ``sum`` — verified arithmetic.
- ``model`` — the ``model`` agent (deterministic stub; real OpenRouter wired when
  ``OPENROUTER_API_KEY`` is set).
- ``store_set`` / ``store_get`` — mediated single-writer store, grant-scoped.
- ``bills_intake`` / ``bills_accept`` / ``bills_calendar`` — the bills demonstration loop
  (human-gated accept).
- ``status`` / ``tree`` — read-only operator inspection.
- ``network`` — run a component network as true dataflow.

An unhandled tool / a raised error is reported honestly (``is_error=True``), never as a
verified success. Use the ``agent-centric-mcp`` console entry point (or ``python -m
agent_centric.mcp``) and point an MCP client at it over stdio.

**Threading.** The ``FbpDriver`` binds a private asyncio event loop and drives it with
``run_until_complete``. That loop must never be the MCP loop (which is already running
asyncio), so the driver is hosted on a **dedicated worker thread**: the driver's loop is bound
to that thread and every tool call is submitted to the worker and awaited by the caller.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent

from agent_centric.fbp import FbpDriver
from agent_centric.fbp.driverhost import FbpDriverHost


def _as_text(value: Any) -> str:
    """Render a verified output as plain text for MCP."""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, sort_keys=True)


# Hard upper bound on the serialized size of a single tool-call argument object.
# An LLM host drives this surface, so it must not be able to feed an
# arbitrarily large payload (e.g. an outsized ``store_set`` / ``bills_intake`` /
# ``model`` prompt) through the verified spine or into memory. Mirrors the ACP
# prompt bound and the landing server's request-body bound. Fail-closed.
_MAX_CALL_BYTES = 1 << 16


def _call_oversized(args: dict[str, Any]) -> bool:
    """True if the serialized tool args exceed the hard size bound (fail-closed)."""
    try:
        return len(json.dumps(args, default=str, sort_keys=True)) > _MAX_CALL_BYTES
    except (TypeError, ValueError):
        # Unserializable args are inherently untrustworthy; fail closed.
        return True


def _call_unless_result(
    host: FbpDriverHost,
    task: str,
    args: dict[str, Any],
    *,
    child: str | None = None,
) -> CallToolResult:
    """Run one FBP task through the verified spine; return a CallToolResult."""

    if _call_oversized(args):
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        "fail-closed: tool args exceed the "
                        f"{_MAX_CALL_BYTES}-byte bound; refusing"
                    ),
                )
            ],
            is_error=True,
        )

    def _fn(driver: FbpDriver) -> Any:
        return driver.run(task, args, child=child)

    try:
        resp = host.submit(_fn)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clear failure
        return CallToolResult(
            content=[TextContent(type="text", text=f"fail-closed: {exc}")],
            is_error=True,
        )
    if resp.verified:
        return CallToolResult(
            content=[TextContent(type="text", text=f"verified: {_as_text(resp.value)}")],
            is_error=False,
        )
    return CallToolResult(
        content=[TextContent(type="text", text=f"fail-closed: {resp.error or 'unverified'}")],
        is_error=True,
    )


def _readonly_result(host: FbpDriverHost, kind: str) -> CallToolResult:
    """Run a read-only driver method (status/tree); never a task run."""

    def _fn(driver: FbpDriver) -> Any:
        return driver.status() if kind == "status" else driver.tree()

    try:
        value = host.submit(_fn)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clear failure
        return CallToolResult(
            content=[TextContent(type="text", text=f"fail-closed: {exc}")],
            is_error=True,
        )
    return CallToolResult(
        content=[TextContent(type="text", text=_as_text(value))],
        is_error=False,
    )


def build_server(*, store_prefix: str = "mcp-*") -> MCPServer[Any]:
    """Build the MCP server exposing FBP capabilities as tools.

    One process owns one ``FbpDriverHost`` (built lazily, reused) on a dedicated
    worker thread. Each tool maps to a deterministic ``run`` through the verified
    driver spine. ``store_prefix`` scopes the store keys the store child may serve.
    """
    host = FbpDriverHost(store_keys=store_prefix)

    def _verify_run(task: str, args: dict[str, Any], *, child: str | None = None) -> CallToolResult:
        return _call_unless_result(host, task, args, child=child)

    server: MCPServer[Any] = MCPServer(
        name="agent-centric",
        title="Agent-centric (FBP deterministic verified) — MCP tools",
        description="Expose the deterministic FBP platform as MCP tools for LLM hosts.",
        version="0.1.0",
        instructions=(
            "These tools execute through a verified, deterministic spine. A verified "
            "result is trustworthy; an unverified one is reported honestly (is_error). "
            "Unknown/ungranted operations fail closed. Nothing you can call bypasses "
            "verification."
        ),
    )

    # Arithmetic
    @server.tool(name="double", title="Verified double", description="value * 2")
    def double(value: int) -> CallToolResult:
        return _verify_run("double", {"value": value})

    @server.tool(name="square", title="Verified square", description="value * value")
    def square(value: int) -> CallToolResult:
        return _verify_run("square", {"value": value})

    @server.tool(name="negate", title="Verified negate", description="-value")
    def negate(value: int) -> CallToolResult:
        return _verify_run("negate", {"value": value})

    @server.tool(name="sum", title="Verified sum", description="a + b")
    def sum_(a: int, b: int) -> CallToolResult:
        return _verify_run("sum", {"a": a, "b": b})

    # Model (ordinary agent; stub/opt-in provider)
    @server.tool(
        name="model", title="Model agent", description="Run a prompt through the model agent."
    )
    def model(prompt: str) -> CallToolResult:
        return _verify_run("model", {"prompt": prompt}, child="model")

    # Store (grant-scoped)
    @server.tool(name="store_set", title="Store set", description="Store a JSON value under a key.")
    def store_set(key: str, value: dict[str, Any]) -> CallToolResult:
        return _verify_run("store_set", {"key": key, "value": value}, child="store")

    @server.tool(name="store_get", title="Store get", description="Read a stored value by key.")
    def store_get(key: str) -> CallToolResult:
        return _verify_run("store_get", {"key": key}, child="store")

    # Bills demonstration loop
    @server.tool(
        name="bills_intake",
        title="Bills intake",
        description="Intake a bill draft (unverified).",
    )
    def bills_intake(draft: dict[str, Any]) -> CallToolResult:
        return _verify_run("bills_intake", {"draft": draft}, child="bills")

    @server.tool(
        name="bills_accept",
        title="Bills accept",
        description="Human-gated accept a bill.",
    )
    def bills_accept(draft: dict[str, Any]) -> CallToolResult:
        return _verify_run("bills_accept", {"draft": draft}, child="bills")

    @server.tool(
        name="bills_calendar",
        title="Bills calendar",
        description="Verified bills calendar.",
    )
    def bills_calendar(from_date: str, to_date: str) -> CallToolResult:
        return _verify_run(
            "bills_calendar", {"from_date": from_date, "to_date": to_date}, child="bills"
        )

    # Read-only inspection
    @server.tool(name="status", title="Status", description="Read-only tree + summary.")
    def status() -> CallToolResult:
        return _readonly_result(host, "status")

    @server.tool(name="tree", title="Tree", description="Read-only tree snapshot.")
    def tree() -> CallToolResult:
        return _readonly_result(host, "tree")

    # Component network (true dataflow)
    @server.tool(
        name="network",
        title="Network",
        description="Run a component network as true dataflow.",
    )
    def network(network_json: dict[str, Any]) -> CallToolResult:
        from agent_centric.fbp.network import network_from_dict, run_network

        try:
            net = network_from_dict(network_json)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear failure
            return CallToolResult(
                content=[TextContent(type="text", text=f"fail-closed: {exc}")],
                is_error=True,
            )

        def _fn(driver: FbpDriver) -> Any:
            return run_network(driver, net)

        try:
            result = host.submit(_fn)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear failure
            return CallToolResult(
                content=[TextContent(type="text", text=f"fail-closed: {exc}")],
                is_error=True,
            )
        if not result.get("ok"):
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"fail-closed: {result.get('error') or 'unverified step'}",
                    )
                ],
                is_error=True,
            )
        lines = [f"network ok ({result['completed']} step(s))"]
        for r in result["results"]:
            lines.append(
                f"  {r['id']} ({r['task']}): verified={r['verified']} "
                f"value={_as_text(r['value'])}"
            )
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))], is_error=False
        )

    # Attach the host for teardown (private-but-reachable; additive).
    server._driver_host = host  # type: ignore[attr-defined]
    return server


async def _serve() -> None:
    """Run the MCP server over stdio (blocking until the client disconnects)."""
    server = build_server()
    try:
        await server.run_stdio_async()
    finally:
        host = getattr(server, "_driver_host", None)
        if host is not None:
            host.close()


def main() -> int:
    """Console entry point for the MCP server (``agent-centric-mcp``)."""
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())