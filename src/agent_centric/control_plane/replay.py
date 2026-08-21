"""Deterministic trajectory replay verification.

This module provides a Manager-owned API that re-executes a task under the same
deterministic configuration and confirms the new trajectory is equivalent to the
stored one in all correctness-relevant respects. It is read-only with respect to
the original trajectory: it never mutates the stored audit record.

Equivalence definition (documented)
-----------------------------------
A replayed run is *equivalent* to the stored trajectory when all of the
following hold:

1. **Terminal outcome class**: both are verified, or both are failed with the
   same machine-readable failure reason. (An interrupted stored trajectory is
   not replayed as equivalent — see below.)
2. **Verified output**: when both are verified, the outputs are equal.
3. **Step sequence**: the ordered sequence of ``(status, description)`` step
   signatures is identical for single-agent and sequential runs. For parallel
   runs, concurrent stage work interleaves in the append-only log and its order
   is not guaranteed deterministic, so the *multiset* of step signatures is
   compared instead (order among concurrent work is excluded). Stage-boundary
   markers (``pipeline stage N begin`` / ``parallel group begin`` /
   ``parallel stage N begin`` / ``parallel group end``) are always compared in
   order, since they are recorded deterministically.
4. **Agents / selections**: the ordered unique agents involved match.
5. **Tool grant / rejection pattern**: the per-tool request / success / failure
   / rejection counts match.

Explicitly excluded from equivalence (never cause a false failure):
- Wall-clock timings (``elapsed_seconds``) and any non-deterministic fields.
- The trajectory ids (the replayed run necessarily gets a new id).
- The exact ordering of *concurrent* parallel stage steps (see rule 3).

Interrupted trajectories: a stored trajectory whose outcome kind is
``interrupted`` is not replayed as equivalent by this API. Replaying a task that
was interrupted would require reproducing the interruption (e.g. a crash), which
is not a deterministic configuration. The verifier reports this explicitly as a
non-passing result with a clear message.
"""

from __future__ import annotations

from collections import Counter

from ..contracts.replay import ReplayDiff, ReplayResult, ReplayVersion
from ..contracts.trajectory import StepRecord, StepStatus, Trajectory
from .trajectory_store import StoredOutcome, StoredTrajectory

# Step descriptions that are recorded deterministically as composition
# boundaries; these are always compared in order, even for parallel runs.
_BOUNDARY_PREFIXES = (
    "pipeline stage ",
    "parallel group begin",
    "parallel stage ",
    "parallel group end",
)


def _is_parallel(trajectory: Trajectory) -> bool:
    return any(
        s.description.startswith("parallel stage ")
        or s.description == "parallel group begin"
        for s in trajectory.steps
    )


def _step_signature(step: StepRecord) -> tuple[str, str]:
    """The correctness-relevant signature of a step (status + description)."""
    return (step.status.value, step.description)


def _is_boundary(step: StepRecord) -> bool:
    return step.description.startswith(_BOUNDARY_PREFIXES)


def _steps_equivalent(original: Trajectory, replayed: Trajectory) -> bool:
    """Compare step signatures under the documented equivalence rules."""
    if _is_parallel(original) or _is_parallel(replayed):
        # Compare boundary markers in order, and the multiset of all other
        # (concurrent) step signatures.
        orig_boundaries = [_step_signature(s) for s in original.steps if _is_boundary(s)]
        rep_boundaries = [_step_signature(s) for s in replayed.steps if _is_boundary(s)]
        if orig_boundaries != rep_boundaries:
            return False
        orig_work = Counter(
            _step_signature(s) for s in original.steps if not _is_boundary(s)
        )
        rep_work = Counter(
            _step_signature(s) for s in replayed.steps if not _is_boundary(s)
        )
        return orig_work == rep_work
    # Single-agent and sequential runs: exact ordered sequence.
    return [_step_signature(s) for s in original.steps] == [
        _step_signature(s) for s in replayed.steps
    ]


def _agents_equivalent(original: Trajectory, replayed: Trajectory) -> bool:
    """Compare the ordered unique agents involved in the run."""
    return _compose_agents(original) == _compose_agents(replayed)


def _compose_agents(trajectory: Trajectory) -> tuple[str, ...]:
    labels: list[str] = []
    for step in trajectory.steps:
        desc = step.description
        if desc.startswith("pipeline stage ") or desc.startswith("parallel stage "):
            data = step.input
            if isinstance(data, dict):
                agent = data.get("agent")
                if isinstance(agent, str) and agent not in labels:
                    labels.append(agent)
        elif desc == "parallel group begin":
            data = step.input
            if isinstance(data, dict) and isinstance(data.get("stages"), (list, tuple)):
                for agent in data["stages"]:
                    if isinstance(agent, str) and agent not in labels:
                        labels.append(agent)
    if labels:
        return tuple(labels)
    return (trajectory.agent_name,)


