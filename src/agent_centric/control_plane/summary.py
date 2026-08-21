"""Deterministic, side-effect-free trajectory summary builder.

This module provides pure function(s) that project a loaded durable trajectory
into an immutable :class:`~agent_centric.contracts.summary.TrajectorySummary`.
It is observational only: it never mutates the trajectory store, never writes
records, and recomputes the same summary for the same trajectory content. It is
the read half of the operator inspection API.

Stage-status convention (documented):

- **Single**: no stages; ``stage_kind == "single"``.
- **Sequential**: stages are attributed cleanly because they run one after
  another in declared order. A stage that began and completed is ``completed``;
  the last-began stage is ``cancelled`` for envelope/cancel failures (a
  ``cancelled`` step was recorded) and ``failed`` otherwise; stages after it
  never began and are absent.
- **Parallel**: stage work interleaves in the append-only log, so per-stage
  failure attribution is not reliable from the trajectory alone. Every stage
  that was *announced* (a ``parallel stage N begin`` marker exists) is reported
  uniformly: ``completed`` when the run verified, ``cancelled`` when the group
  terminated by cancellation/envelope (recorded ``cancelled`` steps), and
  ``failed`` otherwise. The terminal ``failure_reason`` carries the cause.

Tool and model attribution uses the Manager's stable, recorded description
scheme (``tool 'NAME' request`` / ``tool 'NAME' result`` / ``model call failed``
/ ``llm_complete``) so the counts are derived from the audit log itself.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..contracts.summary import (
    ModelSummary,
    PolicySummary,
    RunState,
    StageKind,
    StageSummary,
    SummaryVersion,
    ToolSummary,
    TrajectorySummary,
)
from ..contracts.trajectory import StepRecord, StepStatus, Trajectory

_TOOL_REQUEST = "tool '"
_REQUEST_MARKER = " request"
_MODEL_TOOL = "llm_complete"


def _outcome_state(stored_outcome: Any) -> RunState:
    """Derive the terminal run state from the stored outcome kind."""
    kind = getattr(stored_outcome, "kind", None)
    if kind == "verified":
        return RunState.VERIFIED
    if kind == "failure":
        return RunState.FAILED
    return RunState.INTERRUPTED


def _compose_agents(trajectory: Trajectory) -> tuple[str, ...]:
    """Ordered unique agents involved in the run.

    For a composition this is the stage agent labels in declared order
    (deduplicated by position); for a single run it is just the trajectory's
    agent.
    """
    labels: list[str] = []
    for step in trajectory.steps:
        desc = step.description
        if desc.startswith("pipeline stage "):
            _append_marker_agent(step, labels)
        elif desc == "parallel group begin":
            _append_group_agents(step, labels)
        elif desc.startswith("parallel stage "):
            _append_marker_agent(step, labels)
    if labels:
        return tuple(dict.fromkeys(labels))
    return (trajectory.agent_name,)


def _append_marker_agent(step: StepRecord, labels: list[str]) -> None:
    agent = _marker_agent(step)
    if agent is not None and agent not in labels:
        labels.append(agent)


def _append_group_agents(step: StepRecord, labels: list[str]) -> None:
    data = step.input
    if not isinstance(data, dict):
        return
    stages = data.get("stages")
    if isinstance(stages, (list, tuple)):
        for label in stages:
            if isinstance(label, str) and label not in labels:
                labels.append(label)


def _marker_agent(step: StepRecord) -> str | None:
    data = step.input
    if isinstance(data, dict):
        agent = data.get("agent")
        if isinstance(agent, str):
            return agent
    return None


def _stage_kind(trajectory: Trajectory) -> StageKind:
    for step in trajectory.steps:
        desc = step.description
        if desc.startswith("parallel stage ") or desc == "parallel group begin":
            return StageKind.PARALLEL
        if desc.startswith("pipeline stage "):
            return StageKind.SEQUENTIAL
    return StageKind.SINGLE


def _sequential_stages(
    stages: list[tuple[int, str]], state: RunState, cancelled: bool
) -> tuple[StageSummary, ...]:
    """Attributed stage summaries for a sequential composition.

    ``stages`` is the ordered ``(marker_index, agent)`` list of stages that
    began. Every stage but the last one completed (it ran, verified, and handed
    off). The last began stage reflects the run's terminal state: ``completed``
    on success, ``cancelled`` when the run was cancelled (recorded cancelled
    steps), and ``failed`` otherwise.
    """
    if not stages:
        return ()
    result: list[StageSummary] = []
    for i, (_, label) in enumerate(stages):
        if i == len(stages) - 1:
            if state is RunState.VERIFIED:
                status = "completed"
            elif cancelled:
                status = "cancelled"
            else:
                status = "failed"
        else:
            status = "completed"
        result.append(StageSummary(index=i, agent=label, status=status))
    return tuple(result)


def _parallel_stages(
    trajectory: Trajectory, state: RunState, cancelled: bool
) -> tuple[StageSummary, ...]:
    """Uniform stage summaries for a parallel composition."""
    labels: list[str] = []
    for step in trajectory.steps:
        if step.description.startswith("parallel stage ") and step.status is StepStatus.STARTED:
            agent = _marker_agent(step)
            if agent is not None:
                labels.append(agent)
    if not labels:
        return ()
    status = (
        "completed"
        if state == RunState.VERIFIED
        else ("cancelled" if cancelled else "failed")
    )
    return tuple(
        StageSummary(index=i, agent=label, status=status) for i, label in enumerate(labels)
    )


def _has_cancellation(trajectory: Trajectory) -> bool:
    return any(s.status is StepStatus.CANCELLED for s in trajectory.steps)


def _is_tool_request(desc: str) -> bool:
    """True for a tool request step (accepted or rejected), not a result."""
    return desc.startswith(_TOOL_REQUEST) and _REQUEST_MARKER in desc


def _tool_summaries(trajectory: Trajectory) -> tuple[ToolSummary, ...]:
    """Per-tool (non-model) summaries derived from the request/result steps."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: _zero_counts())
    granted: dict[str, bool] = {}
    succeeded: dict[str, int] = defaultdict(int)
    failed: dict[str, int] = defaultdict(int)
    rejected: dict[str, int] = defaultdict(int)

    for step in trajectory.steps:
        desc = step.description
        if not _is_tool_request(desc):
            continue
        tool = _tool_name_from_request(desc)
        if tool == _MODEL_TOOL:
            continue
        counts[tool]["requests"] += 1
        other = _paired_result_step(trajectory, step)
        if step.status is StepStatus.REJECTED:
            rejected[tool] += 1
            continue
        granted[tool] = True
        if other is None:
            continue
        if other.status is StepStatus.FAILED:
            failed[tool] += 1
        else:
            succeeded[tool] += 1

    return tuple(
        ToolSummary(
            name=tool,
            granted=granted.get(tool, False),
            requests=metrics["requests"],
            succeeded=succeeded[tool],
            failed=failed[tool],
            rejected=rejected[tool],
        )
        for tool, metrics in sorted(counts.items())
    )


