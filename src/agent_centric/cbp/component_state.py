"""Component SQLite state materialization (SPEC-0007 §3.1, Phase 1b).

A component's ``component.v1`` manifest may declare a **state descriptor**
(``{"kind": "sqlite", "path": ..., "single_writer": true}``): the self-contained,
single-writer local state a component owns. This module *materializes* that
state deterministically and **fails closed** on anything unsafe.

Invariants (SPEC-0007 §7/§8):

- **Path safety.** ``path`` is relative to a caller-supplied state root; an
  absolute path or any ``..`` traversal is refused, and the resolved target must
  remain inside the state root. State is *mutable*, so it must live outside the
  immutable, content-addressed bundle cache.
- **Single writer.** The descriptor contract already mandates
  ``single_writer=True``; the store is opened ``WAL`` with one connection.
- **Integrity, fail-closed.** A newly created database is ``integrity_check``\\ ed
  before it is published, and a pre-existing database is verified on open: a
  corrupt file is an explicit :class:`StateError`, never a silent success.
- **Atomic publish.** A fresh database is built in a temp file in the same
  directory and moved into place with ``os.replace``, so a crash never leaves a
  half-written state file.

Determinism note (honest): materialization is **idempotent** and produces the
same *logical* (empty, valid, WAL) database every time; it does not promise
byte-identical files, because SQLite's file header carries change counters.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..contracts.component import StateDescriptor


class StateError(Exception):
    """A component's state could not be materialized or verified (fail-closed)."""


class StateSovereigntyError(StateError):
    """A component attempted to reach state outside its own namespace."""


def state_path(descriptor: StateDescriptor, root: str | Path) -> Path:
    """Resolve ``descriptor.path`` safely under ``root`` (fail-closed).

    Raises:
        StateError: If the path is absolute, contains ``..``, or resolves
            outside ``root`` (e.g. via a symlink).
    """
    raw = PurePosixPath(descriptor.path)
    if raw.is_absolute() or ".." in raw.parts:
        raise StateError(f"unsafe state path {descriptor.path!r}")
    base = Path(root).resolve()
    target = (base / descriptor.path).resolve()
    if target != base and base not in target.parents:
        raise StateError(f"state path {descriptor.path!r} escapes the state root")
    return target


def check_integrity(path: str | Path) -> None:
    """Verify a SQLite database passes ``PRAGMA integrity_check`` (fail-closed).

    Raises:
        StateError: If the file is not a readable, valid SQLite database.
    """
    target = Path(path)
    if not target.is_file():
        raise StateError(f"state database {target} does not exist")
    uri = f"file:{target.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - connect rarely raises
        raise StateError(f"cannot open state database {target}: {exc}") from exc
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise StateError(f"state database {target} is corrupt: {exc}") from exc
    finally:
        conn.close()
    if row is None or row[0] != "ok":
        detail = row[0] if row else "no result"
        raise StateError(f"state database {target} failed integrity check: {detail}")


def _create_wal_database(path: Path) -> None:
    """Create a fresh, valid, WAL SQLite database at ``path``."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.commit()
    finally:
        conn.close()


def materialize_state(descriptor: StateDescriptor, root: str | Path) -> Path:
    """Materialize the component's SQLite state, atomically and idempotently.

    Returns the absolute state file path. An existing database is verified and
    returned unchanged; a missing one is created atomically. Any unsafe path or
    corrupt existing database is an explicit :class:`StateError`.

    Raises:
        StateError: On an unsafe path or a corrupt existing database.
    """
    target = state_path(descriptor, root)
    if target.exists():
        check_integrity(target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        _create_wal_database(tmp)
        check_integrity(tmp)
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        _fsync_dir(target.parent)
    finally:
        for sidecar in (
            tmp,
            tmp.with_name(tmp.name + "-wal"),
            tmp.with_name(tmp.name + "-shm"),
        ):
            with contextlib.suppress(OSError):
                sidecar.unlink(missing_ok=True)
    return target


def connect_state(
    descriptor: StateDescriptor, root: str | Path, *, read_only: bool = False
) -> sqlite3.Connection:
    """Open the component's state (materializing it first unless read-only).

    A read-only open requires the database to already exist and be valid. A
    writable open materializes it (if absent) and enables WAL.
    """
    if read_only:
        target = state_path(descriptor, root)
        check_integrity(target)
        return sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    target = materialize_state(descriptor, root)
    conn = sqlite3.connect(target)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@dataclass(frozen=True)
class StateScope:
    """A component's exclusive, namespaced view of the shared state root.

    Each component owns exactly one namespace (``root/<owner>``). The scope is
    the sanctioned way to reach a component's state: it binds the owner once and
    refuses any descriptor or path that would escape that namespace, fail-closed.
    Two distinct owners can therefore never resolve to the same state file — the
    structural form of the ABM sovereignty invariant (a child owns its own state;
    siblings never write each other; effects propagate as verified proposals).
    """

    root: Path
    owner: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        if (
            not self.owner
            or self.owner in (".", "..")
            or "/" in self.owner
            or "\\" in self.owner
            or PurePosixPath(self.owner).is_absolute()
        ):
            raise StateSovereigntyError(
                f"unsafe component owner {self.owner!r} for a state scope"
            )

    @property
    def namespace(self) -> Path:
        """The directory owned exclusively by this component."""
        return self.root / self.owner

    def owns(self, path: str | Path) -> bool:
        """True iff ``path`` resolves inside this scope's namespace."""
        target = Path(path).resolve()
        namespace = self.namespace.resolve()
        return target == namespace or namespace in target.parents

    def assert_owns(self, path: str | Path) -> None:
        """Refuse a path outside this scope's namespace (fail-closed)."""
        if not self.owns(path):
            raise StateSovereigntyError(
                f"component {self.owner!r} may not reach state {path!r} "
                "outside its own namespace (fail-closed)"
            )

    def path(self, descriptor: StateDescriptor) -> Path:
        """Resolve the component's own state file under its namespace."""
        return state_path(descriptor, self.namespace)

    def materialize(self, descriptor: StateDescriptor) -> Path:
        """Materialize the component's own state (atomic, verified)."""
        return materialize_state(descriptor, self.namespace)

    def connect(
        self, descriptor: StateDescriptor, *, read_only: bool = False
    ) -> sqlite3.Connection:
        """Open only this component's own state connection."""
        return connect_state(descriptor, self.namespace, read_only=read_only)


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a directory so a rename is durable."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform-dependent
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - platform-dependent
        pass
    finally:
        os.close(fd)
