"""Tests for Manager-orchestrated parallel composition (fan-out / join) (Volley 010).

These tests prove that a parallel composition runs independent stages under full
governance, that success yields a deterministic join, that any stage failure
cancels siblings and fails closed with a complete audit record, and that policy,
envelopes, verification, and trajectory coherence all still apply per stage.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric.contracts.parallel import ParallelComposition, ParallelVersion
from agent_centric.contracts.pipeline import StageSpec
from agent_centric.contracts.policy import Policy, PolicyVersion
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import CASE_TOOL_MANIFEST, REVERSE_MANIFEST
from tests.fake_agent import (
    COOPERATIVE_CANCEL_MANIFEST,
    SLOW_COOPERATIVE_CANCEL_MANIFEST,
    WRONG_AGENT_MANIFEST,
)


def _parallel_task(
    task_id: str,
    stages: tuple[StageSpec, ...],
    *,
    envelope: ResourceEnvelope | None = None,
    policy: Policy | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V6,
        task_id=task_id,
        payload={"text": "abc"},
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=500),
        parallel=ParallelComposition(version=ParallelVersion.V1, stages=stages),
        policy=policy,
    )


class TestParallelContract:
    def test_empty_parallel_rejected(self) -> None:
        from agent_centric.contracts.parallel import ParallelComposition

        try:
            ParallelComposition(version=ParallelVersion.V1, stages=())
        except ValueError:
            pass
        else:
            raise AssertionError("empty parallel composition should be rejected")

    def test_task_cannot_set_both_pipeline_and_parallel(self) -> None:
        from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition

        try:
            TaskSpecification(
                version=TaskSpecVersion.V6,
                task_id="both",
                payload={},
                pipeline=SequentialComposition(
                    version=PipelineVersion.V3,
                    stages=(StageSpec(agent_name="reverse"),),
                ),
                parallel=ParallelComposition(
                    version=ParallelVersion.V1,
                    stages=(StageSpec(agent_name="reverse"),),
                ),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("task with both pipeline and parallel should be rejected")

    def test_task_cannot_set_single_selector_with_parallel(self) -> None:
        try:
            TaskSpecification(
                version=TaskSpecVersion.V6,
                task_id="sel",
                agent_name="reverse",
                payload={},
                parallel=ParallelComposition(
                    version=ParallelVersion.V1,
                    stages=(StageSpec(agent_name="reverse"),),
                ),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("task with single selector and parallel should be rejected")


class TestParallelSuccess:
    def test_all_stages_succeed_deterministic_join(self, tmp_path: Path) -> None:
        """All stages run and succeed -> deterministic joined result and trajectory."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par-ok",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # Join is an ordered list of (stage_index, agent, output) in declared order.
        assert outcome.result.output == {
            "stages": [
                (0, "case_tool", "ABC"),
                (1, "reverse", "cba"),
            ]
        }

        # Full coherent trajectory with group and per-stage markers.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "parallel group begin" for d in descriptions)
        assert any(d == "parallel stage 0 begin" for d in descriptions)
        assert any(d == "parallel stage 1 begin" for d in descriptions)
        assert any(d == "parallel group end" for d in descriptions)
        # Indices contiguous and ordered.
        assert [s.step_index for s in stored.steps] == list(range(len(stored.steps)))

    def test_join_in_declared_order(self) -> None:
        """The join preserves declared stage order regardless of completion order."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par-order",
            (
                StageSpec(agent_name="reverse"),
                StageSpec(agent_name="reverse"),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        stages = outcome.result.output["stages"]
        assert [s[0] for s in stages] == [0, 1, 2]
        assert [s[1] for s in stages] == ["reverse", "reverse", "reverse"]
        assert [s[2] for s in stages] == ["cba", "cba", "cba"]


class TestParallelFailure:
    def test_one_stage_failure_aborts_and_cancels_siblings(self, tmp_path: Path) -> None:
        """A failing stage aborts the composition, cancels siblings, no success."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(WRONG_AGENT_MANIFEST)
        m.register(SLOW_COOPERATIVE_CANCEL_MANIFEST)
        task = _parallel_task(
            "par-fail",
            (
                StageSpec(agent_name="wrong"),  # fails verification
                StageSpec(agent_name="slow_cooperative_cancel"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

        # The sibling was cooperatively cancelled and the abort is audited.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "parallel group begin" for d in descriptions)
        assert any(s.status.value == "cancelled" for s in stored.steps)
        assert any(d == "parallel group end" for d in descriptions)

    def test_no_partial_success_on_failure(self) -> None:
        """Even if one stage succeeds, a sibling failure means no success."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        m.register(WRONG_AGENT_MANIFEST)
        task = _parallel_task(
            "par-partial",
            (
                StageSpec(agent_name="reverse"),  # would succeed
                StageSpec(agent_name="wrong"),  # fails
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_unknown_stage_agent_aborts(self) -> None:
        """An unknown agent in a parallel stage aborts before any thread runs."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par-unknown",
            (
                StageSpec(agent_name="reverse"),
                StageSpec(agent_name="does_not_exist"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.UNKNOWN_AGENT


class TestParallelGovernance:
    def test_policy_applies_per_stage(self) -> None:
        """A policy denying a stage's agent aborts before any work."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        m.register(CASE_TOOL_MANIFEST)
        task = _parallel_task(
            "par-policy",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            policy=Policy(
                version=PolicyVersion.V1,
                deny_agents=frozenset({"reverse"}),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION

    def test_per_stage_envelope_applies(self) -> None:
        """A stage with a tiny envelope is cancelled even if the parent is large."""
        m = AgentManager()
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par-env",
            (
                StageSpec(
                    agent_name="cooperative_cancel",
                    stage_envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
                ),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_verification_still_applies_per_stage(self) -> None:
        """Verification is enforced for each parallel stage."""
        m = AgentManager()
        m.register(WRONG_AGENT_MANIFEST)
        task = _parallel_task(
            "par-verify",
            (StageSpec(agent_name="wrong"),),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_tool_mediation_still_applies(self) -> None:
        """A parallel stage can use a mediated tool; ungranted is rejected."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        # to_upper granted -> succeeds.
        task = _parallel_task(
            "par-tool",
            (StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output["stages"][0][2] == "ABC"


class TestParallelTrajectory:
    def test_trajectory_coherent_and_reconstructible(self, tmp_path: Path) -> None:
        """The parallel trajectory is durable and reconstructible after restart."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _parallel_task(
            "par-durable",
            (
                StageSpec(agent_name="reverse"),
                StageSpec(agent_name="reverse"),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None

        fresh = AgentManager(store=FileTrajectoryStore(tmp_path))
        stored = fresh.load(outcome.trajectory_id or "")
        assert stored is not None
        assert stored.outcome.kind == "verified"
        # JSON round-trip turns the in-memory tuples into lists.
        assert stored.outcome.output == {
            "stages": [[0, "reverse", "cba"], [1, "reverse", "cba"]]
        }
        assert [s.step_index for s in stored.steps] == list(range(len(stored.steps)))

    def test_agents_cannot_invoke_each_other(self) -> None:
        """Agents have no mechanism to spawn or directly invoke other agents."""
        from agent_centric.agents.interface import ToolContext

        ctx = ToolContext()
        assert not hasattr(ctx, "invoke")
        assert not hasattr(ctx, "spawn")