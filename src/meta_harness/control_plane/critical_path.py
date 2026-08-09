"""Deterministic, read-only Critical Path Method (CPM) analysis.

This module provides the pure function that computes a critical-path analysis
over a composition plan and, optionally, recorded consumption from a completed
trajectory. It is side-effect free with respect to execution: it never mutates
tasks, envelopes, schedules, or resource accounting. It is an observational /
planning aid only.

Cost metric (documented):
- Default ``envelope_max_steps``: the effective stage envelope's ``max_steps``
  (the stage's ``stage_envelope`` if declared, else the parent task envelope).
- Override ``recorded_steps``: when a per-stage recorded step count is provided
  for a completed trajectory, it is used instead of the declared envelope cost.

Path semantics:
- Sequential: critical path is the full ordered sequence; length is the sum of
  costs; every stage is on the path with zero slack.
- Parallel: critical path is the stage(s) of greatest cost; length is that
  greatest cost; other stages have slack ``max_cost - stage_cost``.

Input types accepted (any of these may be used as the plan):
    SequentialComposition, ParallelComposition, or a TaskSpecification carrying
    one of ``pipeline`` / ``parallel``.
"""

from __future__ import annotations

from ..contracts.critical_path import (
    CpmMetric,
    CpmVersion,
    CriticalPathResult,
    CriticalPathStage,
)
from ..contracts.parallel import ParallelComposition
from ..contracts.pipeline import SequentialComposition, StageSpec
from ..contracts.task import ResourceEnvelope, TaskSpecification

_ASSUMPTIONS = (
    "Cost = effective stage max_steps (recorded steps override when supplied). "
    "Sequential: path is the full ordered sequence. Parallel: path is the "
    "most costly stage(s); other stages have slack = max_cost - stage_cost."
)


def _label(stage: StageSpec) -> str:
    if stage.agent_name is not None:
        return stage.agent_name
    assert stage.capability is not None
    return f"capability:{stage.capability.name}"


def _stage_cost(
    stage: StageSpec,
    stage_index: int,
    parent: ResourceEnvelope,
    recorded: dict[int, int] | None,
) -> tuple[int | float, CpmMetric]:
    """Return ``(cost, metric)`` for a stage, honouring a recorded override."""
    recorded_cost = recorded.get(stage_index) if recorded is not None else None
    if recorded_cost is not None:
        return recorded_cost, CpmMetric.RECORDED_STEPS
    effective = stage.stage_envelope or parent
    return effective.max_steps, CpmMetric.ENVELOPE_MAX_STEPS


def analyse_critical_path(
    plan: SequentialComposition | ParallelComposition | TaskSpecification,
    recorded_steps: dict[int, int] | None = None,
    parent_envelope: ResourceEnvelope | None = None,
) -> CriticalPathResult:
    """Compute a deterministic critical-path analysis for ``plan``.

    Args:
        plan: The composition plan — a sequential composition, a parallel
            composition, or a task carrying one of them.
        recorded_steps: Optional mapping of stage index -> recorded step count
            from a completed trajectory. When provided for a stage, it overrides
            the declared envelope cost for that stage.
        parent_envelope: The parent task envelope to use as the default effective
            envelope for stages that do not declare their own. For a
            ``TaskSpecification`` plan this defaults to the task's own envelope.

    Returns:
        A ``CriticalPathResult`` describing the critical path and per-stage
        slack. Read-only: the inputs are never mutated.
    """
    parent = parent_envelope

    if isinstance(plan, TaskSpecification):
        parent = plan.envelope
        if plan.pipeline is not None:
            return _analyse_sequential(plan.pipeline.stages, parent, recorded_steps)
        if plan.parallel is not None:
            return _analyse_parallel(plan.parallel, parent, recorded_steps)
        raise ValueError(
            "TaskSpecification plan must carry a pipeline or parallel composition."
        )

    if isinstance(plan, ParallelComposition):
        if parent is None:
            raise ValueError(
                "A parent_envelope is required to analyse a bare ParallelComposition."
            )
        return _analyse_parallel(plan, parent, recorded_steps)

    if isinstance(plan, SequentialComposition):
        if parent is None:
            raise ValueError(
                "A parent_envelope is required to analyse a bare SequentialComposition."
            )
        return _analyse_sequential(plan.stages, parent, recorded_steps)

    raise TypeError(f"Unsupported plan type: {type(plan).__name__!r}")


def _analyse_sequential(
    stages: tuple[StageSpec, ...],
    parent: ResourceEnvelope,
    recorded: dict[int, int] | None,
) -> CriticalPathResult:
    result_stages: list[CriticalPathStage] = []
    metric = CpmMetric.ENVELOPE_MAX_STEPS
    total: int | float = 0
    for i, stage in enumerate(stages):
        cost, m = _stage_cost(stage, i, parent, recorded)
        if m is CpmMetric.RECORDED_STEPS:
            metric = CpmMetric.RECORDED_STEPS
        total += cost
        result_stages.append(
            CriticalPathStage(
                stage=i, agent=_label(stage), cost=cost, slack=0,
                on_critical_path=True,
            )
        )
    return CriticalPathResult(
        version=CpmVersion.V1,
        kind="sequential",
        metric=metric,
        path=tuple(range(len(stages))),
        path_length=total,
        stages=tuple(result_stages),
        assumptions=_ASSUMPTIONS,
    )


def _analyse_parallel(
    parallel: ParallelComposition,
    parent: ResourceEnvelope,
    recorded: dict[int, int] | None,
) -> CriticalPathResult:
    costs: list[int | float] = []
    agents: list[str] = []
    metric = CpmMetric.ENVELOPE_MAX_STEPS
    for i, stage in enumerate(parallel.stages):
        cost, m = _stage_cost(stage, i, parent, recorded)
        if m is CpmMetric.RECORDED_STEPS:
            metric = CpmMetric.RECORDED_STEPS
        costs.append(cost)
        agents.append(_label(stage))
    max_cost = max(costs)
    path = tuple(i for i, c in enumerate(costs) if c == max_cost)
    stages = tuple(
        CriticalPathStage(
            stage=i,
            agent=agents[i],
            cost=costs[i],
            slack=max_cost - costs[i],
            on_critical_path=costs[i] == max_cost,
        )
        for i in range(len(costs))
    )
    return CriticalPathResult(
        version=CpmVersion.V1,
        kind="parallel",
        metric=metric,
        path=path,
        path_length=max_cost,
        stages=stages,
        assumptions=_ASSUMPTIONS,
    )