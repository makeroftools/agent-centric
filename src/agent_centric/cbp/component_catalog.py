"""Parent-provisioned component catalog (SPEC-0002 Phase-1 item 2, step S2).

A registry is a **passive catalog** (Law 10): it records *what* and *where*, and
never decides. Historically the CBP tree resolved task/verifier names from a
module-level global (``agent.py::_REGISTRY``); SPEC-0002 item 2 replaced that
with a catalog **provided by the parent**, so a child resolves only what its
parent explicitly provisioned and an absent name fails closed.

This module is that explicit carrier — :class:`ComponentCatalog` — a **passive**
layer over :class:`~agent_centric.cbp.registry.Registry`. The runtime no longer
has a module-level registry global.
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

    def entries(self) -> tuple[RegistryEntry, ...]:
        """All entries in deterministic (name-sorted) order."""
        out: list[RegistryEntry] = []
        for name in self._registry.names():
            entry = self._registry.entry(name)
            if entry is not None:
                out.append(entry)
        return tuple(out)

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
