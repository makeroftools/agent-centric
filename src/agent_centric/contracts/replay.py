"""Replay verification result contract (versioned).

This defines the immutable result of a deterministic trajectory replay check. A
replay re-executes a task under the same deterministic configuration and
confirms that the new trajectory is equivalent to the stored one in all
correctness-relevant respects. The result is fail-closed: it reports ``passed``
only when every equivalence rule holds, and otherwise carries structured diffs
describing each divergence.

Replay verification is read-only with respect to the *original* trajectory: it
never mutates the stored audit record. It applies only to deterministic
configurations (deterministic agents and stub/fake providers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReplayVersion(StrEnum):
    """Version of the replay verification result contract."""

    V1 = "replay.v1"


@dataclass(frozen=True)
class ReplayDiff:
    """A single structured divergence between the original and replayed run.

    Attributes:
        field: The correctness-relevant aspect that diverged (e.g. ``outcome``,
            ``failure_reason``, ``output``, ``steps``, ``agents``).
        original: The value observed in the original stored trajectory.
        replayed: The value observed in the replayed run.
    """

    field: str
    original: Any = None
    replayed: Any = None


@dataclass(frozen=True)
class ReplayResult:
    """The result of a deterministic replay verification.

    Attributes:
        version: The replay result contract version.
        passed: True iff the replayed run is equivalent to the stored one under
            the documented equivalence definition.
        original_trajectory_id: The id of the stored trajectory being verified.
        replayed_trajectory_id: The id of the freshly replayed trajectory.
        diffs: Structured divergences (empty when ``passed`` is True).
        message: A human-readable summary of the outcome.
    """

    version: ReplayVersion
    passed: bool
    original_trajectory_id: str | None
    replayed_trajectory_id: str | None
    diffs: tuple[ReplayDiff, ...] = field(default_factory=tuple)
    message: str = ""

    def __post_init__(self) -> None:
        if self.version is not ReplayVersion.V1:
            raise ValueError(f"Unsupported replay version: {self.version!r}")
        if self.passed and self.diffs:
            raise ValueError("A passing replay must not carry diffs.")
