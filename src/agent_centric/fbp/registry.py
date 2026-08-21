"""A stub registry of agents and their metadata.

The registry is an **agent**, not a module-level structure. It is a **passive
catalog — nothing more**: it holds a list of records of metadata, where each
record describes an agent/capability (name, metadata, and the location/URL of
its source code or executable). The registry does **not** compile, does **not**
run, and does **not** hold code.

Other agents take the *information* (the location) and act on it — fetch,
compile, run — as their own governed directives allow. Because agents are
self-contained executable processes (potentially different runtimes), the
system can accommodate multiple programming languages simultaneously,
communicating over the ZeroMQ protocol rather than by sharing memory.

This is a minimal stub for the foundation. The protocol is JSON over the wire,
so callables cannot cross the bus; a directive names a callable by its
registered name, and the agent resolves it from this registry at run time.
Trust and persistence are clamped down here: registration is explicit, and the
trajectory records *which* callable ran, so the system is fully auditable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CallableT = Callable[..., Any]


@dataclass(frozen=True)
class RegistryEntry:
    """Metadata for a registered agent/capability (a passive catalog record).

    Attributes:
        name: The name directives use to reference this entry.
        source_url: The location (URL) of the source code or executable.
        kind: The runtime/language kind (e.g. "python", "executable").
        callable: An optional in-process callable (stub convenience only; the
            full design stores only metadata and location, and other agents
            fetch/compile/run as their own directives allow).
    """

    name: str
    source_url: str = ""
    kind: str = "python"
    callable: CallableT | None = field(default=None, repr=False)


class Registry:
    """A stub registry of named agents/capabilities.

    Attributes:
        _entries: Mapping of registered name to entry metadata.
    """

    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    def register(self, name: str, fn: CallableT) -> None:
        """Register a callable under ``name`` (stub; full design uses metadata).

        Args:
            name: The name directives will use to reference the callable.
            fn: The callable to register.
        """
        self._entries[name] = RegistryEntry(name=name, callable=fn)

    def resolve(self, name: str) -> CallableT | None:
        """Resolve a registered callable by name (None if not registered)."""
        entry = self._entries.get(name)
        return entry.callable if entry is not None else None

    def names(self) -> tuple[str, ...]:
        """Return the sorted registered names."""
        return tuple(sorted(self._entries))