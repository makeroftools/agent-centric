"""Capability contract (versioned).

A capability is an explicit, versioned declaration of what an agent can do. It
is used for capability-based selection and is designed for later matching.

Capabilities are immutable and hashable so they can be used as dictionary keys
in the deterministic Registry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    """A single, versioned capability an agent declares.

    Attributes:
        name: Stable, unique name of the capability.
        version: Version of the capability's semantics. Defaults to "1".
    """

    name: str
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Capability name must be non-empty.")
        if not self.version:
            raise ValueError("Capability version must be non-empty.")
