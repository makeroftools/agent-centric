"""Tests for deterministic trajectory replay verification (Volley 015).

These tests prove that a deterministic run can be re-executed and checked for
equivalence against its stored trajectory under a documented definition:
same terminal outcome class / failure reason, same verified output, same ordered
step sequence (multiset for concurrent parallel work), same agents, and same
tool grant/rejection pattern. They also prove mismatches fail closed with
structured diffs, that the original trajectory is never mutated, and that
non-equivalent timings do not cause false failures.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric.contracts.parallel import ParallelComposition, ParallelVersion
from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from agent_centric.contracts.policy import Policy, PolicyVersion
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.replay import verify_replay
from agent_centric.control_plane.tools import ToolRegistry
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from agent_centric.providers import StubModelProvider
from tests.conftest import CASE_TOOL_MANIFEST, MODEL_MANIFEST, REVERSE_MANIFEST
from tests.fake_agent import (
    COOPERATIVE_CANCEL_MANIFEST,
    UNGUARDED_TOOL_AGENT_MANIFEST,
    WRONG_AGENT_MANIFEST,
)


def _reverse_task(task_id: str, text: str = "abcdef") -> TaskSpecification:
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
        payload={"text": "abcdefghij"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=500),
        parallel=ParallelComposition(version=ParallelVersion.V1, stages=stages),
    )


class TestSuccessfulReplay:
    def test_single_agent_replays_equivalent(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _reverse_task("single")
        outcome = m.run(task)
        assert outcome.trajectory_id is not None

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs
        assert result.original_trajectory_id == outcome.trajectory_id
        assert result.replayed_trajectory_id is not None
        assert result.replayed_trajectory_id != outcome.trajectory_id

    def test_sequential_replays_equivalent(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "seq",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.trajectory_id is not None

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs

    def test_parallel_replays_equivalent(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par",
            (
                StageSpec(agent_name="reverse"),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.trajectory_id is not None

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs

    def test_model_agent_replays_equivalent(self, tmp_path: Path) -> None:
        provider = StubModelProvider(responses={"hi": "hello"})
        m = AgentManager(
            store=FileTrajectoryStore(tmp_path),
            tools=ToolRegistry(model_provider=provider),
        )
        m.register(MODEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="model",
            agent_name="model",
            payload={"prompt": "hi", "expected": "hello"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("llm_complete",),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs


class TestFailurePathReplay:
    def test_verification_failure_replays_equivalent(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(WRONG_AGENT_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="verif",
            agent_name="wrong",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.trajectory_id is not None

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs

    def test_policy_rejection_replays_equivalent(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="policy",
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

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs

    def test_tool_denial_replays_equivalent(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(UNGUARDED_TOOL_AGENT_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="tool-deny",
            agent_name="unguarded_tool",
            payload="abc",
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs

    def test_step_limit_cancellation_replays_equivalent(self, tmp_path: Path) -> None:
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

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs


class TestMismatchDetection:
    def test_divergent_output_detected(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _reverse_task("mismatch", "abc")
        outcome = m.run(task)
        assert outcome.trajectory_id is not None

        # Replay with a different payload -> different output.
        divergent_task = _reverse_task("mismatch", "xyz")
        result = m.replay(divergent_task, outcome.trajectory_id)
        assert result.passed is False
        fields = {d.field for d in result.diffs}
        assert "output" in fields

    def test_divergent_failure_reason_detected(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="mismatch-cancel",
            agent_name="cooperative_cancel",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=2),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.trajectory_id is not None

        # Replay with a large envelope -> the run still hits the step limit but
        # after many more steps, so the step sequence diverges.
        divergent_task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="mismatch-cancel",
            agent_name="cooperative_cancel",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        result = m.replay(divergent_task, outcome.trajectory_id)
        assert result.passed is False
        assert any(d.field == "steps" for d in result.diffs)

    def test_verify_replay_pure_function_detects_divergence(self, tmp_path: Path) -> None:
        """The pure comparator detects a deliberately injected divergence."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _reverse_task("pure", "abc")
        outcome = m.run(task)
        assert outcome.trajectory_id is not None
        original = m.load(outcome.trajectory_id)
        assert original is not None

        # Run a second, different task and compare its trajectory directly.
        other = m.run(_reverse_task("pure", "xyz"))
        assert other.trajectory_id is not None
        replayed = m.load(other.trajectory_id)
        assert replayed is not None

        result = verify_replay(original, replayed)
        assert result.passed is False
        assert any(d.field == "output" for d in result.diffs)


class TestOriginalTrajectoryUnchanged:
    def test_replay_does_not_mutate_original(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _reverse_task("no-mutate")
        outcome = m.run(task)
        assert outcome.trajectory_id is not None

        before = m.load(outcome.trajectory_id)
        assert before is not None
        before_sig = [(s.step_index, s.status.value, s.description) for s in before.steps]
        before_outcome = before.outcome

        m.replay(task, outcome.trajectory_id)
        m.replay(task, outcome.trajectory_id)

        after = m.load(outcome.trajectory_id)
        assert after is not None
        after_sig = [(s.step_index, s.status.value, s.description) for s in after.steps]
        assert after_sig == before_sig
        assert after.outcome == before_outcome

    def test_replay_missing_trajectory_raises(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        import pytest

        with pytest.raises(ValueError):
            m.replay(_reverse_task("missing"), "does-not-exist")


class TestTimingInsensitivity:
    def test_different_timings_do_not_fail_replay(self, tmp_path: Path) -> None:
        """Equivalence excludes wall-clock timings (elapsed_seconds)."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _reverse_task("timing")
        outcome = m.run(task)
        assert outcome.trajectory_id is not None

        # The replayed run will have different elapsed_seconds; equivalence must
        # not treat that as a divergence.
        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs
        assert all(d.field != "timing" for d in result.diffs)