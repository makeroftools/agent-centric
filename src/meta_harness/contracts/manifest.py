"""Agent Component Manifest contract (versioned).

The manifest is the immutable declaration of an agent component: its identity,
the interface version it implements, and the capabilities it declares. The
Manager uses the manifest to register and later instantiate the agent.

Version history:
- manifest.v1: capabilities were a flat ``frozenset[str]``.
- manifest.v2: capabilities are structured, versioned ``Capability`` objects,
  suitable for exact-match selection and later matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .capability import Capability


class AgentManifestVersion(StrEnum):
    """Version of the manifest contract this record conforms to."""

    V1 = "manifest.v1"
    V2 = "manifest.v2"


@dataclass(frozen=True)
class AgentComponentManifest:
    """Immutable declaration of a governable agent component.

    Attributes:
        version: The manifest contract version.
        name: Unique, stable identifier of the agent component.
        entry_point: Fully-qualified import path to the agent factory, e.g.
            ``meta_harness.agents.counter:create_counter_agent``. The Manager
            resolves this to obtain a fresh agent instance per task.
        description: Human-readable summary of what the agent does.
        declared_capabilities: The set of capabilities the agent declares it
            can perform, as structured ``Capability`` objects.
    """

    version: AgentManifestVersion
    name: str
    entry_point: str
    description: str
    declared_capabilities: frozenset[Capability] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.version is not AgentManifestVersion.V2:
            raise ValueError(f"Unsupported manifest version: {self.version!r}")
        if not self.name:
            raise ValueError("Agent manifest name must be non-empty.")
        if not self.entry_point:
            raise ValueError("Agent manifest entry_point must be non-empty.")
