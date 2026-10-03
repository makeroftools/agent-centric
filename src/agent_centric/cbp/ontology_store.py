"""Authored-fact store (SPEC-0023 M3) — component-owned ABox state.

A component may publish its own **authored/instance facts**. The store keeps them
as an **append-only canonical assertion log** — the source of truth — and offers
a **derived, disposable SQLite index** (materialized through
:mod:`agent_centric.cbp.component_state`) plus a **disposable closure cache**.

Invariants (SPEC-0023 §6/§10):

- **Single writer, sovereign.** The store is bound to one :class:`StateScope`
  owner; it refuses any path outside that namespace (fail-closed). Siblings never
  write each other.
- **Content addressed from the log, never the DB.** A graph's address is
  recomputed from the canonical assertion records; SQLite (whose file bytes are
  not stable) is only ever a cache.
- **Idempotent.** Appending the same assertion twice is a no-op, so a retried
  publish never duplicates facts. The log is advisory-locked and ``fsync``-ed,
  and materialized ``0600``.
- **Fail-closed.** A malformed or non-canonical log line is an explicit error;
  bounds are enforced before materialization.

The log is the recommended source of truth (§10); the index is rebuildable and
never trusted over the log.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from ..contracts.closure import Closure
from ..contracts.component import StateDescriptor
from ..contracts.ontology import (
    MAX_CLOSURE_ASSERTIONS,
    Assertion,
    ErrorKind,
    Ontology,
    OntologyError,
    SemanticGraph,
    _assertion_key,
    _canonical_bytes,
    object_from_dict,
)
from .component_state import StateError, StateScope

LOG_NAME = "assertions.jsonl"


def _lock_file(handle: TextIO) -> None:
    """Take an exclusive advisory lock (POSIX); a no-op where unavailable."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: TextIO) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class OntologyStore:
    """An append-only, idempotent, content-addressed authored-fact store.

    Bound to one :class:`StateScope` (the owning component's namespace) and one
    :class:`StateDescriptor` (the derived SQLite index).
    """

    def __init__(
        self,
        scope: StateScope,
        descriptor: StateDescriptor,
        *,
        log_name: str = LOG_NAME,
    ) -> None:
        if (
            not log_name
            or "/" in log_name
            or "\\" in log_name
            or log_name in (".", "..")
        ):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                f"unsafe assertion log name {log_name!r} (fail-closed)",
            )
        self._scope = scope
        self._descriptor = descriptor
        self._path = scope.namespace / log_name
        scope.assert_owns(self._path)

    @property
    def owner(self) -> str:
        return self._scope.owner

    @property
    def path(self) -> Path:
        return self._path

    # -- the canonical log (source of truth) --------------------------------

    def append(self, assertion: Assertion) -> bool:
        """Append one assertion idempotently; return True iff it was new."""
        line = _assertion_key(assertion).decode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a+", encoding="utf-8") as handle:
            _lock_file(handle)
            try:
                handle.seek(0)
                for existing in handle.read().splitlines():
                    if existing == line:
                        return False
                handle.seek(0, os.SEEK_END)
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                _unlock_file(handle)
        os.chmod(self._path, 0o600)
        return True

    def append_all(self, assertions: Iterable[Assertion]) -> int:
        """Append many assertions idempotently; return the number that were new."""
        return sum(1 for assertion in assertions if self.append(assertion))

    def assertions(self) -> tuple[Assertion, ...]:
        """The canonical, deduplicated, sorted assertions in the log."""
        if not self._path.exists():
            return ()
        unique: dict[bytes, Assertion] = {}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OntologyError(
                    ErrorKind.MALFORMED_DOCUMENT,
                    f"malformed assertion log line: {exc}",
                ) from exc
            assertion = Assertion(
                s=str(data["s"]),
                p=str(data["p"]),
                o=object_from_dict(data["o"]),
            )
            if _assertion_key(assertion) != line.encode("utf-8"):
                raise OntologyError(
                    ErrorKind.MALFORMED_DOCUMENT,
                    "non-canonical assertion log line (fail-closed)",
                )
            unique[_assertion_key(assertion)] = assertion
        if len(unique) > MAX_CLOSURE_ASSERTIONS:
            raise OntologyError(
                ErrorKind.CLOSURE_OVERFLOW,
                f"authored log has {len(unique)} assertions, exceeding "
                f"{MAX_CLOSURE_ASSERTIONS} (fail-closed)",
            )
        return tuple(unique[key] for key in sorted(unique))

    def count(self) -> int:
        return len(self.assertions())

    def content_hash(self) -> str:
        """The content address of the authored facts (from the log, not the DB)."""
        payload = [assertion.to_dict() for assertion in self.assertions()]
        return hashlib.sha256(_canonical_bytes(payload)).hexdigest()

    def graph(self, scope_name: str | None = None) -> SemanticGraph:
        """The authored ABox as a canonical :class:`SemanticGraph`."""
        return SemanticGraph(
            scope=scope_name or self.owner,
            assertions=self.assertions(),
            sources=(("assertion_log_sha256", self.content_hash()),),
        )

    def closure(self, ontology: Ontology) -> Closure:
        """Materialize the pinned entailment closure over the authored facts."""
        from .ontology_runtime import compute_closure

        return compute_closure(self.graph(), ontology)

    # -- the derived index (disposable cache) --------------------------------

    def index(self) -> Path:
        """Rebuild the derived SQLite index from the canonical log (idempotent)."""
        path = self._scope.materialize(self._descriptor)
        graph_hash = self.graph().graph_hash()
        conn = self._scope.connect(self._descriptor)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assertions "
                "(s TEXT NOT NULL, p TEXT NOT NULL, o TEXT NOT NULL, "
                "graph TEXT NOT NULL, PRIMARY KEY (s, p, o, graph))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assertion_meta "
                "(assertion_id TEXT PRIMARY KEY, source TEXT, content_addr TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS imports "
                "(graph TEXT, imported TEXT, ontology_hash TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS closure_cache "
                "(graph_hash TEXT, ontology_hash TEXT, profile TEXT, "
                "closure_hash TEXT, PRIMARY KEY (graph_hash, ontology_hash, profile))"
            )
            conn.execute("DELETE FROM assertions")
            conn.execute("DELETE FROM assertion_meta")
            rows = []
            meta = []
            for assertion in self.assertions():
                rows.append(
                    (
                        assertion.s,
                        assertion.p,
                        _canonical_bytes(assertion.o.to_dict()).decode("utf-8"),
                        graph_hash,
                    )
                )
                meta.append(
                    (
                        _assertion_key(assertion).hex(),
                        self.owner,
                        self.content_hash(),
                    )
                )
            conn.executemany(
                "INSERT OR IGNORE INTO assertions (s, p, o, graph) VALUES (?, ?, ?, ?)",
                rows,
            )
            conn.executemany(
                "INSERT OR IGNORE INTO assertion_meta "
                "(assertion_id, source, content_addr) VALUES (?, ?, ?)",
                meta,
            )
            conn.commit()
        finally:
            conn.close()
        return path

    def verify_index(self) -> None:
        """Fail closed if the derived index disagrees with the canonical log."""
        target = self._scope.path(self._descriptor)
        if not target.exists():
            raise StateError(f"no index at {target} (fail-closed)")
        graph_hash = self.graph().graph_hash()
        expected = sorted(
            (
                assertion.s,
                assertion.p,
                _canonical_bytes(assertion.o.to_dict()).decode("utf-8"),
                graph_hash,
            )
            for assertion in self.assertions()
        )
        conn = self._scope.connect(self._descriptor, read_only=True)
        try:
            rows = [
                tuple(row)
                for row in conn.execute(
                    "SELECT s, p, o, graph FROM assertions ORDER BY s, p, o, graph"
                ).fetchall()
            ]
        finally:
            conn.close()
        if rows != expected:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                "the derived assertion index disagrees with the canonical log "
                "(fail-closed)",
            )

    def cache_closure(self, closure: Closure) -> None:
        """Record a closure in the disposable cache, if it matches this graph."""
        graph_hash = self.graph().graph_hash()
        if closure.graph_hash != graph_hash:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                "refusing to cache a closure for a different graph (fail-closed)",
            )
        conn = self._scope.connect(self._descriptor)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO closure_cache "
                "(graph_hash, ontology_hash, profile, closure_hash) VALUES (?, ?, ?, ?)",
                (
                    closure.graph_hash,
                    closure.ontology_hash,
                    closure.profile,
                    closure.closure_hash(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def cached_closure_hash(
        self, ontology_hash: str, profile: str
    ) -> str | None:
        """Look up a cached closure hash (a disposable hint, never a decision)."""
        graph_hash = self.graph().graph_hash()
        conn = self._scope.connect(self._descriptor, read_only=True)
        try:
            row = conn.execute(
                "SELECT closure_hash FROM closure_cache "
                "WHERE graph_hash = ? AND ontology_hash = ? AND profile = ?",
                (graph_hash, ontology_hash, profile),
            ).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row else None
