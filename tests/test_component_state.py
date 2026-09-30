"""Tests for component SQLite state materialization (SPEC-0007 Phase 1b)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_centric.contracts.component import StateDescriptor
from agent_centric.fbp.component_state import (
    StateError,
    check_integrity,
    connect_state,
    materialize_state,
    state_path,
)


def _desc(path: str = "state/bills.db") -> StateDescriptor:
    return StateDescriptor(kind="sqlite", path=path, single_writer=True)


class TestStatePath:
    def test_resolves_under_root(self, tmp_path: Path) -> None:
        target = state_path(_desc("state/bills.db"), tmp_path)
        assert target == (tmp_path / "state" / "bills.db").resolve()

    def test_absolute_path_refused(self, tmp_path: Path) -> None:
        with pytest.raises(StateError):
            state_path(_desc("/etc/passwd"), tmp_path)

    def test_traversal_refused(self, tmp_path: Path) -> None:
        with pytest.raises(StateError):
            state_path(_desc("../outside.db"), tmp_path)
        with pytest.raises(StateError):
            state_path(_desc("state/../../outside.db"), tmp_path)

    def test_symlink_escape_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "root"
        root.mkdir()
        (root / "link").symlink_to(outside)
        with pytest.raises(StateError):
            state_path(_desc("link/db.sqlite"), root)


class TestMaterialize:
    def test_creates_valid_wal_database(self, tmp_path: Path) -> None:
        target = materialize_state(_desc(), tmp_path)
        assert target.is_file()
        check_integrity(target)
        conn = sqlite3.connect(target)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        first = materialize_state(_desc(), tmp_path)
        second = materialize_state(_desc(), tmp_path)
        assert first == second
        check_integrity(second)

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = materialize_state(_desc("deep/nested/state.db"), tmp_path)
        assert target.parent.is_dir()

    def test_single_writer_survives_write(self, tmp_path: Path) -> None:
        desc = _desc()
        target = materialize_state(desc, tmp_path)
        conn = connect_state(desc, tmp_path)
        try:
            conn.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO t (k) VALUES ('a')")
            conn.commit()
        finally:
            conn.close()
        check_integrity(target)

    def test_corrupt_existing_fails_closed(self, tmp_path: Path) -> None:
        target = tmp_path / "state" / "bills.db"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"not a sqlite database")
        with pytest.raises(StateError):
            materialize_state(_desc(), tmp_path)
        with pytest.raises(StateError):
            check_integrity(target)

    def test_read_only_requires_existing(self, tmp_path: Path) -> None:
        with pytest.raises(StateError):
            connect_state(_desc(), tmp_path, read_only=True)

    def test_read_only_opens_existing(self, tmp_path: Path) -> None:
        materialize_state(_desc(), tmp_path)
        conn = connect_state(_desc(), tmp_path, read_only=True)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
