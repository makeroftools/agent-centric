"""Fail-closed / lifecycle coverage for the durable directive ledger.

``DirectiveLedger`` is the crash-safe persistence behind replay/audit. Its
invariants are mission-critical:

- a directive is append-once (duplicate ``correlation_id`` fails closed with a
  ``StoreError``, never a silent double-write),
- nothing writes until the ledger is opened, and nothing writes after close
  (``_require`` guards every operation),
- open/close via the context manager is symmetric, and reopening loads the next
  sequence and sees every prior row (so replay is deterministic),
- the callable manifest round-trips and is deterministic (ordered by name),
- the ledger's parent directory is created only on explicit open (a grant).

All tests are deterministic and offline, on a temp path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.fbp.ledger import DirectiveLedger
from agent_centric.fbp.store import StoreError


class TestLifecycle:
    def test_creates_on_explicit_open(self, tmp_path: Path) -> None:
        path = tmp_path / "dirs" / "session.db"
        with DirectiveLedger(path) as ledger:
            assert ledger.connected is True
        assert path.exists()

    def test_context_manager_closes(self, tmp_path: Path) -> None:
        ledger = DirectiveLedger(tmp_path / "l.db")
        with ledger as open_ledger:
            assert open_ledger.connected is True
        assert ledger.connected is False

    def test_require_fails_when_closed(self, tmp_path: Path) -> None:
        ledger = DirectiveLedger(str(tmp_path / "l.db"))
        with pytest.raises(StoreError):
            ledger.append(correlation_id="c1", kind="run", payload={})


class TestAppendOnce:
    def test_append_then_duplicate_fails_closed(self, tmp_path: Path) -> None:
        with DirectiveLedger(str(tmp_path / "l.db")) as ledger:
            seq = ledger.append(correlation_id="c1", kind="double", payload={"value": 21})
            assert seq == 1
            assert ledger.count() == 1
            with pytest.raises(StoreError, match="already in the directive ledger"):
                ledger.append(correlation_id="c1", kind="double", payload={"value": 21})


class TestOutcomeAndCallables:
    def test_set_outcome_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "l.db"
        with DirectiveLedger(str(path)) as ledger:
            ledger.append(correlation_id="c1", kind="double", payload={"value": 21})
            ledger.set_outcome(correlation_id="c1", outcome={"value": 42, "verified": True})
        # Reopen: the outcome is durable and rows survive.
        with DirectiveLedger(str(path)) as ledger:
            rows = ledger.all()
            assert ledger.count() == 1
            assert rows[0]["correlation_id"] == "c1"
            assert rows[0]["response"] == {"value": 42, "verified": True}
            assert rows[0]["_child"] is False

    def test_child_flag_round_trips(self, tmp_path: Path) -> None:
        with DirectiveLedger(str(tmp_path / "l.db")) as ledger:
            ledger.append(correlation_id="c2", kind="cfg", payload={}, child=True)
            rows = ledger.all()
            assert rows[0]["_child"] is True

    def test_callable_manifest_round_trip_deterministic(self, tmp_path: Path) -> None:
        path = tmp_path / "l.db"
        with DirectiveLedger(str(path)) as ledger:
            ledger.record_callable(name="b", source_url="s://b", module="m", qualname="q")
            ledger.record_callable(name="a", source_url="s://a")
            manifest = ledger.callables()
        assert list(manifest) == ["a", "b"]  # ordered by name
        with DirectiveLedger(str(path)) as ledger:
            assert ledger.callables()["a"]["module"] == ""


class TestSequenceResume:
    def test_reopen_continues_sequence(self, tmp_path: Path) -> None:
        path = tmp_path / "l.db"
        with DirectiveLedger(str(path)) as ledger:
            ledger.append(correlation_id="c1", kind="x", payload={})
        with DirectiveLedger(str(path)) as ledger:
            seq = ledger.append(correlation_id="c2", kind="x", payload={})
            assert seq == 2  # resumed from the prior max seq
            assert ledger.count() == 2