def _tool_pattern(trajectory: Trajectory) -> tuple[tuple[str, int, int, int, int], ...]:
    """Per-tool (name, requests, succeeded, failed, rejected) pattern.

    Model calls (``llm_complete``) are treated as tools here for the grant /
    rejection pattern; they are deterministic under the stub provider.
    """
    from collections import defaultdict

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # req, ok, fail, rej
    for step in trajectory.steps:
        desc = step.description
        if not (desc.startswith("tool '") and " request" in desc):
            continue
        tool = _tool_name(desc)
        c = counts[tool]
        c[0] += 1
        if step.status is StepStatus.REJECTED:
            c[3] += 1
            continue
        paired = _paired_result(trajectory, step, tool)
        if paired is None:
            continue
        if paired.status is StepStatus.FAILED:
            c[2] += 1
        else:
            c[1] += 1
    return tuple((name, c[0], c[1], c[2], c[3]) for name, c in sorted(counts.items()))


def _tool_name(desc: str) -> str:
    start = desc.index("'") + 1
    end = desc.index("'", start)
    return desc[start:end]


def _paired_result(trajectory: Trajectory, request: StepRecord, tool: str) -> StepRecord | None:
    for other in trajectory.steps:
        if other.step_index <= request.step_index:
            continue
        if other.description.startswith(f"tool '{tool}' result"):
            return other
        if other.description == "model call failed":
            return other
    return None


def _outcome_kind(stored: StoredOutcome) -> str:
    return stored.kind


def _failure_reason(stored: StoredOutcome) -> str | None:
    return stored.reason


def _verify_against_stored(
    original: StoredTrajectory,
    replayed: StoredTrajectory,
) -> ReplayResult:
    """Compare a stored trajectory against a freshly replayed one."""
    diffs: list[ReplayDiff] = []
    orig_outcome = original.outcome
    rep_outcome = replayed.outcome

    # Interrupted stored trajectories are not deterministically replayable.
    if _outcome_kind(orig_outcome) == "interrupted":
        return ReplayResult(
            version=ReplayVersion.V1,
            passed=False,
            original_trajectory_id=original.trajectory_id,
            replayed_trajectory_id=replayed.trajectory_id,
            diffs=(
                ReplayDiff(
                    field="outcome",
                    original="interrupted",
                    replayed=_outcome_kind(rep_outcome),
                ),
            ),
            message=(
                "The stored trajectory is interrupted and cannot be replayed as "
                "equivalent; replay requires a deterministic configuration."
            ),
        )

    # 1. Terminal outcome class + failure reason.
    if _outcome_kind(orig_outcome) != _outcome_kind(rep_outcome):
        diffs.append(
            ReplayDiff(
                field="outcome",
                original=_outcome_kind(orig_outcome),
                replayed=_outcome_kind(rep_outcome),
            )
        )
    if _outcome_kind(orig_outcome) == "failure" and (
        _failure_reason(orig_outcome) != _failure_reason(rep_outcome)
    ):
        diffs.append(
            ReplayDiff(
                field="failure_reason",
                original=_failure_reason(orig_outcome),
                replayed=_failure_reason(rep_outcome),
            )
        )

    # 2. Verified output.
    if _outcome_kind(orig_outcome) == "verified" and orig_outcome.output != rep_outcome.output:
        diffs.append(
            ReplayDiff(
                field="output",
                original=orig_outcome.output,
                replayed=rep_outcome.output,
            )
        )

    orig_traj = original.to_trajectory()
    rep_traj = replayed.to_trajectory()

    # 3. Step sequence.
    if not _steps_equivalent(orig_traj, rep_traj):
        diffs.append(
            ReplayDiff(
                field="steps",
                original=len(orig_traj.steps),
                replayed=len(rep_traj.steps),
            )
        )

    # 4. Agents / selections.
    if not _agents_equivalent(orig_traj, rep_traj):
        diffs.append(
            ReplayDiff(
                field="agents",
                original=_compose_agents(orig_traj),
                replayed=_compose_agents(rep_traj),
            )
        )

    # 5. Tool grant / rejection pattern.
    if _tool_pattern(orig_traj) != _tool_pattern(rep_traj):
        diffs.append(
            ReplayDiff(
                field="tools",
                original=_tool_pattern(orig_traj),
                replayed=_tool_pattern(rep_traj),
            )
        )

    passed = not diffs
    message = (
        "Replay is equivalent to the stored trajectory."
        if passed
        else f"Replay diverged in {len(diffs)} correctness-relevant aspect(s)."
    )
    return ReplayResult(
        version=ReplayVersion.V1,
        passed=passed,
        original_trajectory_id=original.trajectory_id,
        replayed_trajectory_id=replayed.trajectory_id,
        diffs=tuple(diffs),
        message=message,
    )


def verify_replay(
    original: StoredTrajectory,
    replayed: StoredTrajectory,
) -> ReplayResult:
    """Verify that a replayed run is equivalent to a stored trajectory.

    This is the pure comparison used by the Manager's replay API. It never
    mutates either trajectory and is deterministic for the same inputs.
    """
    return _verify_against_stored(original, replayed)


# Re-export for convenience in the public surface.
__all__ = ["verify_replay", "ReplayResult", "ReplayDiff", "ReplayVersion"]