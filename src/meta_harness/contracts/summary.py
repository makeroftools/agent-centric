"""Trajectory Summary contract (versioned).

This defines the versioned, immutable summary view an operator can obtain over a
durable trajectory. It is a read-only, observational projection: it is derived
from an already-recorded trajectory and never mutates the trajectory store or
any control-plane behaviour. Summaries are computed on demand (not persisted by
default) and are deterministic for the same trajectory content.

The summary is deliberately minimal and stable. It covers the aspects an
operator needs to understand a run without replaying every step: the task
identity, the terminal outcome, the agents/stages involved, mediated tool and
model calls, resource consumption, the policy decision, and any cancellations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SummaryVersion(StrEnum):
    """Version of the trajectory summary contract."""

    V1 = "summary.v1"


class RunState(StrEnum):
    """Terminal state of the run as recorded by the trajectory outcome."""

    VERIFIED = "verified"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class StageKind(StrEnum):
    """Structural kind of the run, derived from the trajectory boundaries."""

    SINGLE = "single"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class StageSummary:
    """Summary of a single composition stage (present for compositions).

    Attributes:
        index: Zero-based stage index within the composition.
        agent: Human-readable stage agent label (name or capability selector).
        status: One of ``completed``, ``cancelled``, or ``failed``. ``started``
            is not used: a stage is reported only once it began, and its status
            reflects the composition-level terminal state (see the builder).
    """

    index: int
    agent: str
    status: str


@dataclass(frozen=True)
class ToolSummary:
    """Summary of all mediated calls to a single (non-model) tool.

    Attributes:
        name: The tool's name.
        granted: True if the agent was granted the tool (any request accepted).
        requests: Number of times the agent requested the tool (accepted or
            rejected).
        succeeded: Number of successful tool executions.
        failed: Number of tool executions that errored.
        rejected: Number of requests rejected (not granted / policy-denied).
    """

    name: str
    granted: bool
    requests: int
    succeeded: int
    failed: int
    rejected: int


@dataclass(frozen=True)
class ModelSummary:
    """Summary of mediated ``llm_complete`` (model) calls, if any.

    Attributes:
        requests: Number of model tool requests.
        succeeded: Number of successful model calls.
        failed: Number of model calls that errored.
        rejected: Number of requests rejected.
    """

    requests: int
    succeeded: int
    failed: int
    rejected: int


@dataclass(frozen=True)
class PolicySummary:
    """The policy decision for the run, if a policy was attached.

    Attributes:
        accepted: True if the policy accepted the task/stages; False if it
            rejected them.
        constraints: The ordered constraints checked on acceptance (each a
            ``(kind, label)`` pair), or empty for a rejection.
        message: The rejection message, or None on acceptance.
    """

    accepted: bool
    constraints: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    message: str | None = None


@dataclass(frozen=True)
class TrajectorySummary:
    """A deterministic, immutable summary of a durable trajectory.

    Attributes:
        version: The summary contract version.
        trajectory_id: The unique identifier of the trajectory.
        task_id: The task this trajectory belongs to.
        agent_name: The primary agent component that executed the task.
        agents: Ordered unique agents/stages involved (for a composition, the
            stage agent labels in declared order; for a single run, the agent).
        state: The terminal run state (``verified``/``failed``/``interrupted``).
        failure_reason: The machine-readable failure reason, if ``state`` is
            ``failed``.
        failure_message: The human-readable failure message, if any.
        output: The verified output, if ``state`` is ``verified``.
        stage_kind: ``single``, ``sequential``, or ``parallel``.
        stages: Per-stage summaries for a composition (empty for single).
        tools: Per-tool summaries for mediated (non-model) tool calls.
        models: The model-call summary, if any ``llm_complete`` calls occurred.
        steps: Total number of recorded steps in the trajectory.
        approximate_time_seconds: Sum of recorded per-step elapsed time.
        policy: The policy decision, if a policy was attached.
        cancellations: Number of cancelled steps recorded in the trajectory.
    """

    version: SummaryVersion
    trajectory_id: str | None
    task_id: str
    agent_name: str
    agents: tuple[str, ...]
    state: RunState
    failure_reason: str | None
    failure_message: str | None
    output: Any
    stage_kind: StageKind
    stages: tuple[StageSummary, ...]
    tools: tuple[ToolSummary, ...]
    models: ModelSummary | None
    steps: int
    approximate_time_seconds: float
    policy: PolicySummary | None
    cancellations: int

    def __post_init__(self) -> None:
        if self.version is not SummaryVersion.V1:
            raise ValueError(f"Unsupported summary version: {self.version!r}")
        if self.steps < 0:
            raise ValueError("steps must be non-negative.")
        if self.approximate_time_seconds < 0.0:
            raise ValueError("approximate_time_seconds must be non-negative.")
        if self.cancellations < 0:
            raise ValueError("cancellations must be non-negative.")
        for stage in self.stages:
            if stage.status not in ("completed", "cancelled", "failed"):
                raise ValueError(f"Unexpected stage status: {stage.status!r}")
        for tool in self.tools:
            if tool.requests < 0 or tool.succeeded < 0:
                raise ValueError("Tool counts must be non-negative.")
            if tool.failed < 0 or tool.rejected < 0:
                raise ValueError("Tool counts must be non-negative.")
        if self.models is not None and (
            self.models.requests < 0
            or self.models.succeeded < 0
            or self.models.failed < 0
            or self.models.rejected < 0
        ):
            raise ValueError("Model counts must be non-negative.")