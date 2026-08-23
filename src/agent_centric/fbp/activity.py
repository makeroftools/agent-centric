"""Operator activity feed — a deterministic, bounded, append-only audit trail.

This is the observability layer for *what an operator did* through the easy-UX
surface. Every action (a model prompt, an orchestrated or component-network run,
a bills intake/accept, a provisioning pass) is recorded as a single, immutable
entry carrying its verification status. It is the audit counterpart to the chat
history and the artifact vault: a bounded, deterministic, optionally-durable log
of operator actions.

Design (all enforced):

- **Append-only**: entries are never mutated once recorded; ``clear()`` is the
  only destructive operation (an explicit operator action).
- **Bounded**: the in-memory feed is capped at ``MAX_ACTIVITY_ENTRIES``; the
  oldest entries are dropped first, so the feed never grows without bound.
- **Deterministic**: entries are ordered by a monotonic sequence number
  (insertion order, oldest first); identical sequences yield identical readouts.
  A wall-clock timestamp is *optional* and never used for ordering, so the feed
  is fully reproducible and testable.
- **Durable by explicit grant**: a caller may pass a path; the feed persists
  atomically (temp file + ``os.replace``) and reloads on open. Without a grant
  it stays in-memory only — persistence is always an explicit grant.
- **Fail-closed**: a malformed entry is rejected; a corrupt durable file
  degrades to an empty feed rather than crashing the caller.

The feed is a pure, offline capability (no I/O, no model). It never decides
anything and never mutates durable state on its own — it only records and
replays what an operator did.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any

# The default cap on in-memory activity entries (a small, bounded feed).
MAX_ACTIVITY_ENTRIES = 500

# The kinds of activity the feed understands (a closed set, fail-closed).
ACTIVITY_KINDS = ("model", "orchestrate", "network", "bills", "provision", "action")


class ActivityError(ValueError):
    """An activity-feed input violated its contract (fail-closed)."""


@dataclass(frozen=True)
class ActivityEntry:
    """One recorded operator action (immutable, append-only).

    Attributes:
        seq: A monotonic sequence number (insertion order; the feed's sort key).
        kind: One of ``ACTIVITY_KINDS`` (the surface that produced the action).
        action: A short human-readable action label (e.g. ``model run``).
        detail: Optional structured detail (e.g. the prompt, the bill id).
        verified: Whether the action's outcome passed the correctness spine.
        model: Optional model/source id (for model actions).
        ts: Optional wall-clock timestamp (informational only; never used for
            ordering, so the feed stays deterministic and reproducible).
    """

    seq: int
    kind: str
    action: str
    detail: Any = None
    verified: bool = False
    model: str | None = None
    ts: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe, deterministic serialization (for the wire / page)."""
        return {
            "seq": self.seq,
            "kind": self.kind,
            "action": self.action,
            "detail": self.detail,
            "verified": self.verified,
            "model": self.model,
            "ts": self.ts,
        }


class ActivityFeed:
    """A bounded, append-only, optionally-durable operator activity feed.

    Entries are recorded in insertion order (monotonic ``seq``), bounded to
    ``max_entries`` (oldest dropped first). ``clear()`` is the only destructive
    operation. When a ``path`` is granted at construction the feed persists
    atomically and reloads on open; without a grant it stays in-memory only.
    """

    def __init__(self, *, max_entries: int = MAX_ACTIVITY_ENTRIES, path: str | None = None) -> None:
        if max_entries <= 0:
            raise ActivityError("max_entries must be positive")
        self._max = max_entries
        self._path = path
        self._entries: list[ActivityEntry] = []
        self._seq = 0
        if path is not None:
            self._load()

    # -- recording ----------------------------------------------------------

    def record(
        self,
        *,
        kind: str,
        action: str,
        detail: Any = None,
        verified: bool = False,
        model: str | None = None,
        ts: str | None = None,
    ) -> ActivityEntry:
        """Append one operator action (fail-closed on a malformed entry).

        ``kind`` must be in ``ACTIVITY_KINDS`` and ``action`` must be a non-empty
        string; anything else raises ``ActivityError`` so a malformed record is
        never silently accepted. Returns the recorded (immutable) entry.
        """
        if kind not in ACTIVITY_KINDS:
            raise ActivityError(
                f"unknown activity kind {kind!r}; "
                f"expected one of {', '.join(ACTIVITY_KINDS)}"
            )
        if not isinstance(action, str) or not action.strip():
            raise ActivityError("activity requires a non-empty 'action' string")
        entry = ActivityEntry(
            seq=self._seq,
            kind=kind,
            action=action.strip(),
            detail=detail,
            verified=bool(verified),
            model=model,
            ts=ts,
        )
        self._seq += 1
        self._entries.append(entry)
        if len(self._entries) > self._max:
            del self._entries[: len(self._entries) - self._max]
        if self._path is not None:
            self._save()
        return entry

    # -- read-only inspection ----------------------------------------------

    def all(self) -> tuple[ActivityEntry, ...]:
        """Return the feed in insertion order (oldest first). Read-only."""
        return tuple(self._entries)

    def count(self) -> int:
        """The number of entries currently in the feed."""
        return len(self._entries)

    def verified_count(self) -> int:
        """How many recorded actions were verified (a quick audit readout)."""
        return sum(1 for e in self._entries if e.verified)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready, deterministic snapshot of the whole feed."""
        return {
            "entries": [e.to_dict() for e in self._entries],
            "count": len(self._entries),
            "verified": self.verified_count(),
            "durable": self._path is not None,
        }

    # -- destructive (explicit operator action) -----------------------------

    def clear(self) -> None:
        """Drop every entry (the only destructive operation; explicit)."""
        self._entries.clear()
        self._seq = 0
        if self._path is not None:
            self._save()

    # -- durable persistence (explicit grant) -------------------------------

    def _load(self) -> None:
        """Reload a prior feed from the granted path (fail-closed)."""
        assert self._path is not None
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            # A corrupt durable feed must not crash the caller; degrade to empty.
            print(f"fbp-activity: could not load activity feed: {exc}")
            return
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            try:
                entry = ActivityEntry(
                    seq=int(raw.get("seq", self._seq)),
                    kind=str(raw.get("kind", "")),
                    action=str(raw.get("action", "")),
                    detail=raw.get("detail"),
                    verified=bool(raw.get("verified", False)),
                    model=raw.get("model"),
                    ts=raw.get("ts"),
                )
            except (TypeError, ValueError):
                continue
            if entry.kind not in ACTIVITY_KINDS or not entry.action:
                continue
            self._entries.append(entry)
            self._seq = max(self._seq, entry.seq + 1)
        # Bound after reload (a hand-edited file could exceed the cap).
        if len(self._entries) > self._max:
            del self._entries[: len(self._entries) - self._max]

    def _save(self) -> None:
        """Atomically persist the feed to the granted path (temp + replace)."""
        assert self._path is not None
        directory = os.path.dirname(os.path.abspath(self._path)) or "."
        os.makedirs(directory, exist_ok=True)
        payload = {"entries": [e.to_dict() for e in self._entries]}
        (fd, tmp_path) = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, sort_keys=True, default=str)
            os.replace(tmp_path, self._path)
        except BaseException:
            from contextlib import suppress

            with suppress(OSError):
                os.unlink(tmp_path)
            raise