"""Tests for cooperative cancellation & envelope exhaustion (Volley 009).

These tests prove that when a stage or composition envelope (steps or time) is
exhausted, the Manager cooperatively cancels the running agent, records a
distinct, auditable cancellation in the durable trajectory, and never returns
an unverified success. Prior invariants (verification gate, policy, hand-off,
accounting, single coherent trajectory) remain intact.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import CASE_TOOL_MANIFEST, REVERSE_MANIFEST
from tests.fake_agent import (
    COOPERATIVE_CANCEL_MANIFEST,
    IGNORING_CANCEL_MANIFEST,
    SLEEPY_AGENT_MANIFEST,
    SLOW_COOPERATIVE_CANCEL_MANIFEST,
)


def _task(*, agent_name: str, envelope: ResourceEnvelope, task_id: str = "t") -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=task_id,
        agent_name=agent_name,
        payload={"text": "abc"},
        envelope=envelope,
    )


def _pipeline_task(
    task_id: str,
    stages: tuple[StageSpec, ...],
    envelope: ResourceEnvelope | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V4,
        task_id=task_id,
        payload={"text": "abc"},
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        pipeline=SequentialComposition(version=PipelineVersion.V3, stages=stages),
    )


class TestEnvelopeExhaustionCancelsCooperatively:
    def test_step_limit_cancels_cooperative_agent(self, tmp_path: Path) -> None:
        """Step-limit exhaustion cancels the agent and records it durably."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        task = _task(
            agent_name="cooperative_cancel",
            task_id="step-cancel",
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=2),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

        # Cancellation appears explicitly in the durable trajectory.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(s.status.value == "cancelled" for s in stored.steps)
        assert any(d == "agent cancelled" for d in descriptions)
        # The agent cooperated and stopped: no verified success is returned even
        # though it exited cleanly after observing the signal.
        assert outcome.result is None

    def test_timeout_cancels_cooperative_agent(self, tmp_path: Path) -> None:
        """Timeout exhaustion cancels the agent and records it durably."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(SLOW_COOPERATIVE_CANCEL_MANIFEST)
        task = _task(
            agent_name="slow_cooperative_cancel",
            task_id="timeout-cancel",
            envelope=ResourceEnvelope(timeout_seconds=0.05, max_steps=1000),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.TIMEOUT

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(s.status.value == "cancelled" for s in stored.steps)
        assert any(d == "agent cancelled" for d in descriptions)

    def test_non_cooperative_agent_still_cancelled_fail_closed(self, tmp_path: Path) -> None:
        """An agent that ignores cancellation is still terminated fail-closed."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(IGNORING_CANCEL_MANIFEST)
        task = _task(
            agent_name="ignoring_cancel",
            task_id="ignore-cancel",
            envelope=ResourceEnvelope(timeout_seconds=0.05, max_steps=1000),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.TIMEOUT

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        assert any(s.status.value == "cancelled" for s in stored.steps)

    def test_cancellation_never_yields_unverified_success(self) -> None:
        """A cooperative agent that produces output after cancel still fails."""
        m = AgentManager()
        m.register(SLOW_COOPERATIVE_CANCEL_MANIFEST)
        task = _task(
            agent_name="slow_cooperative_cancel",
            task_id="no-success",
            envelope=ResourceEnvelope(timeout_seconds=0.05, max_steps=1000),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        # The output the agent produced upon cancellation is never a success.
        assert outcome.failure.reason is FailureReason.TIMEOUT


class TestExistingLimitPathsStillWork:
    def test_existing_step_limit_agents_still_fail_explicitly(self) -> None:
        """Prior step-limit failure semantics are unchanged."""
        m = AgentManager()
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        # max_steps=1: the agent yields one step, then hits the limit and is
        # cancelled.
        task = _task(
            agent_name="cooperative_cancel",
            task_id="old-slimit",
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_existing_timeout_agents_still_fail_explicitly(self) -> None:
        """A sleeping agent is still bounded and cancelled at the timeout."""
        m = AgentManager()
        m.register(SLEEPY_AGENT_MANIFEST)
        task = _task(
            agent_name="sleepy",
            task_id="old-timeout",
            envelope=ResourceEnvelope(timeout_seconds=0.05, max_steps=1000),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.TIMEOUT


class TestPipelineCancellation:
    def test_stage_envelope_exhaustion_cancels_and_aborts_composition(
        self, tmp_path: Path
    ) -> None:
        """A stage hitting its step limit cancels and aborts the composition."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # The first stage has a tiny envelope so it is cancelled.
        task = TaskSpecification(
            version=TaskSpecVersion.V4,
            task_id="stage-cancel",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
            pipeline=SequentialComposition(
                version=PipelineVersion.V3,
                stages=(
                    StageSpec(
                        agent_name="cooperative_cancel",
                        stage_envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=2),
                    ),
                    StageSpec(agent_name="reverse"),
                ),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(s.status.value == "cancelled" for s in stored.steps)
        # The second stage never began.
        assert not any("stage 1 begin" in d for d in descriptions)

    def test_pipeline_invariants_intact(self) -> None:
        """Normal pipeline with permitted envelopes still succeeds."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "pipeline-ok",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "CBA"


class TestDeterminismAndAudit:
    def test_cancellation_is_deterministic_and_fail_closed(self, tmp_path: Path) -> None:
        """Two identical cancellation runs produce a coherent trajectory."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        task = _task(
            agent_name="cooperative_cancel",
            task_id="det",
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=2),
        )
        first = m.run(task)
        second = m.run(task)
        assert first.failure is not None and second.failure is not None
        assert first.failure.reason is second.failure.reason

    def test_partial_work_remains_recorded(self, tmp_path: Path) -> None:
        """Partial work before cancellation remains in the trajectory."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        task = _task(
            agent_name="cooperative_cancel",
            task_id="partial",
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=3),
        )
        outcome = m.run(task)
        assert outcome.result is None
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        steps = [s for s in stored.steps if s.status.value == "completed"]
        # Some cooperative steps were durable before cancellation.
        assert any("cooperative step" in s.description for s in steps)
        assert any(s.status.value == "cancelled" for s in stored.steps)