def _zero_counts() -> dict[str, int]:
    return {"requests": 0}


def _tool_name_from_request(desc: str) -> str:
    start = desc.index("'") + 1
    end = desc.index("'", start)
    return desc[start:end]


def _paired_result_step(
    trajectory: Trajectory, request_step: StepRecord
) -> StepRecord | None:
    """The next ``<tool> result`` step after ``request_step``, if any."""
    tool = _tool_name_from_request(request_step.description)
    for other in trajectory.steps:
        if other.step_index <= request_step.step_index:
            continue
        if other.description.startswith(f"tool '{tool}' result"):
            return other
    return None


def _model_summary(trajectory: Trajectory) -> ModelSummary | None:
    """Tally model (llm_complete) calls from the request/result steps."""
    requests = 0
    succeeded = 0
    failed = 0
    rejected = 0
    for step in trajectory.steps:
        desc = step.description
        if not _is_tool_request(desc):
            continue
        if _tool_name_from_request(desc) != _MODEL_TOOL:
            continue
        requests += 1
        if step.status is StepStatus.REJECTED:
            rejected += 1
            continue
        if _model_succeeded(trajectory, step):
            succeeded += 1
        else:
            failed += 1
    if requests == 0:
        return None
    return ModelSummary(requests=requests, succeeded=succeeded, failed=failed, rejected=rejected)


def _model_succeeded(trajectory: Trajectory, request_step: StepRecord) -> bool:
    for other in trajectory.steps:
        if other.step_index <= request_step.step_index:
            continue
        if other.description == "model call failed":
            return False
        if other.description == "received llm_complete tool result":
            return True
    return False


