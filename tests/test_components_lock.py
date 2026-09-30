"""Tests for the ``components.lock/v1`` contract."""

from __future__ import annotations

import pytest

from agent_centric.contracts.components_lock import (
    LOCK_SCHEMA,
    ComponentsLock,
    LockEntry,
    LockError,
)

_SHA = "c" * 64
_COMMIT = "d" * 40


def _entry(name: str) -> LockEntry:
    return LockEntry(
        name=name,
        repo_url=f"ssh://git@localhost/{name}.git",
        commit_sha=_COMMIT,
        tree_sha256=_SHA,
        implements=("component.v1",),
        signature="sig",
    )


def _lock() -> ComponentsLock:
    return ComponentsLock(
        root="shell",
        entries=(_entry("shell"), _entry("counter")),
        harness_contracts=("component.v1", "components.lock.v1"),
    )


class TestComponentsLock:
    def test_defaults_schema(self) -> None:
        assert _lock().schema == LOCK_SCHEMA

    def test_lock_hash_is_deterministic(self) -> None:
        assert _lock().lock_hash() == _lock().lock_hash()

    def test_lock_hash_changes_with_content(self) -> None:
        a = ComponentsLock(
            root="shell",
            entries=(_entry("shell"),),
            harness_contracts=("component.v1",),
        )
        b = ComponentsLock(
            root="shell",
            entries=(_entry("shell"), _entry("counter")),
            harness_contracts=("component.v1",),
        )
        assert a.lock_hash() != b.lock_hash()

    def test_unique_names(self) -> None:
        with pytest.raises(LockError):
            ComponentsLock(
                root="shell",
                entries=(_entry("shell"), _entry("shell")),
                harness_contracts=("component.v1",),
            )

    def test_root_must_be_present(self) -> None:
        with pytest.raises(LockError):
            ComponentsLock(
                root="missing",
                entries=(_entry("shell"),),
                harness_contracts=("component.v1",),
            )

    def test_entry_lookup(self) -> None:
        assert _lock().entry("counter").name == "counter"
        with pytest.raises(LockError):
            _lock().entry("nope")

    def test_roundtrip(self) -> None:
        lock = _lock()
        again = ComponentsLock.from_dict(lock.to_dict())
        assert again.lock_hash() == lock.lock_hash()

    def test_malformed_entry_rejected(self) -> None:
        bad = {
            "schema": LOCK_SCHEMA,
            "harness_contracts": ["component.v1"],
            "root": "shell",
            "entries": [{"name": "shell", "repo_url": "x", "commit_sha": "nope",
                         "tree_sha256": _SHA, "implements": ["component.v1"]}],
        }
        with pytest.raises(LockError):
            ComponentsLock.from_dict(bad)
