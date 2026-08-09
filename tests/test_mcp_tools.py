"""Tests for the thin Manager-mediated MCP tool adapter (Volley 019).

These tests prove that MCP-backed tools reach agents only through the existing
ToolRegistry / Manager mediation path, and that MCP output is untrusted until it
passes the mandatory verification gate. They exercise, against an in-process
fake MCP server (no real network):

- an ungranted MCP tool cannot be used;
- a granted MCP tool round-trips and is durably recorded in the trajectory;
- policy can deny an MCP tool before any work runs;
- server/protocol/tool/timeout failures are audited and fail-closed.

All existing local-tool and control-plane invariants continue to hold (verified
by the full suite).
"""

from __future__ import annotations

import time

from meta_harness import AgentManager
from meta_harness.contracts.policy import Policy, PolicyVersion
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.mcp_tools import (
    LocalMcpServer,
    McpProtocolError,
    McpTimeoutError,
    McpToolAdapter,
    McpToolCallError,
    mcp_descriptor,
)
from meta_harness.control_plane.tools import ToolRegistry
from tests.fake_agent import MCP_TOOL_MANIFEST

_MCP_ECHO = mcp_descriptor(
    name="mcp_echo",
    description="Echo MCP tool.",
    input_schema={"text": "str"},
    output_schema="str",
)


def _mcp_echo(text: str) -> str:
    return f"mcp:{text}"


def _mcp_fail(**_kwargs: object) -> str:
    raise McpToolCallError("simulated MCP tool error")


def _mcp_slow(**_kwargs: object) -> str:
    time.sleep(0.5)
    return "too-late"


def _mcp_task(
    task_id: str,
    *,
    tool: str,
    args: dict[str, object],
    expected: str,
    granted: tuple[str, ...],
    policy: Policy | None = None,
) -> TaskSpecification:
    version = TaskSpecVersion.V5 if policy is not None else TaskSpecVersion.V3
    return TaskSpecification(
        version=version,
        task_id=task_id,
        agent_name="mcp_tool",
        payload={"tool": tool, "args": args, "expected": expected},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
        policy=policy,
    )


def _manager_with_mcp(*, adapter_timeout: float = 5.0) -> AgentManager:
    """An AgentManager whose ToolRegistry hosts tools from a fake MCP server."""
    server = LocalMcpServer()
    server.register_tool(_MCP_ECHO, _mcp_echo)
    server.register_tool(mcp_descriptor("mcp_fail", "Fails on call"), _mcp_fail)
    server.register_tool(mcp_descriptor("mcp_slow", "Runs long", input_schema={}), _mcp_slow)
    adapter = McpToolAdapter(server, timeout_seconds=adapter_timeout)
    registry = ToolRegistry()
    registered = registry.register_mcp(adapter)

    m = AgentManager(tools=registry)
    m.register(MCP_TOOL_MANIFEST)
    assert "mcp_echo" in registered
    return m


