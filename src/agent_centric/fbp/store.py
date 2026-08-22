"""Durable, deterministic storage for the agent-centric FBP foundation.

This is the *on-demand, durable persistence layer* an agent may opt into via
``configure``. It provides two distinct, purpose-separated stores:

- **state** — a mutable, authoritative, single-writer key/value store for a
  resource an agent owns (e.g. a registry, a counter, a ledger).
- **trajectory** — an append-only audit record of a single agent's local
  activity (every directive it received and every response it produced). This
  is where *chain* audit begins: each agent records locally, and parents
  connect the chain as verified responses bubble up.

Both are plain SQLite files (stdlib ``sqlite3``), each in its own file,
created on demand. Persistence is an explicit, parent-provisioned grant—an
agent never silently writes a file. A store may be opened read-only so an
agent can read a parent-provisioned resource without write access, which is
itself a fail-closed grant.

Determinism is preserved by construction:

- Every state mutation is an **idempotent upsert keyed by the directive
  fingerprint**. Replaying the same directive yields the same row, so retries
  and replay never double-apply.
- **No auto-generated keys.** A state key arrives in the directive (or derives
  from it), never from ``rowid``/autoincrement, so replay rebuilds identical
  content.
- A single connection owns writes to a store (opened ``WAL``), so ordering is
  deterministic.

The trajectory is append-only and **write-once**: its primary key is the
``correlation_id``, so a repeated event fails closed with an explicit
``StoreError`` rather than silently duplicating—matching the protocol's
correlation-id-reuse rule.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class StoreError(RuntimeError):
    """A durable store operation failed (fail-closed)."""


def _canonic(value: Any) -> str:
    """A stable JSON rendering (sorted keys) of a payload.

    Mirrors the protocol fingerprint: identical content maps to an identical
    string, so determinism and replay are preserved.
    """
    return json.dumps(value, sort_keys=True, default=str)


class _Base:
    """Shared SQLite lifecycle for the durable stores."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def open(self, *, read_only: bool = False) -> None:
        """Create (or recurse broken) parent dirs and open the store.

        A store may be opened read-only—e.g. a child reading a parent-provisioned
        state file. A trajectory is always append-write.
        """
        if read_only:
            if not self._path.exists():
                raise StoreError(f"read-only store {self._path} does not exist")
            uri = f"file:{self._path.as_posix()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()
        self._conn.commit()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _create_schema(self) -> None:
        raise NotImplementedError

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("store is not open")
        return self._conn

    def __enter__(self) -> _Base:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class StateStore(_Base):
    """A durable, deterministic key/value state store (single writer)."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._read_only = False

    def open(self, *, read_only: bool = False) -> None:
        self._read_only = read_only
        super().open(read_only=read_only)

    def _create_schema(self) -> None:
        conn = self._require()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS state ("
            "  key TEXT PRIMARY KEY,"
            "  value TEXT NOT NULL,"          # JSON-encoded
            "  write_fingerprint TEXT NOT NULL"
            ")"
        )

    def set(self, key: str, value: Any, *, fingerprint: str) -> None:
        """Idempotently set ``key`` — durable once per directive.

        A write is applied once per (key, fingerprint): replaying the same
        directive reuses the existing row rather than overwriting; a different
        directive updating the same key applies a genuine update. Idempotent
        retries and deterministic replay are thereby safe.
        """
        if self._read_only:
            raise StoreError("state store is read-only (cannot write)")
        conn = self._require()
        encoded = _canonic(value)
        row = conn.execute(
            "SELECT write_fingerprint FROM state WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO state (key, value, write_fingerprint) VALUES (?, ?, ?)",
                (key, encoded, fingerprint),
            )
        elif row[0] != fingerprint:
            conn.execute(
                "UPDATE state SET value = ?, write_fingerprint = ? WHERE key = ?",
                (encoded, fingerprint, key),
            )
        # else: same key, same fingerprint -> idempotent reapply, no change.
        conn.commit()

    def get(self, key: str) -> Any | None:
        """Return the stored value for ``key`` (not found -> None)."""
        conn = self._require()
        row = conn.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def count(self) -> int:
        conn = self._require()
        (n,) = conn.execute("SELECT COUNT(*) FROM state").fetchone()
        return int(n)


class TrajectoryStore(_Base):
    """An append-only, write-once audit record for one agent's local chain."""

    def _create_schema(self) -> None:
        conn = self._require()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "  correlation_id TEXT PRIMARY KEY,"
            "  kind TEXT NOT NULL,"          # response kind
            "  node TEXT NOT NULL,"
            "  verified INTEGER NOT NULL,"
            "  value TEXT,"                  # JSON-encoded verified value
            "  error TEXT,"
            "  source TEXT NOT NULL DEFAULT '',"
            "  fingerprint TEXT NOT NULL,"
            "  parent TEXT NOT NULL DEFAULT ''"
            ")"
        )

    def record(
        self,
        *,
        correlation_id: str,
        kind: str,
        node: str,
        verified: bool,
        value: Any = None,
        error: str | None = None,
        source: str = "",
        fingerprint: str = "",
        parent: str = "",
    ) -> None:
        """Append an audit event, failing-closed on correlation-id reuse."""
        conn = self._require()
        row = conn.execute(
            "SELECT 1 FROM events WHERE correlation_id = ?", (correlation_id,)
        ).fetchone()
        if row is not None:
            raise StoreError(
                f"correlation id {correlation_id!r} already recorded "
                "(trajectory is write-once)"
            )
        conn.execute(
            "INSERT INTO events (correlation_id, kind, node, verified, value, "
            "error, source, fingerprint, parent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                correlation_id,
                kind,
                node,
                1 if verified else 0,
                _canonic(value) if value is not None else None,
                error,
                source,
                fingerprint,
                parent,
            ),
        )
        conn.commit()

    def all(self) -> tuple[dict[str, Any], ...]:
        """Return every event, JSON-ready, ordered by correlation id."""
        conn = self._require()
        rows = conn.execute(
            "SELECT correlation_id, kind, node, verified, value, error, source, "
            "fingerprint, parent FROM events ORDER BY correlation_id"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for cid, kind, node, verified, value, error, source, fp, parent in rows:
            out.append(
                {
                    "correlation_id": cid,
                    "kind": kind,
                    "node": node,
                    "verified": bool(verified),
                    "value": json.loads(value) if value is not None else None,
                    "error": error,
                    "source": source,
                    "fingerprint": fp,
                    "parent": parent,
                }
            )
        return tuple(out)

    def count(self) -> int:
        conn = self._require()
        (n,) = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(n)


def open_state(path: str | Path, *, read_only: bool = False) -> StateStore:
    st = StateStore(path)
    st.open(read_only=read_only)
    return st


def open_trajectory(path: str | Path) -> TrajectoryStore:
    tr = TrajectoryStore(path)
    tr.open()
    return tr