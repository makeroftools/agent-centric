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
  stage inherits the parent task envelope.
- pipeline.v3: additive — each ``StageSpec`` may declare an ``output_schema``
  and/or an ``input_schema``. The verified output of a stage is validated
  against the producing stage's ``output_schema`` and the consuming stage's
  ``input_schema`` before it is handed off as the next stage's input. Stages
  that declare neither schema use the documented conservative default (see
  ``validate_handoff``). All three versions are accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from .capability import Capability
from .handoff import HandoffSchema, is_valid_schema

if TYPE_CHECKING:
    from .task import ResourceEnvelope


class PipelineVersion(StrEnum):
    """Version of the sequential composition contract."""

    V1 = "pipeline.v1"
    V2 = "pipeline.v2"
    V3 = "pipeline.v3"


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
        output_schema: An optional schema the verified output of this stage must
            satisfy before it is handed off to the next stage. If None, no
            output constraint is declared for this stage.
        input_schema: An optional schema the input handed to this stage must
            satisfy. If None, no input constraint is declared for this stage.
    """

    agent_name: str | None = None
    capability: Capability | None = None
    granted_tools: tuple[str, ...] = field(default_factory=tuple)
    stage_envelope: ResourceEnvelope | None = None
    output_schema: HandoffSchema | None = None
    input_schema: HandoffSchema | None = None

    def __post_init__(self) -> None:
        if (self.agent_name is None) == (self.capability is None):
            raise ValueError(
                "Exactly one of agent_name or capability must be provided per stage."
            )
        if self.agent_name is not None and not self.agent_name:
            raise ValueError("agent_name must be non-empty when provided.")
        if any(not name for name in self.granted_tools):
            raise ValueError("granted_tools names must be non-empty.")
        if self.output_schema is not None and not is_valid_schema(self.output_schema):
            raise ValueError(f"Invalid output_schema: {self.output_schema!r}")
        if self.input_schema is not None and not is_valid_schema(self.input_schema):
            raise ValueError(f"Invalid input_schema: {self.input_schema!r}")


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
        if self.version not in (
            PipelineVersion.V1,
            PipelineVersion.V2,
            PipelineVersion.V3,
        ):
            raise ValueError(f"Unsupported pipeline version: {self.version!r}")
        if not self.stages:
            raise ValueError("A sequential composition must have at least one stage.")
        if self.version is PipelineVersion.V1 and any(
            stage.stage_envelope is not None for stage in self.stages
        ):
            raise ValueError(
                "pipeline.v1 does not support stage_envelope; use pipeline.v2."
            )
        if self.version in (PipelineVersion.V1, PipelineVersion.V2) and any(
            stage.output_schema is not None or stage.input_schema is not None
            for stage in self.stages
        ):
            raise ValueError(
                "pipeline.v1/v2 do not support hand-off schemas; use pipeline.v3."
            )