"""Critical Path analysis result contract (versioned).

Critical Path Method (CPM) analysis is a deterministic, read-only observational
aid owned by the control plane. It identifies the longest dependency chain
(the critical path) and per-stage slack/float over a composition plan and,
optionally, recorded consumption from a completed trajectory. It is analysis
only: it never alters scheduling, execution, or resource enforcement.

Cost metric (documented, deterministic):
- Default: the declared effective stage envelope's ``max_steps``. The effective
  envelope is the stage's ``stage_envelope`` if declared, else the parent task
  envelope.
- Override: when ``recorded_steps`` from a completed trajectory is supplied for
  a stage, that recorded step count is used as that stage's cost instead.

Path semantics:
- Sequential composition: the critical path is the full ordered sequence; the
  path length is the sum of stage costs, and every stage lies on the path with
  zero slack.
- Parallel composition: the critical path is the stage(s) with the greatest
  cost; the path length is that greatest cost, and every other stage has slack
  equal to ``max_cost - stage_cost``. Ties (multiple stages at the maximum cost)
  are all placed on the critical path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CpmVersion(StrEnum):
    """Version of the critical path analysis result contract."""

    V1 = "cpm.v1"


class CpmMetric(StrEnum):
    """The cost metric used by a critical path analysis."""

    ENVELOPE_MAX_STEPS = "envelope_max_steps"
    RECORDED_STEPS = "recorded_steps"


@dataclass(frozen=True)
class CriticalPathStage:
    """Per-stage critical path analysis.

    Attributes:
        stage: Zero-based stage index within the composition.
        agent: Human-readable stage label (agent name or capability selector).
        cost: The stage's cost under the chosen metric.
        slack: The stage's float / slack (0 for every stage on the critical
            path; for parallel stages this is ``max_cost - stage_cost``).
        on_critical_path: True if the stage lies on the critical path.
    """

    stage: int
    agent: str
    cost: int | float
    slack: int | float
    on_critical_path: bool


@dataclass(frozen=True)
class CriticalPathResult:
    """The result of a deterministic critical path analysis.

    Attributes:
        version: The analysis result contract version.
        kind: ``sequential`` or ``parallel``.
        metric: The cost metric used (``envelope_max_steps`` or
            ``recorded_steps``).
        path: The ordered stage indices lying on the critical path.
        path_length: The total cost of the critical path.
        stages: Per-stage analysis (cost, slack, on-path) in declared order.
        assumptions: Explicit statement of the metric and path semantics used.
    """

    version: CpmVersion
    kind: str
    metric: CpmMetric
    path: tuple[int, ...]
    path_length: int | float
    stages: tuple[CriticalPathStage, ...]
    assumptions: str

    def __post_init__(self) -> None:
        if self.version is not CpmVersion.V1:
            raise ValueError(f"Unsupported CPM version: {self.version!r}")
        if self.kind not in ("sequential", "parallel"):
            raise ValueError("CPM kind must be 'sequential' or 'parallel'.")
        if not self.stages:
            raise ValueError("A critical path analysis requires at least one stage.")