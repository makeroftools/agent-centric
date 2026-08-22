"""A durable, append-only directive ledger for the FBP driver.

The driver's in-memory ``_ledger`` is the inputs to deterministic replay; but it
dies with the process. This module persists that ledger so a later process can
reopen a session and replay (re-verify) it after the fact — crash-safe,
recoverable re-verification.

Design (matches the store conventions):

- Append-only, ordermaintained by a sequence: directives are appended in issue
  order, and same-order reconstruction is deterministic (sort by ``seq``).
- Each row is a directive (kind + payload) plus its terminal outcome once known.
  A directive is identified by its full fingerprint (correlation id + kind +
  canonic payload), per the protocol's idempotency rule — a replayed directive
  returns its cached outcome rather than double-applying.
- Payloads/outcomes are JSON-encoded. The ledger is an explicit, parent-chosen
  grant (like a state or trajectory store): the driver only opens one when given
  a path — never silently.

Determinism is preserved by construction: no auto-generated content beyond the
monotonic sequence used purely for order; identical directives produce
identical rows, so a reopened ledger replays identically.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .store import StoreError


class DirectiveLedger:
    """An append-only SQLite ledger of directives issued by a driver session.

    Attributes:
        _path: The ledger file path.
        _conn: The owning connection (single writer).
        _seq: The highest sequence id written (for next append).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._seq = 0

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Create (or reopen) the ledger and load the current sequence."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()
        self._conn.commit()
        (self._seq,) = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM directives"
        ).fetchone()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> DirectiveLedger:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    # -- helpers -------------------------------------------------------------

    def _create_schema(self) -> None:
        conn = self._require()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS directives ("
            "  seq INTEGER PRIMARY KEY,"
            "  correlation_id TEXT NOT NULL UNIQUE,"
            "  kind TEXT NOT NULL,"                 # directive kind
            "  payload TEXT NOT NULL,"              # JSON-encoded directive payload
            "  response TEXT,"                     # JSON-encoded terminal outcome, if known
            "  _child INTEGER NOT NULL DEFAULT 0"   # 1 for synthetic child-configure
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS callables ("
            "  name TEXT PRIMARY KEY,"
            "  source_url TEXT NOT NULL DEFAULT '',"
            "  module TEXT NOT NULL DEFAULT '',"
            "  qualname TEXT NOT NULL DEFAULT ''"
            ")"
        )

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("directive ledger is not open")
        return self._conn

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _loads(text: str | None) -> Any:
        return json.loads(text) if text is not None else None

    # -- writes --------------------------------------------------------------

    def append(
        self,
        *,
        correlation_id: str,
        kind: str,
        payload: dict[str, Any],
        child: bool = False,
    ) -> int:
        """Append a directive in issue order; returns its sequence id.

        ``correlation_id`` is unique, so a replayed append of the same directive
        fails closed rather than double-appending.
        """
        conn = self._require()
        self._seq += 1
        try:
            conn.execute(
                "INSERT INTO directives (seq, correlation_id, kind, payload, _child) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    self._seq,
                    correlation_id,
                    kind,
                    self._dumps(payload),
                    1 if child else 0,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(
                f"correlation id {correlation_id!r} already in the directive ledger "
                "(a directive is append-once)"
            ) from exc
        conn.commit()
        return self._seq

    def set_outcome(self, *, correlation_id: str, outcome: dict[str, Any]) -> None:
        """Record the terminal outcome for a directive already appended."""
        conn = self._require()
        conn.execute(
            "UPDATE directives SET response = ? WHERE correlation_id = ?",
            (self._dumps(outcome), correlation_id),
        )
        conn.commit()

    def record_callable(
        self, *, name: str, source_url: str = "", module: str = "", qualname: str = ""
    ) -> None:
        """Record a registered callable in the ledger's registry manifest.

        The registry manifest lets a later process re-register the same callable
        names (by source) so cross-process replay can re-resolve directives. The
        callable itself cannot cross the wire (JSON), so a replay must seed the
        same callables — the manifest records *what* to seed and from where
        (``module``/``qualname`` give an importable source).
        """
        conn = self._require()
        conn.execute(
            "INSERT OR REPLACE INTO callables (name, source_url, module, qualname) "
            "VALUES (?, ?, ?, ?)",
            (name, source_url, module, qualname),
        )
        conn.commit()

    def callables(self) -> dict[str, dict[str, str]]:
        """Return the registry manifest: {name: {source_url, module, qualname}}.

        Ordered by name for determinism.
        """
        conn = self._require()
        rows = conn.execute(
            "SELECT name, source_url, module, qualname FROM callables ORDER BY name"
        ).fetchall()
        out: dict[str, dict[str, str]] = {}
        for name, source_url, module, qualname in rows:
            out[name] = {
                "source_url": source_url,
                "module": module,
                "qualname": qualname,
            }
        return out

    # -- reads ---------------------------------------------------------------

    def all(self) -> tuple[dict[str, Any], ...]:
        """Return every directive in issue order (JSON-ready, same order)."""
        conn = self._require()
        rows = conn.execute(
            "SELECT seq, correlation_id, kind, payload, response, _child "
            "FROM directives ORDER BY seq"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for _seq, cid, kind, payload, response, child in rows:
            out.append(
                {
                    "seq": _seq,
                    "correlation_id": cid,
                    "kind": kind,
                    "payload": self._loads(payload),
                    "response": self._loads(response),
                    "_child": bool(child),
                }
            )
        return tuple(out)

    def count(self) -> int:
        conn = self._require()
        (n,) = conn.execute("SELECT COUNT(*) FROM directives").fetchone()
        return int(n)