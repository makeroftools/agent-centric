"""Parent-provisioned component catalog (SPEC-0002 Phase-1 item 2, step S2).

A registry is a **passive catalog** (Law 10): it records *what* and *where*, and
never decides. Today the CBP tree still resolves task/verifier names from a
module-level global (``agent.py::_REGISTRY``); the SPEC-0002 target makes
registration/resolution **a component provided by the parent**, so a child can
resolve only what its parent explicitly provisioned and an absent name fails
closed.

This module introduces that explicit carrier — :class:`ComponentCatalog` — as an
**additive** layer over the existing :class:`~agent_centric.cbp.registry.Registry`.
It changes no behavior on its own: an agent that is not given a catalog keeps
resolving from the module global (the documented compatibility shim). Step S3
cuts the runtime over to the parent-provisioned catalog.
"""

from __future__ import annotations

from typing import Any

from .registry import CallableT, Registry, RegistryEntry


class ComponentCatalog:
    """An explicit, passive catalog provisioned by a parent to its children.

    Composition over :class:`Registry` (which stays the passive store). The
    catalog never fetches, compiles, or runs — it records metadata and resolves
    names, exactly like the registry it wraps.
    """

    def __init__(self) -> None:
        self._registry = Registry()

    def register(self, name: str, fn: CallableT, *, source_url: str = "") -> None:
        """Register a callable under ``name``, recording its importable source."""
        self._registry.register_entry(
            RegistryEntry.from_callable(name, fn, source_url=source_url)
        )

    def register_entry(self, entry: RegistryEntry) -> None:
        """Register a pre-built metadata entry."""
        self._registry.register_entry(entry)

    def resolve(self, name: str) -> CallableT | None:
        """Resolve a callable by name (None if absent)."""
        return self._registry.resolve(name)

    def entry(self, name: str) -> RegistryEntry | None:
        """Return the metadata entry by name (None if absent)."""
        return self._registry.entry(name)

    def source(self, name: str) -> str | None:
        """Return the source URL of an entry (None if absent)."""
        return self._registry.source(name)

    def names(self) -> tuple[str, ...]:
        """Return the sorted registered names."""
        return self._registry.names()

    def callable_from_source(self, name: str) -> CallableT | None:
        """Reconstruct a callable from its recorded importable source."""
        return self._registry.callable_from_source(name)

    @classmethod
    def from_entries(cls, entries: dict[str, RegistryEntry]) -> ComponentCatalog:
        """Build a catalog from a name→entry mapping (deterministic order)."""
        catalog = cls()
        for name in sorted(entries):
            catalog.register_entry(entries[name])
        return catalog

    def catalog_payload(self) -> dict[str, Any]:
        """A canonical passive-catalog payload (name + source + kind, sorted).

        The shape matches :func:`agent_centric.cbp.registry_component.catalog_snapshot`
        so the catalog can be projected, hashed, and replayed like any other
        passive snapshot.
        """
        items: list[dict[str, str]] = []
        for name in self._registry.names():
            entry = self._registry.entry(name)
            items.append(
                {
                    "name": name,
                    "source_url": entry.source_url if entry else "",
                    "kind": entry.kind if entry else "python",
                }
            )
        return {"catalog": items}
