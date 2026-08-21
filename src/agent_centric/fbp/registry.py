"""A durable registry of callables (tasks, verifiers) by name.

The protocol is JSON over the wire, so callables cannot cross the bus. A
directive names a callable by its registered name; the agent resolves it from
this registry at run time. This is what makes tasks recallable, replayable,
and deterministic: the same name always resolves to the same callable.

This is a minimal stub for the foundation. Trust and persistence are clamped
down here: registration is explicit, and the trajectory records *which*
callable ran, so the system is fully auditable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

CallableT = Callable[..., Any]


class Registry:
    """A registry of named callables (tasks, verifiers).

    Attributes:
        _callables: Mapping of registered name to callable.
    """

    def __init__(self) -> None:
        self._callables: dict[str, CallableT] = {}

    def register(self, name: str, fn: CallableT) -> None:
        """Register a callable under ``name``.

        Args:
            name: The name directives will use to reference the callable.
            fn: The callable to register.
        """
        self._callables[name] = fn

    def resolve(self, name: str) -> CallableT | None:
        """Resolve a registered callable by name (None if not registered)."""
        return self._callables.get(name)

    def names(self) -> tuple[str, ...]:
        """Return the sorted registered names."""
        return tuple(sorted(self._callables))