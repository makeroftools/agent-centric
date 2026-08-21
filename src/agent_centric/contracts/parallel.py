"""Parallel composition contract (versioned).

Parallel composition (fan-out / join) is a Manager-orchestrated set of
independent stages that may run concurrently, followed by a single deterministic
join. The Manager alone controls spawning, resource envelopes, cancellation of
siblings on failure, verification of each branch, and the final join. Agents
never gain the ability to invoke or coordinate with one another directly.

Each parallel stage reuses the full ``StageSpec`` capabilities already
established for sequential stages: exact selection (name or capability), tool
grants, optional per-stage envelope, and optional output/input schemas.

Join rule (minimal, deterministic): if and only if every stage succeeds and
verifies, the Manager produces a join of the stage outputs as an ordered list of
``(stage_index, agent, output)`` entries in the declared stage order. Any failure
- verification, policy, envelope exhaustion, cancellation, unknown agent, or
internal - cancels remaining running siblings and aborts the composition; no
partial success is returned.

Version history:
- parallel.v1: initial parallel composition contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .pipeline import StageSpec


class ParallelVersion(StrEnum):
    """Version of the parallel composition contract."""

    V1 = "parallel.v1"


@dataclass(frozen=True)
class ParallelComposition:
    """A Manager-orchestrated parallel fan-out of independent stages and join.

    Attributes:
        version: The parallel composition contract version.
        stages: The ordered, non-empty list of independent stages to run
            concurrently, in declared order (used for the deterministic join and
            trajectory ordering).
    """

    version: ParallelVersion
    stages: tuple[StageSpec, ...]

    def __post_init__(self) -> None:
        if self.version is not ParallelVersion.V1:
            raise ValueError(f"Unsupported parallel version: {self.version!r}")
        if not self.stages:
            raise ValueError("A parallel composition must have at least one stage.")