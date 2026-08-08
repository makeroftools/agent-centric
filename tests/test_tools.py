"""Tests for mediated tool access: grants, execution, recording, and bounds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.tools import ToolExecutionError, ToolRegistry
from meta_harness.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import CASE_TOOL_MANIFEST


def _case_task(
    task_id: str,
    text: str,
    *,
    granted: tuple[str, ...] = (),
    envelope: ResourceEnvelope | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=task_id,
        agent_name="case_tool",
        payload={"text": text},
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
    )


class TestToolRegistry:
    def test_execute_to_upper(self) -> None:
        reg = ToolRegistry()
        assert reg.execute("to_upper", {"text": "hello"}) == "HELLO"

    def test_execute_add(self) -> None:
        reg = ToolRegistry()
        assert reg.execute("add", {"a": 2, "b": 3}) == 5

    def test_unknown_tool_raises(self) -> None:
        reg = ToolRegistry()
        with pytest.raises(ToolExecutionError):
            reg.execute("nope", {})

    def test_descriptor_lookup(self) -> None:
        reg = ToolRegistry()
        desc = reg.descriptor("to_upper")
        assert desc is not None
        assert desc.name == "to_upper"
        assert desc.input_schema == {"text": "str"}
        assert reg.descriptor("missing") is None


class TestMediatedToolAccess:
    def test_granted_tool_succeeds_and_verifies(self) -> None:
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        outcome = m.run(_case_task("granted", "hello", granted=("to_upper",)))
        assert outcome.result is not None
        assert outcome.result.output == "HELLO"

    def test_ungranted_tool_cannot_be_used(self) -> None:
        """An agent cannot use a tool that was not granted."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        outcome = m.run(_case_task("ungranted", "hello", granted=()))
        # The agent cannot uppercase without the tool, so verification fails.
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_granting_a_different_tool_does_not_grant_to_upper(self) -> None:
        """Granting 'add' does not grant 'to_upper'."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        outcome = m.run(_case_task("wrong-grant", "hello", granted=("add",)))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_tool_interaction_appears_in_trajectory(self, tmp_path: Path) -> None:
        """Every tool request and result is a first-class, ordered step."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        outcome = m.run(_case_task("traj", "abc", granted=("to_upper",)))
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any("to_upper' request" in d for d in descriptions)
        assert any("to_upper' result" in d for d in descriptions)
        # The tool result step carries the output.
        result_steps = [s for s in stored.steps if "to_upper' result" in s.description]
        assert result_steps and result_steps[0].output == "ABC"

    def test_ungranted_tool_rejection_is_recorded(self, tmp_path: Path) -> None:
        """The Manager rejects and records an ungranted tool request."""
        from meta_harness.contracts.task import ResourceEnvelope as RE
        from tests.fake_agent import UNGUARDED_TOOL_AGENT_MANIFEST

        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(UNGUARDED_TOOL_AGENT_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="reject",
            agent_name="unguarded_tool",
            payload="abc",  # the unguarded agent passes this to to_upper
            envelope=RE(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),  # to_upper NOT granted
        )
        outcome = m.run(task)
        # The agent yields the rejected ToolResult then returns the passthrough,
        # which verifies, so the run succeeds with a recorded rejection step.
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        rejected = [s for s in stored.steps if s.status.value == "rejected"]
        assert rejected, "expected a REJECTED tool step in the trajectory"
        assert "not granted" in (rejected[0].error or "")

    def test_tool_calls_consume_step_budget(self) -> None:
        """Tool calls count against the task's step budget."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        # max_steps=1: the agent yields a validation step, then a tool request.
        # With only 1 step available, the task must hit the step limit before
        # completing (the tool interaction consumes additional steps).
        outcome = m.run(
            _case_task(
                "budget",
                "hello",
                granted=("to_upper",),
                envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_tool_failure_does_not_produce_unverified_success(self) -> None:
        """A tool that fails must not yield a verified success."""
        # Use an agent that requests a tool with malformed args. The case_tool
        # agent passes correct args, so we instead verify that a tool execution
        # failure is recorded and the final gate still applies. We simulate by
        # granting a tool but making the manager's registry fail via a stub.
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)

        # Monkeypatch the registry to fail on to_upper.
        original = m._tools.execute  # type: ignore[attr-defined]

        def failing_execute(name: str, args: dict[str, Any]) -> Any:
            raise ToolExecutionError("simulated tool failure")

        m._tools.execute = failing_execute  # type: ignore[attr-defined]
        outcome = m.run(_case_task("toolfail", "hello", granted=("to_upper",)))
        # Restore.
        m._tools.execute = original  # type: ignore[attr-defined]

        # The agent receives a failed ToolResult and returns the original text,
        # which fails verification -> explicit failure, not a success.
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_ungranted_tool_request_returns_failure_to_agent(self) -> None:
        """The Manager delivers a failed ToolResult for an ungranted request.

        The unguarded agent inspects the result it receives; we check the
        trajectory records that the request was rejected and that a failure with
        a clear message occurred.
        """
        from tests.fake_agent import UNGUARDED_TOOL_AGENT_MANIFEST

        m = AgentManager()
        m.register(UNGUARDED_TOOL_AGENT_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="reject-msg",
            agent_name="unguarded_tool",
            payload="abc",
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        rejected = [
            s for s in outcome.result.trajectory.steps if s.status.value == "rejected"
        ]
        assert rejected and "not granted" in (rejected[0].error or "")

    def test_final_verification_gate_applies_after_tool_use(self) -> None:
        """The verification gate still applies after a successful tool call."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        # Correct output passes.
        ok = m.run(_case_task("gate-ok", "hello", granted=("to_upper",)))
        assert ok.result is not None and ok.result.output == "HELLO"
        # A wrong output (simulated by a misbehaving agent) would fail the gate;
        # here we assert the gate function itself rejects a wrong output.
        from meta_harness.control_plane.verifier import verify_case_tool_output

        task = _case_task("gate", "hello", granted=("to_upper",))
        assert verify_case_tool_output(task, "hello").passed is False
        assert verify_case_tool_output(task, "HELLO").passed is True

    def test_deterministic_tools_replayable(self, tmp_path: Path) -> None:
        """Deterministic tools produce deterministic, replayable trajectories."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        first = m.run(_case_task("det", "hello", granted=("to_upper",)))
        second = m.run(_case_task("det", "hello", granted=("to_upper",)))
        assert first.result is not None and second.result is not None
        assert first.result.output == second.result.output == "HELLO"

        def sig(outcome) -> list[tuple[int, str, str, Any]]:
            return [
                (s.step_index, s.status.value, s.description, s.output)
                for s in outcome.result.trajectory.steps
            ]

        assert sig(first) == sig(second)
