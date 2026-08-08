"""The deterministic in-process Registry owned by the Agent Manager.

The Registry is the single source of truth for registered agent components. It
is deterministic and side-effect free during lookup: given the same set of
registered manifests, it always resolves the same agent for a given selector.

Invariants enforced at registration:
- A manifest must be valid (validated by the manifest contract itself).
- An agent name must be unique.
- A capability must be owned by exactly one agent, so that capability-based
  lookup is always unambiguous. Registering a second agent that declares an
  already-owned capability is rejected as a conflict.
"""

from __future__ import annotations

from ..contracts.capability import Capability
from ..contracts.manifest import AgentComponentManifest


class Registry:
    """Deterministic registry of agent components, keyed by name and capability."""

    def __init__(self) -> None:
        self._by_name: dict[str, AgentComponentManifest] = {}
        self._by_capability: dict[Capability, AgentComponentManifest] = {}

    def register(self, manifest: AgentComponentManifest) -> None:
        """Register an agent component.

        Raises:
            ValueError: If the manifest is invalid, the name is already
                registered, or a declared capability is already owned by
                another agent.
        """
        # The manifest contract validates itself in __post_init__; this is a
        # defensive re-check that the object is a manifest at all.
        if not isinstance(manifest, AgentComponentManifest):
            raise ValueError("Registry.register requires an AgentComponentManifest.")

        if manifest.name in self._by_name:
            raise ValueError(f"Agent {manifest.name!r} is already registered.")

        for capability in manifest.declared_capabilities:
            owner = self._by_capability.get(capability)
            if owner is not None:
                raise ValueError(
                    f"Capability {capability.name!r} (v{capability.version}) is already "
                    f"declared by agent {owner.name!r}; cannot register {manifest.name!r}."
                )

        # All checks passed: commit atomically.
        self._by_name[manifest.name] = manifest
        for capability in manifest.declared_capabilities:
            self._by_capability[capability] = manifest

    def get_by_name(self, name: str) -> AgentComponentManifest | None:
        """Return the manifest for a registered agent name, or None."""
        return self._by_name.get(name)

    def get_by_capability(self, capability: Capability) -> AgentComponentManifest | None:
        """Return the manifest that owns an exact capability, or None.

        Exact match only: the capability name and version must both match.
        """
        return self._by_capability.get(capability)

    def names(self) -> tuple[str, ...]:
        """Return the registered agent names in deterministic (insertion) order."""
        return tuple(self._by_name.keys())

    def __len__(self) -> int:
        return len(self._by_name)
