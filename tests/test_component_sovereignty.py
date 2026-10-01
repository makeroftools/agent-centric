"""Tests for component state sovereignty (SPEC-0009 / SPEC-0007 §8).

A component owns its own namespaced state; siblings never write each other.
``StateScope`` is the sanctioned accessor: it binds a component to one namespace
under the shared state root and refuses any descriptor or path that escapes it,
fail-closed. Effects therefore propagate as verified proposals (data returned
upward), never as a child mutating a parent's or sibling's state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.cbp.component_state import (
    StateError,
    StateScope,
    StateSovereigntyError,
    check_integrity,
)
from agent_centric.contracts.component import StateDescriptor


def _desc(path: str = "state/bills.db") -> StateDescriptor:
    return StateDescriptor(kind="sqlite", path=path, single_writer=True)


class TestStateScope:
    def test_state_is_namespaced_by_owner(self, tmp_path: Path) -> None:
        scope = StateScope(tmp_path, "alpha")
        target = scope.materialize(_desc())
        assert target == (tmp_path / "alpha" / "state" / "bills.db").resolve()
        check_integrity(target)

    def test_siblings_have_disjoint_state(self, tmp_path: Path) -> None:
        alpha = StateScope(tmp_path, "alpha").materialize(_desc())
        beta = StateScope(tmp_path, "beta").materialize(_desc())
        assert alpha != beta
        assert alpha.is_file() and beta.is_file()

    def test_traversal_to_a_sibling_is_refused(self, tmp_path: Path) -> None:
        scope = StateScope(tmp_path, "alpha")
        with pytest.raises(StateError):
            scope.path(_desc("../beta/state.db"))

    def test_owns_only_its_own_namespace(self, tmp_path: Path) -> None:
        alpha = StateScope(tmp_path, "alpha")
        beta = StateScope(tmp_path, "beta")
        alpha_file = alpha.materialize(_desc())
        beta_file = beta.materialize(_desc())
        assert alpha.owns(alpha_file)
        assert not alpha.owns(beta_file)
        alpha.assert_owns(alpha_file)
        with pytest.raises(StateSovereigntyError):
            alpha.assert_owns(beta_file)

    def test_writes_never_leak_to_a_sibling(self, tmp_path: Path) -> None:
        alpha = StateScope(tmp_path, "alpha")
        beta = StateScope(tmp_path, "beta")
        alpha.materialize(_desc())
        beta_file = beta.materialize(_desc())
        before = beta_file.read_bytes()
        connection = alpha.connect(_desc())
        try:
            connection.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO t (k) VALUES ('x')")
            connection.commit()
        finally:
            connection.close()
        assert beta_file.read_bytes() == before
        check_integrity(alpha.path(_desc()))
        check_integrity(beta_file)

    def test_unsafe_owner_is_refused(self, tmp_path: Path) -> None:
        for owner in ("", ".", "..", "a/b", "a\\b", "/abs"):
            with pytest.raises(StateSovereigntyError):
                StateScope(tmp_path, owner)

    def test_connect_read_only_isolated(self, tmp_path: Path) -> None:
        alpha = StateScope(tmp_path, "alpha")
        alpha.materialize(_desc())
        connection = alpha.connect(_desc(), read_only=True)
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection.close()
