"""Tests for read-only Critical Path Method (CPM) analysis (Volley 011).

These tests prove that critical-path analysis identifies the longest dependency
chain and per-stage slack for sequential and parallel compositions under a
documented deterministic cost metric, is deterministic for the same inputs, is
read-only (does not mutate tasks, envelopes, or execution behaviour), and
handles edge cases (single stage, equal costs) explicitly.
"""

from __future__ import annotations

from meta_harness.contracts.critical_path import CpmMetric
from meta_harness.contracts.parallel import ParallelComposition, ParallelVersion
from meta_harness.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.critical_path import analyse_critical_path

PARENT = ResourceEnvelope(timeout_seconds=10.0, max_steps=200)


def _seq(*stages: StageSpec) -> SequentialComposition:
    return SequentialComposition(version=PipelineVersion.V3, stages=stages)


def _par(*stages: StageSpec) -> ParallelComposition:
    return ParallelComposition(version=ParallelVersion.V1, stages=stages)


def _env(max_steps: int) -> ResourceEnvelope:
    return ResourceEnvelope(timeout_seconds=10.0, max_steps=max_steps)


def _pipeline_task(*stages: StageSpec) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V6,
        task_id="cpm-task",
        payload={},
        envelope=PARENT,
        pipeline=_seq(*stages),
    )


def _parallel_task(*stages: StageSpec) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V6,
        task_id="cpm-task",
        payload={},
        envelope=PARENT,
        parallel=_par(*stages),
    )


class TestSequentialCriticalPath:
    def test_full_ordered_sequence_is_the_path(self) -> None:
        plan = _seq(
            StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
            StageSpec(agent_name="reverse"),
        )
        result = analyse_critical_path(plan, parent_envelope=PARENT)
        assert result.kind == "sequential"
        # Both stages inherit the parent envelope cost = max_steps=200.
        assert result.path == (0, 1)
        assert result.path_length == 400
        assert all(s.slack == 0 for s in result.stages)
        assert all(s.on_critical_path for s in result.stages)

    def test_path_length_is_sum_of_effective_costs(self) -> None:
        plan = _seq(
            StageSpec(
                agent_name="reverse",
                stage_envelope=_env(10),
            ),
            StageSpec(agent_name="reverse", stage_envelope=_env(30)),
        )
        result = analyse_critical_path(plan, parent_envelope=PARENT)
        assert result.path_length == 40
        assert [s.cost for s in result.stages] == [10, 30]

    def test_task_plan_with_pipeline(self) -> None:
        task = _pipeline_task(
            StageSpec(agent_name="reverse"),
            StageSpec(agent_name="reverse"),
        )
        result = analyse_critical_path(task)
        assert result.kind == "sequential"
        assert result.path == (0, 1)
        # Both inherit the parent task envelope (max_steps=200).
        assert result.path_length == 400


class TestParallelCriticalPath:
    def test_most_costly_stage_is_the_path(self) -> None:
        plan = _par(
            StageSpec(agent_name="reverse", stage_envelope=_env(10)),
            StageSpec(agent_name="case_tool", stage_envelope=_env(50)),
            StageSpec(agent_name="reverse", stage_envelope=_env(20)),
        )
        result = analyse_critical_path(plan, parent_envelope=PARENT)
        assert result.kind == "parallel"
        assert result.path == (1,)  # stage 1 is most costly
        assert result.path_length == 50
        stages = {s.stage: s for s in result.stages}
        assert stages[0].slack == 40
        assert stages[1].slack == 0
        assert stages[1].on_critical_path
        assert stages[2].slack == 30

    def test_equal_costs_all_on_path(self) -> None:
        plan = _par(
            StageSpec(agent_name="reverse", stage_envelope=_env(20)),
            StageSpec(agent_name="reverse", stage_envelope=_env(20)),
        )
        result = analyse_critical_path(plan, parent_envelope=PARENT)
        assert result.kind == "parallel"
        assert result.path == (0, 1)  # both tied for max
        assert result.path_length == 20
        assert all(s.slack == 0 for s in result.stages)
        assert all(s.on_critical_path for s in result.stages)

    def test_single_stage_parallel_is_its_own_path(self) -> None:
        plan = _par(
            StageSpec(agent_name="reverse", stage_envelope=_env(7)),
        )
        result = analyse_critical_path(plan, parent_envelope=PARENT)
        assert result.kind == "parallel"
        assert result.path == (0,)
        assert result.path_length == 7
        assert result.stages[0].slack == 0


class TestRecordedStepsMetric:
    def test_recorded_steps_override_envelope(self) -> None:
        plan = _par(
            StageSpec(agent_name="reverse", stage_envelope=_env(100)),
            StageSpec(agent_name="reverse", stage_envelope=_env(50)),
        )
        # Recorded consumption flips the critical path to stage 1.
        result = analyse_critical_path(
            plan, recorded_steps={0: 10, 1: 90}, parent_envelope=PARENT
        )
        assert result.metric is CpmMetric.RECORDED_STEPS
        assert result.path == (1,)
        assert result.path_length == 90
        assert result.stages[0].slack == 80


class TestDeterminismAndReadOnly:
    def test_deterministic_for_same_inputs(self) -> None:
        plan = _par(
            StageSpec(agent_name="reverse", stage_envelope=_env(10)),
            StageSpec(agent_name="reverse", stage_envelope=_env(30)),
        )
        a = analyse_critical_path(plan, parent_envelope=PARENT)
        b = analyse_critical_path(plan, parent_envelope=PARENT)
        assert a == b

    def test_analysis_does_not_mutate_plan(self) -> None:
        stage = StageSpec(agent_name="reverse", stage_envelope=_env(30))
        env = _env(30)
        plan = _par(stage)
        parent = ResourceEnvelope(timeout_seconds=10.0, max_steps=200)
        result = analyse_critical_path(plan, parent_envelope=parent)
        assert result.path_length == 30
        # Inputs are unchanged.
        assert stage.stage_envelope == env
        assert parent.max_steps == 200
        assert plan.stages == (stage,)

    def test_analysis_is_side_effect_free_of_execution(self) -> None:
        """CPM is a pure function: no Manager, scheduler, or state mutation."""
        plan = _seq(StageSpec(agent_name="reverse"))
        result = analyse_critical_path(plan, parent_envelope=PARENT)
        assert result.kind == "sequential"
        # Running analysis twice yields identical results and no hidden state.
        assert analyse_critical_path(plan, parent_envelope=PARENT) == result


class TestEdgeCases:
    def test_single_stage_sequential(self) -> None:
        plan = _seq(StageSpec(agent_name="reverse"))
        result = analyse_critical_path(plan, parent_envelope=PARENT)
        assert result.path == (0,)
        assert result.path_length == PARENT.max_steps
        assert result.stages[0].on_critical_path

    def test_unsupported_plan_rejected(self) -> None:
        try:
            analyse_critical_path("not-a-plan", parent_envelope=PARENT)  # type: ignore[arg-type]
        except TypeError:
            pass
        else:
            raise AssertionError("unsupported plan should be rejected")