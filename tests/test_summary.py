"""Tests for the trajectory summary & operator inspection API (Volley 014).

These tests prove that a deterministic, immutable summary can be derived from a
durable trajectory across every shape — successful single-agent, sequential, and
parallel runs; failure, policy-rejection, cancellation, and tool-rejection paths;
and runs with optional fields absent (no policy, no model, no parallel). They
also prove the summary is deterministic and never mutates the stored trajectory.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric.contracts.parallel import ParallelComposition, ParallelVersion
from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from agent_centric.contracts.policy import Policy, PolicyVersion
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.summary import RunState, StageKind
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.summary import summarise_stored, summarise_trajectory
from agent_centric.control_plane.tools import ToolRegistry
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from agent_centric.providers import StubModelProvider
from tests.conftest import CASE_TOOL_MANIFEST, MODEL_MANIFEST, REVERSE_MANIFEST
from tests.fake_agent import (
    COOPERATIVE_CANCEL_MANIFEST,
    UNGUARDED_TOOL_AGENT_MANIFEST,
    WRONG_AGENT_MANIFEST,
)


def _reverse_task(task_id: str, text: str = "abc") -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=task_id,
        agent_name="reverse",
        payload={"text": text},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    )


def _case_task(task_id: str, text: str = "hello", *, granted: tuple[str, ...]) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=task_id,
        agent_name="case_tool",
        payload={"text": text},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
    )


def _pipeline_task(
    task_id: str, stages: tuple[StageSpec, ...], *, policy: Policy | None = None
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        payload={"text": "abc"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        pipeline=SequentialComposition(version=PipelineVersion.V3, stages=stages),
        policy=policy,
    )


def _parallel_task(task_id: str, stages: tuple[StageSpec, ...]) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V6,
        task_id=task_id,
        payload={"text": "abc"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=500),
        parallel=ParallelComposition(version=ParallelVersion.V1, stages=stages),
    )


class TestSingleAgentSummary:
    def test_successful_single_agent_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        outcome = m.run(_reverse_task("single-ok"))
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.trajectory_id == outcome.trajectory_id
        assert summary.task_id == "single-ok"
        assert summary.agent_name == "reverse"
        assert summary.agents == ("reverse",)
        assert summary.state is RunState.VERIFIED
        assert summary.failure_reason is None
        assert summary.output == "cba"
        assert summary.stage_kind is StageKind.SINGLE
        assert summary.stages == ()
        assert summary.tools == ()
        assert summary.models is None
        assert summary.policy is None
        assert summary.cancellations == 0
        assert summary.steps == len(outcome.result.trajectory.steps)
        assert summary.approximate_time_seconds >= 0.0

    def test_summary_does_not_mutate_stored_trajectory(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        outcome = m.run(_reverse_task("no-mutate"))
        assert outcome.trajectory_id is not None

        before = m.load(outcome.trajectory_id)
        assert before is not None
        before_sig = [(s.step_index, s.status.value, s.description) for s in before.steps]

        m.summarise(outcome.trajectory_id)
        m.summarise(outcome.trajectory_id)

        after = m.load(outcome.trajectory_id)
        assert after is not None
        after_sig = [(s.step_index, s.status.value, s.description) for s in after.steps]
        assert after_sig == before_sig
        assert after.outcome == before.outcome

    def test_summary_is_deterministic(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        outcome = m.run(_reverse_task("det"))
        assert outcome.trajectory_id is not None

        first = m.summarise(outcome.trajectory_id)
        second = m.summarise(outcome.trajectory_id)
        assert first is not None and second is not None
        assert first == second

    def test_missing_trajectory_returns_none(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        assert m.summarise("does-not-exist") is None


class TestSequentialSummary:
    def test_successful_sequential_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "seq-ok",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.VERIFIED
        assert summary.stage_kind is StageKind.SEQUENTIAL
        assert summary.agents == ("case_tool", "reverse")
        assert [s.agent for s in summary.stages] == ["case_tool", "reverse"]
        assert [s.status for s in summary.stages] == ["completed", "completed"]
        # The mediated to_upper tool is reflected.
        assert len(summary.tools) == 1
        tool = summary.tools[0]
        assert tool.name == "to_upper"
        assert tool.granted is True
        assert tool.requests == 1
        assert tool.succeeded == 1

    def test_failed_sequential_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(WRONG_AGENT_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "seq-fail",
            (
                StageSpec(agent_name="wrong"),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.FAILED
        assert summary.failure_reason == FailureReason.VERIFICATION_FAILED.value
        assert summary.stage_kind is StageKind.SEQUENTIAL
        # Only the first stage began (and failed verification); the second stage
        # never began and is absent.
        assert [s.status for s in summary.stages] == ["failed"]


class TestParallelSummary:
    def test_successful_parallel_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par-ok",
            (
                StageSpec(agent_name="reverse"),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.VERIFIED
        assert summary.stage_kind is StageKind.PARALLEL
        assert summary.agents == ("reverse",)
        assert [s.status for s in summary.stages] == ["completed", "completed"]

    def test_failed_parallel_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(WRONG_AGENT_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par-fail",
            (
                StageSpec(agent_name="wrong"),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.FAILED
        assert summary.failure_reason == FailureReason.VERIFICATION_FAILED.value
        assert summary.stage_kind is StageKind.PARALLEL
        assert [s.status for s in summary.stages] == ["failed", "failed"]


class TestFailureAndRejectionPaths:
    def test_policy_rejection_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="policy-reject",
            agent_name="reverse",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
            policy=Policy(
                version=PolicyVersion.V1,
                deny_agents=frozenset({"reverse"}),
            ),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.FAILED
        assert summary.failure_reason == FailureReason.POLICY_VIOLATION.value
        assert summary.policy is not None
        assert summary.policy.accepted is False
        assert "reverse" in (summary.policy.message or "")

    def test_policy_accepted_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="policy-accept",
            agent_name="reverse",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
            policy=Policy(
                version=PolicyVersion.V1,
                allow_agents=frozenset({"reverse"}),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.policy is not None
        assert summary.policy.accepted is True
        assert any(kind == "agent" for kind, _ in summary.policy.constraints)

    def test_cancellation_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="cancel",
            agent_name="cooperative_cancel",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=2),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.FAILED
        assert summary.failure_reason == FailureReason.STEP_LIMIT.value
        assert summary.cancellations >= 1

    def test_tool_rejection_summary(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(UNGUARDED_TOOL_AGENT_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="tool-reject",
            agent_name="unguarded_tool",
            payload="abc",
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.VERIFIED
        assert len(summary.tools) == 1
        tool = summary.tools[0]
        assert tool.name == "to_upper"
        assert tool.granted is False
        assert tool.requests == 1
        assert tool.rejected == 1
        assert tool.succeeded == 0


class TestModelSummary:
    def test_model_call_summary(self, tmp_path: Path) -> None:
        provider = StubModelProvider(responses={"hi": "hello"})
        m = AgentManager(
            store=FileTrajectoryStore(tmp_path),
            tools=ToolRegistry(model_provider=provider),
        )
        m.register(MODEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="model-ok",
            agent_name="model",
            payload={"prompt": "hi", "expected": "hello"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("llm_complete",),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.VERIFIED
        # The model call is counted separately from tools.
        assert summary.models is not None
        assert summary.models.requests == 1
        assert summary.models.succeeded == 1
        assert summary.models.failed == 0
        assert summary.models.rejected == 0
        # llm_complete is not reported as a regular tool.
        assert all(t.name != "llm_complete" for t in summary.tools)

    def test_model_call_failure_summary(self, tmp_path: Path) -> None:
        from agent_centric.providers import FailingStubModelProvider

        m = AgentManager(
            store=FileTrajectoryStore(tmp_path),
            tools=ToolRegistry(model_provider=FailingStubModelProvider()),
        )
        m.register(MODEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="model-fail",
            agent_name="model",
            payload={"prompt": "hi", "expected": "hello"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("llm_complete",),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.FAILED
        assert summary.models is not None
        assert summary.models.requests == 1
        assert summary.models.failed == 1


class TestPureFunctions:
    def test_summarise_stored_roundtrip(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        outcome = m.run(_reverse_task("pure"))
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        summary = summarise_stored(stored)
        assert summary.state is RunState.VERIFIED
        assert summary.output == "cba"
        assert summary.trajectory_id == outcome.trajectory_id

    def test_summarise_trajectory_without_outcome_is_interrupted(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        outcome = m.run(_reverse_task("interrupted"))
        assert outcome.result is not None

        # Reconstruct the trajectory without the stored outcome.
        summary = summarise_trajectory(outcome.result.trajectory)
        assert summary.state is RunState.INTERRUPTED
        assert summary.output is None