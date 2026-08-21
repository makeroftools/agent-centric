"""Tests for nested composition: a sequential stage as a parallel group (Volley 016).

These tests prove that ``pipeline.v4`` allows a sequential stage to be either an
agent stage or a nested parallel group, that the Manager orchestrates the group
(concurrent branches, verify, join) and then continues to the next sequential
stage with the join handed off as input, and that failure inside a nested group
aborts the outer sequence and cancels siblings. They also prove policy,
envelopes, verification, tool mediation, cancellation, trajectory
reconstructibility, summary, and replay all still hold for nested runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_centric.contracts.parallel import ParallelComposition, ParallelVersion
from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from agent_centric.contracts.policy import Policy, PolicyVersion
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.summary import RunState, StageKind
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import CASE_TOOL_MANIFEST, REVERSE_MANIFEST
from tests.fake_agent import (
    COOPERATIVE_CANCEL_MANIFEST,
    JOIN_CONSUMER_MANIFEST,
    SLOW_COOPERATIVE_CANCEL_MANIFEST,
    WRONG_AGENT_MANIFEST,
)


def _nested_task(
    task_id: str,
    stages: tuple[StageSpec | ParallelComposition, ...],
    payload: Any,
    *,
    envelope: ResourceEnvelope | None = None,
    policy: Policy | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V6,
        task_id=task_id,
        payload=payload,
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=500),
        pipeline=SequentialComposition(version=PipelineVersion.V4, stages=stages),
        policy=policy,
    )


def _group(*stages: StageSpec) -> ParallelComposition:
    return ParallelComposition(version=ParallelVersion.V1, stages=stages)


class TestNestedContract:
    def test_v4_accepts_parallel_group_stage(self) -> None:
        """pipeline.v4 accepts a parallel group as a sequential stage."""
        comp = SequentialComposition(
            version=PipelineVersion.V4,
            stages=(
                _group(StageSpec(agent_name="reverse")),
                StageSpec(agent_name="reverse"),
            ),
        )
        assert len(comp.stages) == 2
        assert isinstance(comp.stages[0], ParallelComposition)
        assert isinstance(comp.stages[1], StageSpec)

    def test_v3_rejects_parallel_group_stage(self) -> None:
        """Pre-v4 pipelines reject nested parallel groups."""
        try:
            SequentialComposition(
                version=PipelineVersion.V3,
                stages=(
                    _group(StageSpec(agent_name="reverse")),
                    StageSpec(agent_name="reverse"),
                ),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("pipeline.v3 should reject nested parallel groups")

    def test_group_stages_are_agent_stages_only(self) -> None:
        """A parallel group's stages are agent stages (shallow nesting)."""
        # A ParallelComposition only accepts StageSpec, so deep nesting is
        # structurally impossible by construction.
        group = _group(StageSpec(agent_name="reverse"))
        assert all(isinstance(s, StageSpec) for s in group.stages)