def _policy_summary(trajectory: Trajectory) -> PolicySummary | None:
    for step in trajectory.steps:
        if step.description == "policy accepted" and step.status is StepStatus.COMPLETED:
            constraints: list[tuple[str, str]] = []
            data = step.input
            if isinstance(data, dict) and isinstance(data.get("constraints"), (list, tuple)):
                for c in data["constraints"]:
                    if isinstance(c, (list, tuple)) and len(c) == 2:
                        constraints.append((str(c[0]), str(c[1])))
            return PolicySummary(accepted=True, constraints=tuple(constraints))
        if step.description == "policy rejected" and step.status is StepStatus.REJECTED:
            return PolicySummary(accepted=False, message=step.error)
    return None


def summarise_trajectory(
    trajectory: Trajectory,
    trajectory_id: str | None = None,
    stored_outcome: Any = None,
) -> TrajectorySummary:
    """Build a deterministic, immutable summary from a loaded trajectory.

    ``stored_outcome`` (optional) is the terminal ``StoredOutcome`` from the
    store, used to distinguish verified / failed / interrupted. When omitted the
    trajectory is treated as interrupted unless a terminal ``policy rejected``
    step is present (a fail-closed, always-recorded terminal decision).

    ``trajectory_id`` (optional) is the durable record id, surfaced on the
    summary when known.

    This function is side-effect free: it never reads or writes the store and
    returns the same result for the same ``trajectory`` content.
    """
    if stored_outcome is not None:
        state = _outcome_state(stored_outcome)
    else:
        state = _default_state(trajectory)

    failure_reason = (
        _failure_reason(stored_outcome) if stored_outcome is not None else None
    )
    failure_message = (
        getattr(stored_outcome, "message", None) if stored_outcome is not None else None
    )
    output = getattr(stored_outcome, "output", None) if stored_outcome is not None else None

    cancellations = sum(1 for s in trajectory.steps if s.status is StepStatus.CANCELLED)

    tool_summaries = _tool_summaries(trajectory)
    models = _model_summary(trajectory)
    policy = _policy_summary(trajectory)

    step_count = len(trajectory.steps)
    approximate_time = sum(s.elapsed_seconds for s in trajectory.steps)
    agents = _compose_agents(trajectory)
    kind = _stage_kind(trajectory)

    if kind is StageKind.SINGLE:
        stages: tuple[StageSummary, ...] = ()
    elif kind is StageKind.SEQUENTIAL:
        began: list[tuple[int, str]] = []
        for s in trajectory.steps:
            if s.description.startswith("pipeline stage "):
                agent = _marker_agent(s)
                if agent is not None:
                    began.append((s.step_index, agent))
        cancelled = _has_cancellation(trajectory)
        stages = _sequential_stages(began, state, cancelled=cancelled)
    else:
        stages = _parallel_stages(trajectory, state, cancelled=cancellations > 0)

    return TrajectorySummary(
        version=SummaryVersion.V1,
        trajectory_id=trajectory_id,
        task_id=trajectory.task_id,
        agent_name=trajectory.agent_name,
        agents=agents,
        state=state,
        failure_reason=failure_reason,
        failure_message=failure_message,
        output=output,
        stage_kind=kind,
        stages=stages,
        tools=tool_summaries,
        models=models,
        steps=step_count,
        approximate_time_seconds=approximate_time,
        policy=policy,
        cancellations=cancellations,
    )


def summarise_stored(stored_trajectory: Any) -> TrajectorySummary:
    """Build a summary directly from a ``StoredTrajectory``.

    Thin convenience wrapper that reconstructs the immutable ``Trajectory`` and
    passes the stored outcome for an accurate terminal state.
    """
    trajectory = stored_trajectory.to_trajectory()
    return summarise_trajectory(
        trajectory,
        trajectory_id=stored_trajectory.trajectory_id,
        stored_outcome=stored_trajectory.outcome,
    )


def _default_state(trajectory: Trajectory) -> RunState:
    """Fallback state when no stored outcome is provided."""
    for step in trajectory.steps:
        if step.description == "policy rejected":
            return RunState.FAILED
    return RunState.INTERRUPTED


def _failure_reason(stored_outcome: Any) -> str | None:
    reason = getattr(stored_outcome, "reason", None)
    return reason if isinstance(reason, str) else None