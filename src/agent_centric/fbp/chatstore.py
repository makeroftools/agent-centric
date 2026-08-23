"""Durable, deterministic chat-history persistence for the landing page.

The landing-page model box keeps a bounded **in-memory** transcript by default
(the easy, ephemeral UX). When a caller opts in by passing a ``history`` path
(e.g. ``fbp-web --history <path>``) the transcript is ALSO persisted durably so
it survives restarts — the "persisted chat history" item on the FBP roadmap.

This store follows the subsystem's persistence spine:

- **Persistence is an explicit grant.** Nothing is written unless a caller hands
  over a path (the in-memory fallback stays the default). A store must be
  ``open()`` (creating its file) before use.
- **Append-only, single-writer.** Like ``TrajectoryStore`` this is a *log*, not
  a mutable key/value store: turns are only ever appended (or bulk-cleared by
  an explicit operator action). One connection owns writes.
- **Deterministic order.** ``seq`` is a plain monotonic counter owned by the
  store (0, 1, 2, …). It is not user-supplied entropy; replaying an identical
  transcript over the same file rebuilds an identical, ordered log. There is no
  ``AUTOINCREMENT``/``rowid`` dependence for content — ``seq`` is derived
  explicitly from the current count so ordering is stable and auditable.
- **Bounded.** An upper bound trims the oldest turns, matching the in-memory
  transcript cap, so the durable file never grows without limit.

The transcript may contain user prompts and model answers, so the store
deliberately mirrors the prototype's *explicit-grant* posture: it is disabled
unless a path is provided and offers no networked-authenticated access (that
remains documented in ``docs/transport_trust_boundary.md``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ChatHistoryError(RuntimeError):
    """A durable chat-history operation failed (fail-closed)."""


# Default upper bound on durable transcript turns (mirrors the in-memory cap).
DEFAULT_MAX_TURNS = 100


class ChatHistoryStore:
    """An append-only, durable transcript for the model box (single writer).

    ``seq`` is the turn's position in the transcript (0, 1, 2, …), derived from
    the current count at insert time so ordering is deterministic and stable.
    Appending past ``max_turns`` trims the oldest turns, bounding the file.
    """

    def __init__(self, path: str | Path, *, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self._path = Path(path)
        self._max_turns = max(1, int(max_turns))
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> ChatHistoryStore:
        """Create (and recurse broken) parent dirs, open, and prepare the schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS turns ("
            "  seq INTEGER PRIMARY KEY,"
            "  role TEXT NOT NULL,"        # "user" | "assistant"
            "  content TEXT NOT NULL,"
            "  model TEXT,"
            "  verified INTEGER,"            # 1/0 for assistant turns; NULL otherwise
            "  error TEXT"
            ")"
        )
        self._conn.commit()
        return self

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ChatHistoryError("chat-history store is not open")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- writes -------------------------------------------------------------

    def append(
        self,
        *,
        role: str,
        content: str,
        model: str | None = None,
        verified: bool | None = None,
        error: str | None = None,
    ) -> int:
        """Append one transcript turn; return its ``seq``.

        ``seq`` is derived from the current count (max+1, or 0 for an empty
        log), so the transcript order is deterministic and a fresh append
        continues cleanly after a restart. The oldest turns are trimmed beyond
        ``max_turns``.
        """
        conn = self._require()
        (current_max,) = conn.execute("SELECT MAX(seq) FROM turns").fetchone()
        seq = (0 if current_max is None else int(current_max)) + 1
        conn.execute(
            "INSERT INTO turns (seq, role, content, model, verified, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (seq, role, content, model,
             1 if verified is True else 0 if verified is False else None,
             error),
        )
        # Bound the transcript: drop the oldest rows beyond max_turns.
        conn.execute(
            "DELETE FROM turns WHERE seq < (SELECT seq FROM turns "
            "ORDER BY seq DESC LIMIT 1 OFFSET ?)",
            (self._max_turns - 1 if self._max_turns > 1 else 0,),
        )
        conn.commit()
        return seq

    def clear(self) -> None:
        """Clear the whole transcript (an explicit operator action)."""
        conn = self._require()
        conn.execute("DELETE FROM turns")
        conn.commit()

    # -- reads --------------------------------------------------------------

    def all(self) -> tuple[dict[str, Any], ...]:
        """All turns, ordered by ``seq``, JSON-ready."""
        conn = self._require()
        rows = conn.execute(
            "SELECT seq, role, content, model, verified, error FROM turns "
            "ORDER BY seq"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for _seq, role, content, model, verified, error in rows:
            out.append({
                "role": role,
                "content": content,
                "model": model,
                "verified": (True if verified == 1 else False if verified == 0 else None),
                "error": error,
            })
        return tuple(out)

    def count(self) -> int:
        conn = self._require()
        (n,) = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
        return int(n)


def open_chat_history(path: str | Path, *, max_turns: int = DEFAULT_MAX_TURNS) -> ChatHistoryStore:
    """Open (creating if needed) a durable chat-history store at ``path``."""
    store = ChatHistoryStore(path, max_turns=max_turns)
    store.open()
    return store