"""Load a component's execution entry, allowlist-guarded (SPEC-0007 §6, Q16).

Phase 1a executes only **allowlisted** (trusted) entries that resolve to code
already installed in the harness. A fetched bundle's code is never imported
until the subprocess isolation backend exists (a declared gate). An entry that
is not on the allowlist — or that uses a non-Python runtime — is refused.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, cast

from ..contracts.component import EntryDescriptor, RuntimeKind


class EntryNotAllowed(Exception):
    """A component entry is not allowed to execute (fail-closed)."""


class AllowlistedEntryResolver:
    """Resolve an :class:`EntryDescriptor` to a callable, if allowlisted."""

    def __init__(self, allowed: frozenset[str]) -> None:
        self._allowed = allowed

    def resolve(self, entry: EntryDescriptor) -> Callable[..., Any]:
        if entry.runtime != RuntimeKind.PYTHON:
            raise EntryNotAllowed(f"runtime {entry.runtime!r} is not allowed (Phase 1a)")
        if entry.entrypoint not in self._allowed:
            raise EntryNotAllowed(f"entrypoint {entry.entrypoint!r} is not allowlisted")
        module_name, sep, qualname = entry.entrypoint.partition(":")
        if not sep or not module_name or not qualname:
            raise EntryNotAllowed(f"malformed entrypoint {entry.entrypoint!r}")
        obj: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                raise EntryNotAllowed(f"cannot resolve {entry.entrypoint!r}")
        if not callable(obj):
            raise EntryNotAllowed(f"{entry.entrypoint!r} is not callable")
        return cast("Callable[..., Any]", obj)