class TestNestedSuccess:
    def test_sequential_parallel_sequential_succeeds(self, tmp_path: Path) -> None:
        """seq -> parallel group -> seq succeeds with correct hand-off and join."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        m.register(CASE_TOOL_MANIFEST)
        m.register(JOIN_CONSUMER_MANIFEST)
        task = _nested_task(
            "nested-ok",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                _group(
                    StageSpec(agent_name="reverse"),
                    StageSpec(agent_name="reverse"),
                ),
                StageSpec(agent_name="join_consumer"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # Stage 0 uppercases 'abc' -> 'ABC'. The group's two branches each
        # reverse 'ABC' -> 'CBA'. The join consumer concatenates branch outputs.
        assert outcome.result.output == "CBA|CBA"

        # Full coherent trajectory with pipeline and nested group markers.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "pipeline stage 0 begin" for d in descriptions)
        assert any(d == "pipeline stage 1 begin" for d in descriptions)
        assert any(d == "pipeline stage 2 begin" for d in descriptions)
        assert any(d == "parallel group begin" for d in descriptions)
        assert any(d == "parallel stage 0 begin" for d in descriptions)
        assert any(d == "parallel stage 1 begin" for d in descriptions)
        assert any(d == "parallel group end" for d in descriptions)
        # The pipeline stage 1 marker records the group kind.
        group_boundary = next(
            s for s in stored.steps if s.description == "pipeline stage 1 begin"
        )
        assert group_boundary.input["kind"] == "parallel"
        # Indices contiguous and ordered.
        assert [s.step_index for s in stored.steps] == list(range(len(stored.steps)))

    def test_join_handed_off_to_next_stage(self) -> None:
        """The join dict is handed off intact to the next sequential stage."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        m.register(JOIN_CONSUMER_MANIFEST)
        task = _nested_task(
            "nested-handoff",
            (
                _group(
                    StageSpec(agent_name="reverse"),
                    StageSpec(agent_name="reverse"),
                ),
                StageSpec(agent_name="join_consumer"),
            ),
            {"text": "ab"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # reverse('ab')='ba' for both branches; join consumer concatenates.
        assert outcome.result.output == "ba|ba"


class TestNestedFailure:
    def test_group_failure_aborts_outer_sequence(self, tmp_path: Path) -> None:
        """A failure inside a nested group aborts the outer sequence."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(WRONG_AGENT_MANIFEST)
        m.register(SLOW_COOPERATIVE_CANCEL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _nested_task(
            "nested-fail",
            (
                StageSpec(agent_name="reverse"),
                _group(
                    StageSpec(agent_name="wrong"),  # fails verification
                    StageSpec(agent_name="slow_cooperative_cancel"),
                ),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
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
        # The third sequential stage never began.
        assert not any(d == "pipeline stage 2 begin" for d in descriptions)

    def test_no_partial_success_on_group_failure(self) -> None:
        """Even if one branch succeeds, a sibling failure means no success."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        m.register(WRONG_AGENT_MANIFEST)
        task = _nested_task(
            "nested-partial",
            (
                _group(
                    StageSpec(agent_name="reverse"),  # would succeed
                    StageSpec(agent_name="wrong"),  # fails
                ),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_unknown_agent_in_group_aborts(self) -> None:
        """An unknown agent in a nested group aborts before any work runs."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _nested_task(
            "nested-unknown",
            (
                _group(
                    StageSpec(agent_name="reverse"),
                    StageSpec(agent_name="does_not_exist"),
                ),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.UNKNOWN_AGENT


class TestNestedGovernance:
    def test_policy_applies_to_group_stages(self) -> None:
        """A policy denying a nested group's agent aborts before any work."""
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        m.register(CASE_TOOL_MANIFEST)
        task = _nested_task(
            "nested-policy",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                _group(
                    StageSpec(agent_name="reverse"),
                    StageSpec(agent_name="reverse"),
                ),
            ),
            {"text": "abc"},
            policy=Policy(
                version=PolicyVersion.V1,
                deny_agents=frozenset({"reverse"}),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION

    def test_per_stage_envelope_applies_inside_group(self) -> None:
        """A nested group stage with a tiny envelope is cancelled."""
        m = AgentManager()
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _nested_task(
            "nested-env",
            (
                _group(
                    StageSpec(
                        agent_name="cooperative_cancel",
                        stage_envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
                    ),
                    StageSpec(agent_name="reverse"),
                ),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_tool_mediation_still_applies(self) -> None:
        """A nested group branch can use a mediated tool."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _nested_task(
            "nested-tool",
            (
                _group(
                    StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                    StageSpec(agent_name="reverse"),
                ),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        stages = outcome.result.output["stages"]
        assert stages[0][2] == "ABC"
        assert stages[1][2] == "cba"


class TestNestedTrajectory:
    def test_trajectory_reconstructible(self, tmp_path: Path) -> None:
        """The nested trajectory is durable and reconstructible after restart."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        m.register(JOIN_CONSUMER_MANIFEST)
        task = _nested_task(
            "nested-durable",
            (
                _group(
                    StageSpec(agent_name="reverse"),
                    StageSpec(agent_name="reverse"),
                ),
                StageSpec(agent_name="join_consumer"),
            ),
            {"text": "ab"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        fresh = AgentManager(store=FileTrajectoryStore(tmp_path))
        stored = fresh.load(outcome.trajectory_id)
        assert stored is not None
        assert stored.outcome.kind == "verified"
        assert [s.step_index for s in stored.steps] == list(range(len(stored.steps)))


class TestNestedSummary:
    def test_nested_summary_sequential_of_parallel(self, tmp_path: Path) -> None:
        """Summary treats a nested run as sequential (of parallel) and attributes agents."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        m.register(JOIN_CONSUMER_MANIFEST)
        task = _nested_task(
            "nested-summary",
            (
                _group(
                    StageSpec(agent_name="reverse"),
                    StageSpec(agent_name="reverse"),
                ),
                StageSpec(agent_name="join_consumer"),
            ),
            {"text": "ab"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        summary = m.summarise(outcome.trajectory_id)
        assert summary is not None
        assert summary.state is RunState.VERIFIED
        # Sequential of parallel: the pipeline stage markers dominate.
        assert summary.stage_kind is StageKind.SEQUENTIAL
        # Both branches and the consumer are attributed (deduplicated).
        assert summary.agents == ("reverse", "join_consumer")
        assert [s.status for s in summary.stages] == ["completed", "completed"]


class TestNestedReplay:
    def test_nested_replays_equivalent(self, tmp_path: Path) -> None:
        """A nested run replays equivalent under the documented multiset rule."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        m.register(JOIN_CONSUMER_MANIFEST)
        task = _nested_task(
            "nested-replay",
            (
                _group(
                    StageSpec(agent_name="reverse"),
                    StageSpec(agent_name="reverse"),
                ),
                StageSpec(agent_name="join_consumer"),
            ),
            {"text": "ab"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        result = m.replay(task, outcome.trajectory_id)
        assert result.passed, result.diffs