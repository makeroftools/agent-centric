"""Persistent, append-only Review queue for ``review.v1`` (SPEC-0002 §4, §6).

The queue is the durable counterpart of :mod:`agent_centric.contracts.review`:
an outcome that cannot be made deterministic is enqueued once, and its later
resolution is **appended** as a distinct, content-addressed record — the queue is
never mutated (PRINCIPLES Law 10). Both writes are **idempotent** by content
address, so a retried enqueue or resolution is a no-op and a crash/restart cannot
duplicate evidence.

Order is a monotonic, insertion-order sequence (never a wall-clock time), so
reads are deterministic and reproducible: :meth:`ReviewQueue.pending` returns
items in the exact order they entered the queue.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..contracts.review import (
    DEFAULT_REVIEW_THRESHOLD,
    ReviewItem,
    ReviewResolution,
    ReviewStatus,
    needs_review,
)
from .message import Response


class ReviewError(RuntimeError):
    """A Review queue operation violated the contract (fail-closed)."""


class ReviewQueue:
    """An append-only SQLite Review queue (write-once; idempotent; deterministic)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._seq_items = 0
        self._seq_resolutions = 0

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Create (or reopen) the queue and load the current sequences."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()
        self._conn.commit()
        (self._seq_items,) = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM review_items"
        ).fetchone()
        (self._seq_resolutions,) = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM review_resolutions"
        ).fetchone()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> ReviewQueue:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ReviewError("review queue is not open")
        return self._conn

    def _create_schema(self) -> None:
        conn = self._require()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS review_items ("
            "  seq INTEGER PRIMARY KEY,"
            "  item_hash TEXT NOT NULL UNIQUE,"
            "  correlation_id TEXT NOT NULL,"
            "  domain TEXT NOT NULL,"
            "  confidence REAL NOT NULL,"
            "  provenance TEXT NOT NULL,"
            "  document TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS review_resolutions ("
            "  seq INTEGER PRIMARY KEY,"
            "  resolution_hash TEXT NOT NULL UNIQUE,"
            "  item_hash TEXT NOT NULL,"
            "  resolution TEXT NOT NULL,"
            "  resolver TEXT NOT NULL DEFAULT '',"
            "  document TEXT NOT NULL"
            ")"
        )

    @staticmethod
    def _dumps(value: object) -> str:
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _loads(text: str) -> Any:
        return json.loads(text)

    # -- writes --------------------------------------------------------------

    def enqueue(self, item: ReviewItem) -> tuple[int, bool]:
        """Append ``item`` once, idempotently.

        Returns ``(seq, inserted)``: the sequence id of the item and whether it
        was newly inserted. Re-enqueuing identical content returns the original
        sequence id with ``inserted=False`` (write-once).
        """
        if not isinstance(item, ReviewItem):
            raise ReviewError("enqueue requires a ReviewItem")
        conn = self._require()
        item_hash = item.item_hash()
        row = conn.execute(
            "SELECT seq FROM review_items WHERE item_hash = ?", (item_hash,)
        ).fetchone()
        if row is not None:
            return int(row[0]), False
        self._seq_items += 1
        conn.execute(
            "INSERT INTO review_items "
            "(seq, item_hash, correlation_id, domain, confidence, provenance, document) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self._seq_items,
                item_hash,
                item.correlation_id,
                item.domain,
                float(item.confidence),
                self._dumps(list(item.provenance)),
                self._dumps(item.to_dict()),
            ),
        )
        conn.commit()
        return self._seq_items, True

    def resolve(
        self, item_hash: str, resolution: str, *, resolver: str = ""
    ) -> tuple[int, bool]:
        """Append a resolution for ``item_hash``; fails closed if it is unknown.

        Returns ``(seq, inserted)``; re-resolving with identical content is a
        no-op (idempotent by resolution content address). The item itself is
        never mutated.
        """
        conn = self._require()
        if (
            conn.execute(
                "SELECT 1 FROM review_items WHERE item_hash = ?", (item_hash,)
            ).fetchone()
            is None
        ):
            raise ReviewError(f"cannot resolve unknown review item {item_hash!r}")
        record = ReviewResolution(
            item_hash=item_hash, resolution=resolution, resolver=resolver
        )
        resolution_hash = record.resolution_hash()
        row = conn.execute(
            "SELECT seq FROM review_resolutions WHERE resolution_hash = ?",
            (resolution_hash,),
        ).fetchone()
        if row is not None:
            return int(row[0]), False
        self._seq_resolutions += 1
        conn.execute(
            "INSERT INTO review_resolutions "
            "(seq, resolution_hash, item_hash, resolution, resolver, document) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                self._seq_resolutions,
                resolution_hash,
                item_hash,
                resolution,
                resolver,
                self._dumps(record.to_dict()),
            ),
        )
        conn.commit()
        return self._seq_resolutions, True

    # -- reads ---------------------------------------------------------------

    def items(self) -> tuple[ReviewItem, ...]:
        """Every enqueued item, in insertion order (deterministic)."""
        conn = self._require()
        rows = conn.execute("SELECT document FROM review_items ORDER BY seq").fetchall()
        return tuple(ReviewItem.from_dict(self._loads(doc)) for (doc,) in rows)

    def resolutions(self) -> tuple[ReviewResolution, ...]:
        """Every resolution, in insertion order (deterministic)."""
        conn = self._require()
        rows = conn.execute(
            "SELECT document FROM review_resolutions ORDER BY seq"
        ).fetchall()
        return tuple(ReviewResolution.from_dict(self._loads(doc)) for (doc,) in rows)

    def _resolved_hashes(self) -> frozenset[str]:
        conn = self._require()
        rows = conn.execute(
            "SELECT DISTINCT item_hash FROM review_resolutions"
        ).fetchall()
        return frozenset(str(item_hash) for (item_hash,) in rows)

    def pending(self) -> tuple[ReviewItem, ...]:
        """Items with no appended resolution, in insertion order."""
        resolved = self._resolved_hashes()
        return tuple(item for item in self.items() if item.item_hash() not in resolved)

    def resolved(self) -> tuple[ReviewItem, ...]:
        """Items that have an appended resolution, in insertion order."""
        resolved = self._resolved_hashes()
        return tuple(item for item in self.items() if item.item_hash() in resolved)

    def status(self, item_hash: str) -> str:
        """The derived status of ``item_hash``; fails closed if it is unknown."""
        conn = self._require()
        if (
            conn.execute(
                "SELECT 1 FROM review_items WHERE item_hash = ?", (item_hash,)
            ).fetchone()
            is None
        ):
            raise ReviewError(f"unknown review item {item_hash!r}")
        row = conn.execute(
            "SELECT 1 FROM review_resolutions WHERE item_hash = ?", (item_hash,)
        ).fetchone()
        return ReviewStatus.RESOLVED if row is not None else ReviewStatus.PENDING

    def count(self) -> int:
        conn = self._require()
        (n,) = conn.execute("SELECT COUNT(*) FROM review_items").fetchone()
        return int(n)

    def pending_count(self) -> int:
        return len(self.pending())


def enqueue_for_review(
    queue: ReviewQueue,
    response: Response,
    *,
    domain: str,
    threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> ReviewItem | None:
    """Enqueue ``response`` iff it needs review; return the item or ``None``.

    The provenance is built deterministically from the response's audit
    references (model record, node, source) so a reviewer can reconstruct the
    outcome. A response with no confidence that is unverified is recorded at
    confidence ``0.0`` (honest: absence of confidence is not certainty).
    """
    if not needs_review(
        verified=response.verified,
        confidence=response.confidence,
        threshold=threshold,
    ):
        return None
    provenance: list[str] = []
    if response.model_record:
        provenance.append(f"model-record:{response.model_record}")
    if response.node:
        provenance.append(f"node:{response.node}")
    if response.source:
        provenance.append(f"source:{response.source}")
    item = ReviewItem(
        correlation_id=response.correlation_id,
        domain=domain,
        confidence=0.0 if response.confidence is None else float(response.confidence),
        provenance=tuple(provenance),
    )
    queue.enqueue(item)
    return item