class TestMCPGrant:
    def test_ungranted_mcp_tool_cannot_be_used(self) -> None:
        """An MCP tool not in granted_tools is rejected fail-closed."""
        m = _manager_with_mcp()
        task = _mcp_task(
            "ungranted", tool="mcp_echo", args={"text": "hi"},
            expected="mcp:hi", granted=(),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        # The agent could not use the tool, so it returned UNVERIFIED and the
        # verifier rejected it: no verified success.
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED
        assert any(
            s.status.value == "rejected" for s in outcome.failure.trajectory.steps
        )

    def test_granted_mcp_tool_round_trips_and_is_recorded(self) -> None:
        """A granted MCP tool round-trips and is durably recorded."""
        m = _manager_with_mcp()
        task = _mcp_task(
            "granted", tool="mcp_echo", args={"text": "hi"},
            expected="mcp:hi", granted=("mcp_echo",),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "mcp:hi"

        descriptions = [s.description for s in outcome.result.trajectory.steps]
        assert any(d == "tool 'mcp_echo' request" for d in descriptions)
        assert any(d == "tool 'mcp_echo' result" for d in descriptions)
        # The verified output is the *expected* transformed value, not merely
        # any data the MCP call returned.
        assert outcome.result.output == "mcp:hi"


class TestMCPPolicy:
    def test_policy_can_deny_an_mcp_tool(self) -> None:
        """Policy denial of an MCP tool aborts before any work runs."""
        m = _manager_with_mcp()
        policy = Policy(
            version=PolicyVersion.V1,
            deny_tools=frozenset({"mcp_echo"}),
        )
        task = _mcp_task(
            "policy-deny", tool="mcp_echo", args={"text": "hi"},
            expected="mcp:hi", granted=("mcp_echo",), policy=policy,
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION
        descriptions = [s.description for s in outcome.failure.trajectory.steps]
        assert any(d == "policy rejected" for d in descriptions)
        # No MCP tool call occurred.
        assert not any("mcp_echo' request" in d for d in descriptions)


class TestMCPFailClosed:
    def test_server_and_tool_error_is_audited_fail_closed(self) -> None:
        """An MCP tool error is audited and cannot yield a verified success."""
        m = _manager_with_mcp()
        task = _mcp_task(
            "tool-error", tool="mcp_fail", args={},
            expected="ok", granted=("mcp_fail",),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED
        descriptions = [s.description for s in outcome.failure.trajectory.steps]
        assert any(d == "tool 'mcp_fail' execution failed" for d in descriptions)

    def test_mcp_timeout_is_audited_fail_closed(self) -> None:
        """A hung MCP server is bounded and cannot block the Manager."""
        m = _manager_with_mcp(adapter_timeout=0.05)
        task = _mcp_task(
            "timeout", tool="mcp_slow", args={},
            expected="ok", granted=("mcp_slow",),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED
        descriptions = [s.description for s in outcome.failure.trajectory.steps]
        assert any(d == "tool 'mcp_slow' execution failed" for d in descriptions)

    def test_unavailable_server_is_rejected_at_use(self) -> None:
        """An MCP server that becomes unavailable fails closed on use.

        Registration enumerates tools while the server is up; a later close makes
        any further call fail closed rather than returning unverified data.
        """
        server = LocalMcpServer()
        server.register_tool(_MCP_ECHO, _mcp_echo)
        adapter = McpToolAdapter(server, timeout_seconds=5.0)
        registry = ToolRegistry()
        registry.register_mcp(adapter)
        adapter.close()  # server becomes unavailable

        m = AgentManager(tools=registry)
        m.register(MCP_TOOL_MANIFEST)
        task = _mcp_task(
            "unavailable", tool="mcp_echo", args={"text": "hi"},
            expected="mcp:hi", granted=("mcp_echo",),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED
        descriptions = [s.description for s in outcome.failure.trajectory.steps]
        assert any(d == "tool 'mcp_echo' execution failed" for d in descriptions)


class TestMCPAdapterBoundary:
    def test_unknown_tool_raises_protocol_error(self) -> None:
        """Calling an unlisted tool on the server is a protocol error."""
        server = LocalMcpServer()
        server.register_tool(_MCP_ECHO, _mcp_echo)
        adapter = McpToolAdapter(server, timeout_seconds=5.0)
        try:
            adapter.call_tool("no_such_tool", {})
        except McpProtocolError as exc:
            assert "no tool named 'no_such_tool'" in str(exc)
        else:
            raise AssertionError("expected McpProtocolError")

    def test_adapter_timeout_raises(self) -> None:
        """A slow server call surfaces as an explicit McpTimeoutError."""
        server = LocalMcpServer()
        server.register_tool(mcp_descriptor("mcp_slow", "Runs long"), _mcp_slow)
        adapter = McpToolAdapter(server, timeout_seconds=0.05)
        try:
            adapter.call_tool("mcp_slow", {})
        except McpTimeoutError:
            pass
        else:
            raise AssertionError("expected McpTimeoutError")