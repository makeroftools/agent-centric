"""Trajectory / Audit Record contract (versioned).

The trajectory is the durable, reconstructible record of everything that
happened during a task execution. It is append-only from the perspective of
the Manager: each step is recorded once, in order, and the whole trajectory
can be replayed to reconstruct the execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TrajectoryVersion(StrEnum):
    """Version of the trajectory contract."""

    V1 = "trajectory.v1"


class StepStatus(StrEnum):
    """Outcome of a single recorded step."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


@dataclass(frozen=True)
class StepRecord:
    """A single immutable step in the trajectory.

    Attributes:
        step_index: Zero-based, strictly increasing index of the step.
        status: The status of this step.
        description: Human-readable description of what the step did.
        input: The input observed at the start of the step (opaque).
        output: The output produced by the step (opaque); None if none.
        error: A short error message, if the step failed; else None.
        elapsed_seconds: Wall-clock duration of the step.
    """

    step_index: int
    status: StepStatus
    description: str
    input: Any = None
    output: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class Trajectory:
    """The complete, ordered, immutable audit record of a task execution.

    Attributes:
        version: The trajectory contract version.
        task_id: The task this trajectory belongs to.
        agent_name: The agent component that executed the task.
        steps: The ordered list of recorded steps.
    """

    version: TrajectoryVersion
    task_id: str
    agent_name: str
    steps: tuple[StepRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.version is not TrajectoryVersion.V1:
            raise ValueError(f"Unsupported trajectory version: {self.version!r}")
        # Enforce that step indices are strictly increasing and contiguous.
        for i, step in enumerate(self.steps):
            if step.step_index != i:
                raise ValueError(
                    f"Trajectory step index out of order: expected {i}, got {step.step_index}."
                )
