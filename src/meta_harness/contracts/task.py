"""Task Specification and Resource Envelope contracts (versioned).

A task is submitted under a strict resource envelope. The envelope defines the
hard bounds the Manager must enforce: a wall-clock timeout, a maximum number of
steps, and an optional step-level time budget. These are not advisory.

Version history:
- task.v1: agent selected only by explicit ``agent_name``.
- task.v2: agent may be selected by explicit ``agent_name`` OR by an exact
  ``capability``. Exactly one selector must be provided.
- task.v3: additive — adds ``granted_tools`` (the tools explicitly granted to
  the agent for this task). A task.v2 spec is equivalent to a task.v3 spec with
  no granted tools.
- task.v4: additive — adds an optional ``pipeline`` (a Manager-orchestrated
  sequential composition). A task specifies either the single-agent selectors
  (``agent_name``/``capability``) OR a ``pipeline``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .capability import Capability
from .pipeline import SequentialComposition


class TaskSpecVersion(StrEnum):
    """Version of the task specification contract."""

    V1 = "task.v1"
    V2 = "task.v2"
    V3 = "task.v3"
    V4 = "task.v4"


@dataclass(frozen=True)
class ResourceEnvelope:
    """Hard resource bounds governing a single task execution.

    Attributes:
        timeout_seconds: Maximum wall-clock duration for the whole task.
            Must be a positive number.
        max_steps: Maximum number of agent steps allowed. Must be a positive
            integer.
        max_step_seconds: Optional per-step wall-clock budget. If set, must be
            positive. If None, only the overall timeout applies.
    """

    timeout_seconds: float
    max_steps: int
    max_step_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive.")
        if self.max_step_seconds is not None and self.max_step_seconds <= 0:
            raise ValueError("max_step_seconds must be positive when set.")


@dataclass(frozen=True)
class TaskSpecification:
    """A unit of work submitted to the Manager for a governed agent.

    Exactly one of ``agent_name`` or ``capability`` must be provided to select
    the agent to run.

    Attributes:
        version: The task specification contract version.
        task_id: Unique identifier for this task.
        agent_name: Name of the registered agent component to run. Mutually
            exclusive with ``capability``.
        capability: Exact capability required of the agent to run. Mutually
            exclusive with ``agent_name``.
        payload: The agent-specific input. Its interpretation is the agent's
            responsibility; the Manager treats it as opaque.
        envelope: The hard resource bounds for this task.
        granted_tools: The names of tools explicitly granted to the agent for
            this task. The agent may only request tools listed here.
        pipeline: An optional Manager-orchestrated sequential composition. If
            set, it overrides the single-agent selectors and the stage-level
            tool grants.
    """

    version: TaskSpecVersion
    task_id: str
    agent_name: str | None = None
    capability: Capability | None = None
    payload: Any = None
    envelope: ResourceEnvelope = field(default_factory=lambda: ResourceEnvelope(60.0, 100))
    granted_tools: tuple[str, ...] = field(default_factory=tuple)
    pipeline: SequentialComposition | None = None

    def __post_init__(self) -> None:
        if self.version not in (TaskSpecVersion.V2, TaskSpecVersion.V3, TaskSpecVersion.V4):
            raise ValueError(f"Unsupported task spec version: {self.version!r}")
        if not self.task_id:
            raise ValueError("task_id must be non-empty.")
        if self.pipeline is not None:
            # A pipeline task uses the stage selectors, not the single-agent ones.
            if self.agent_name is not None or self.capability is not None:
                raise ValueError(
                    "A pipeline task must not also set agent_name or capability."
                )
        else:
            if (self.agent_name is None) == (self.capability is None):
                raise ValueError(
                    "Exactly one of agent_name or capability must be provided to select an agent."
                )
            if self.agent_name is not None and not self.agent_name:
                raise ValueError("agent_name must be non-empty when provided.")
        if any(not name for name in self.granted_tools):
            raise ValueError("granted_tools names must be non-empty.")
