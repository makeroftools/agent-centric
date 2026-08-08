"""Sequential composition contract (versioned).

Sequential composition is a Manager-orchestrated pipeline: two or more agents run
in sequence on a single task, with the verified output of each stage handed off
as the input to the next. The Manager alone is responsible for sequencing, data
hand-off, and the lifecycle of each stage. Agents never gain the ability to
spawn or directly invoke one another.

Version history:
- pipeline.v1: stages carry an agent selector and optional tool grants.
- pipeline.v2: additive — each ``StageSpec`` may declare its own
  ``stage_envelope``. If declared, it is enforced for that stage; otherwise the
  stage inherits the parent task envelope. Both versions are accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from .capability import Capability

if TYPE_CHECKING:
    from .task import ResourceEnvelope


class PipelineVersion(StrEnum):
    """Version of the sequential composition contract."""

    V1 = "pipeline.v1"
    V2 = "pipeline.v2"


@dataclass(frozen=True)
class StageSpec:
    """A single pipeline stage: how to select the agent to run.

    Exactly one of ``agent_name`` or ``capability`` must be provided to select
    the stage's agent.

    Attributes:
        agent_name: Name of the registered agent to run for this stage.
            Mutually exclusive with ``capability``.
        capability: Exact capability required of the agent for this stage.
            Mutually exclusive with ``agent_name``.
        granted_tools: The names of tools explicitly granted to the stage's
            agent for this stage.
        stage_envelope: An optional per-stage resource envelope. If set, it is
            enforced for this stage; if None, the stage inherits the parent task
            envelope. The parent task envelope still bounds the whole
            composition regardless.
    """

    agent_name: str | None = None
    capability: Capability | None = None
    granted_tools: tuple[str, ...] = field(default_factory=tuple)
    stage_envelope: ResourceEnvelope | None = None

    def __post_init__(self) -> None:
        if (self.agent_name is None) == (self.capability is None):
            raise ValueError(
                "Exactly one of agent_name or capability must be provided per stage."
            )
        if self.agent_name is not None and not self.agent_name:
            raise ValueError("agent_name must be non-empty when provided.")
        if any(not name for name in self.granted_tools):
            raise ValueError("granted_tools names must be non-empty.")


@dataclass(frozen=True)
class SequentialComposition:
    """An ordered, Manager-orchestrated pipeline of agent stages.

    Attributes:
        version: The pipeline contract version.
        stages: The ordered, non-empty list of stages to run in sequence.
    """

    version: PipelineVersion
    stages: tuple[StageSpec, ...]

    def __post_init__(self) -> None:
        if self.version not in (PipelineVersion.V1, PipelineVersion.V2):
            raise ValueError(f"Unsupported pipeline version: {self.version!r}")
        if not self.stages:
            raise ValueError("A sequential composition must have at least one stage.")
        if self.version is PipelineVersion.V1 and any(
            stage.stage_envelope is not None for stage in self.stages
        ):
            raise ValueError(
                "pipeline.v1 does not support stage_envelope; use pipeline.v2."
            )