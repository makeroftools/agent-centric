"""Verified Result and explicit Failure contracts (versioned).

A task execution terminates in exactly one of two audited states: a
``VerifiedResult`` (the agent's output passed the mandatory verification gate)
or an explicit ``Failure`` with a machine-readable reason. There is no third,
ambiguous state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .trajectory import Trajectory


class VerifiedResultVersion(StrEnum):
    """Version of the verified result contract."""

    V1 = "result.v1"


class FailureReason(StrEnum):
    """Machine-readable reason a task did not produce a verified result."""

    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    STEP_LIMIT = "step_limit"
    VERIFICATION_FAILED = "verification_failed"
    UNKNOWN_AGENT = "unknown_agent"
    HANDOFF_FAILED = "handoff_failed"
    INTERNAL = "internal"


@dataclass(frozen=True)
class VerifiedResult:
    """A result that passed the mandatory verification gate.

    Attributes:
        version: The verified result contract version.
        task_id: The task this result belongs to.
        output: The verified output produced by the agent.
        trajectory: The full audit record of the execution.
    """

    version: VerifiedResultVersion
    task_id: str
    output: Any
    trajectory: Trajectory

    def __post_init__(self) -> None:
        if self.version is not VerifiedResultVersion.V1:
            raise ValueError(f"Unsupported result version: {self.version!r}")


@dataclass(frozen=True)
class Failure:
    """An explicit, contained, audited failure.

    Attributes:
        task_id: The task that failed.
        reason: The machine-readable failure reason.
        message: A human-readable explanation.
        trajectory: The full audit record up to the point of failure.
    """

    task_id: str
    reason: FailureReason
    message: str
    trajectory: Trajectory
