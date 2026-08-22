"""Tests for deterministic replay (re-verification after the fact)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_centric.fbp import (
    DirectiveLedger,
    FbpDriver,
    register_callable,
    replay_ledger,
)


def _double(value: int) -> int:
    return value * 2


def _triple(value: int) -> int:
    return value * 3


def _even(v) -> bool:
    return isinstance(v, int) and v % 2 == 0


def _odd(v) -> bool:
    return isinstance(v, int) and v % 2 == 1


class TestReplay:
    def test_replay_passes_for_deterministic_run(self) -> None:
        register_callable("double", _double)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            r = driver.run("double", {"value": 21})
            assert r.verified is True and r.value == 42

            result = driver.replay()
            assert result["passed"] is True
            assert result["recorded"] == result["replayed"]
            assert result["recorded"]["terminal"] == "result"
            assert result["recorded"]["terminal_value"] == 42

    def test_replay_by_target_correlation_id(self) -> None:
        register_callable("double", _double)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run("double", {"value": 21})
            # Find the ledger id for the run.
            run_ids = [
                cid
                for cid, d in driver.ledger().items()
                if d["kind"] == "run"
            ]
            assert run_ids
            result = driver.replay(target=run_ids[-1])
            assert result["passed"] is True

    def test_replay_unknown_target_fails_closed(self) -> None:
        register_callable("double", _double)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run("double", {"value": 21})
            result = driver.replay(target="does-not-exist")
            assert result["passed"] is False
            assert "unknown" in result["diff"]

    def test_replay_no_run_recorded(self) -> None:
        with FbpDriver() as driver:
            # No run directive issued.
            result = driver.replay()
            assert result["passed"] is False

    def test_replay_detects_divergence(self) -> None:
        # A task that is not deterministic: returns a value that depends on a
        # mutable seed would diverge; here we register a callable that raises
        # on the second call to force a difference.
        calls = {"n": 0}

        def _flaky(value: int) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                return value * 2
            raise RuntimeError("boom on replay")

        register_callable("flaky", _flaky)
        with FbpDriver() as driver:
            driver.register("flaky", _flaky)
            driver.configure(tasks=("flaky",))
            first = driver.run("flaky", {"value": 21})
            assert first.verified is True and first.value == 42

            result = driver.replay()
            # The fresh run raises, so outcomes differ (or the replay fails).
            assert result["passed"] is False or result["replayed"] is None

    def test_replay_resolves_per_run_verifier(self) -> None:
        """A run whose per-run verifier differs from the root default must
        replay faithfully. Without registering the verifier on the fresh root,
        a verified original would diverge into a spurious verification failure."""
        register_callable("triple", _triple)
        register_callable("odd", _odd)
        register_callable("even", _even)
        with FbpDriver() as driver:
            driver.register("triple", _triple)
            driver.register("odd", _odd)
            driver.register("even", _even)
            # Root default is 'even'; the run overrides to 'odd'. triple(7)=21 is
            # odd, so the run is verified only under 'odd'.
            driver.configure(
                tasks=("triple",), verifiers=("even", "odd"), verifier="even"
            )
            r = driver.run("triple", {"value": 7}, verifier="odd")
            assert r.verified is True and r.value == 21

            result = driver.replay()
            assert result["passed"] is True, result["diff"]


class TestReplaySession:
    """replay_session re-issues the whole recorded directive sequence on a
    fresh tree and verifies every run outcome (including delegated ones)."""

    def test_replays_local_session(self) -> None:
        register_callable("double", _double)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run("double", {"value": 21})
            driver.run("double", {"value": 5})

            result = driver.replay_session()
            assert result["ok"] is True
            assert result["runs"] == 2
            assert result["passed"] == 2
            assert result["failed"] == []

    def test_replays_delegated_child_run(self) -> None:
        register_callable("double", _double)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.spawn("child")
            driver.configure_child("child", tasks=("double",))
            r = driver.run("double", {"value": 21}, child="child")
            assert r.verified is True and r.value == 42

            result = driver.replay_session()
            # The replay must rebuild the child topology and re-run the
            # delegated directive faithfully.
            assert result["ok"] is True
            assert result["runs"] >= 1
            assert result["passed"] == result["runs"]

    def test_divergent_session_flags_failure(self) -> None:
        calls = {"n": 0}

        def _flaky(value: int) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                return value * 2
            raise RuntimeError("boom on replay")

        register_callable("flaky", _flaky)
        with FbpDriver() as driver:
            driver.register("flaky", _flaky)
            driver.configure(tasks=("flaky",))
            driver.run("flaky", {"value": 21})

            result = driver.replay_session()
            # The non-deterministic task diverges on replay and is flagged.
            assert result["ok"] is False
            assert len(result["failed"]) >= 1

    def test_replay_session_resolves_per_run_verifier(self) -> None:
        """replay_session must faithfully rebuild a session that used per-run
        verifiers differing from the root default."""
        register_callable("triple", _triple)
        register_callable("odd", _odd)
        register_callable("even", _even)
        with FbpDriver() as driver:
            driver.register("triple", _triple)
            driver.register("odd", _odd)
            driver.register("even", _even)
            driver.configure(
                tasks=("triple",), verifiers=("even", "odd"), verifier="even"
            )
            r = driver.run("triple", {"value": 7}, verifier="odd")
            assert r.verified is True and r.value == 21

            result = driver.replay_session()
            assert result["ok"] is True, result["failed"]
            assert result["passed"] == result["runs"]

    def test_replay_session_clears_verifier_faithfully(self) -> None:
        """A ``configure(clear_verifier=True)`` must be recorded and reproduced
        on replay, so delegate results are not spuriously demoted."""
        register_callable("double", _double)
        register_callable("even", _even)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.register("even", _even)
            driver.configure(
                tasks=("double",), verifiers=("even",), verifier="even"
            )
            driver.spawn("child")
            driver.configure_child("child", tasks=("double",), verifier=None)
            driver.configure(clear_verifier=True)
            # Without a verifier, a delegated even result is not demoted.
            r = driver.run("double", {"value": 21}, child="child")
            assert r.verified is True and r.node == "child"

            result = driver.replay_session()
            assert result["ok"] is True, result["failed"]
            assert result["passed"] == result["runs"]


class TestReplaySessionStateIsolation:
    """Full-tree replay must isolate on-disk state so stateful trees (e.g.
    bills) replay cleanly and never touch the original store files."""

    def test_stateful_bills_tree_replays_cleanly(self, tmp_path: Path) -> None:
        from agent_centric.fbp.bills_agent import (
            TASK_ACCEPT,
            TASK_CALENDAR,
            TASK_INTAKE,
        )

        registry = tmp_path / "registry.db"
        with FbpDriver() as driver:
            driver.spawn("bills", kind="bills")
            driver.run(
                "bills_setup",
                {"state": str(registry), "store_keys": ["b1"]},
                child="bills",
            )
            draft = driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b1",
                        "vendor": "GasCo",
                        "amount_cents": 12345,
                        "due_date": "2026-10-01",
                    }
                },
                child="bills",
            )
            assert draft.verified is True
            accepted = driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            assert accepted.verified is True
            cal = driver.run(
                TASK_CALENDAR,
                {"from_date": "2026-10-01", "to_date": "2026-10-31"},
                child="bills",
            )
            assert cal.verified is True

            # The stateful tree replays cleanly (every run outcome matches).
            result = driver.replay_session()
            assert result["ok"] is True, result["failed"]
            assert result["runs"] >= 3
            assert result["passed"] == result["runs"]

    def test_replay_does_not_touch_original_store(self, tmp_path: Path) -> None:
        from agent_centric.fbp.bills_agent import (
            TASK_ACCEPT,
            TASK_INTAKE,
        )

        registry = tmp_path / "registry.db"
        with FbpDriver() as driver:
            driver.spawn("bills", kind="bills")
            driver.run(
                "bills_setup",
                {"state": str(registry), "store_keys": ["b1"]},
                child="bills",
            )
            draft = driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b1",
                        "vendor": "GasCo",
                        "amount_cents": 12345,
                        "due_date": "2026-10-01",
                    }
                },
                child="bills",
            )
            driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")

            # Snapshot the original store's row count before replay.
            from agent_centric.fbp import store

            before = store.open_state(registry)
            before_count = before.count()
            before.close()

            driver.replay_session()

            # The original store is untouched: same row count, and the replay
            # did not re-write or mutate it.
            after = store.open_state(registry)
            assert after.count() == before_count
            assert after.get("b1")["status"] == "open"
            after.close()

    def test_replay_starts_from_clean_slate(self, tmp_path: Path) -> None:
        """Isolation gives the replayed tree a fresh store, so drift in the
        original store after recording cannot make replay diverge."""
        from agent_centric.fbp import store
        from agent_centric.fbp.bills_agent import (
            TASK_ACCEPT,
            TASK_CALENDAR,
            TASK_INTAKE,
        )

        registry = tmp_path / "registry.db"
        with FbpDriver() as driver:
            driver.spawn("bills", kind="bills")
            driver.run(
                "bills_setup",
                {"state": str(registry), "store_keys": ["b1", "b2"]},
                child="bills",
            )
            draft = driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b1",
                        "vendor": "GasCo",
                        "amount_cents": 12345,
                        "due_date": "2026-10-01",
                    }
                },
                child="bills",
            )
            driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            cal = driver.run(
                TASK_CALENDAR,
                {"from_date": "2026-10-01", "to_date": "2026-10-31"},
                child="bills",
            )
            assert [e["id"] for e in cal.value["entries"]] == ["b1"]

            # Drift: a concurrent writer adds an unrelated bill to the original
            # registry after the session was recorded.
            st = store.open_state(registry)
            st.set(
                "b2",
                {
                    "id": "b2",
                    "vendor": "PostCo",
                    "amount_cents": 999,
                    "due_date": "2026-10-05",
                    "status": "open",
                },
                fingerprint="drift",
            )
            st.close()

            # Replay must still pass: the replayed tree reads a fresh, isolated
            # store, so the drifted b2 never leaks into the replayed calendar.
            result = driver.replay_session()
            assert result["ok"] is True, result["failed"]
            assert result["passed"] == result["runs"]

    def test_replay_isolates_trajectory_audit(self, tmp_path: Path) -> None:
        """The replayed tree must not write to the original trajectory file."""
        from agent_centric.fbp import store

        audit_path = tmp_path / "audit.db"
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",), trajectory=str(audit_path))
            driver.run("double", {"value": 21})

            before = store.open_trajectory(audit_path)
            before_count = before.count()
            before.close()

            driver.replay_session()

            # The original trajectory is untouched: replay wrote to an isolated
            # temp audit, not the original file.
            after = store.open_trajectory(audit_path)
            assert after.count() == before_count
            after.close()


class TestSummary:
    """summary() / summarise_ledger give a deterministic, operator-facing view."""

    def test_summary_reports_verified_and_error_runs(self) -> None:
        register_callable("double", _double)
        register_callable("even", _even)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.register("even", _even)
            driver.configure(tasks=("double",), verifiers=("even",), verifier="even")
            ok = driver.run("double", {"value": 21})  # result
            assert ok.verified is True
            # A second run under the even-verifier on an odd output fails.
            driver.register("odd", _odd)
            driver.configure(verifiers=("even", "odd"), verifier="odd")
            bad = driver.run("double", {"value": 21})  # 42 even -> odd fails
            assert bad.verified is False

            s = driver.summary()
            assert s["run_count"] == 2
            assert s["verified_runs"] == 1
            assert s["error_runs"] == 1
            assert s["ok"] is False
            assert s["kinds"].get("run") == 2

    def test_summarise_ledger_matches_driver_summary(
        self, tmp_path: Path
    ) -> None:
        register_callable("double", _double)
        ledger_path = tmp_path / "session.ledger.db"
        with FbpDriver(ledger_path=str(ledger_path)) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run("double", {"value": 21})
            driver.run("double", {"value": 5})

            live = driver.summary()
        from agent_centric.fbp import summarise_ledger

        persisted = summarise_ledger(str(ledger_path))
        assert persisted["run_count"] == live["run_count"] == 2
        assert persisted["verified_runs"] == live["verified_runs"] == 2
        assert persisted["ok"] is True

    def test_summarise_ledger_missing_fails_closed(self, tmp_path: Path) -> None:
        from agent_centric.fbp import summarise_ledger

        with pytest.raises(FileNotFoundError):
            summarise_ledger(str(tmp_path / "nope.db"))


class TestDurableLedger:
    """A durable, recoverable directive ledger: a session can be recorded to
    disk and re-verified (replayed) by a later process."""

    def test_records_and_reopens_session(self, tmp_path: Path) -> None:
        ledger_path = tmp_path / "session.ledger.db"
        with FbpDriver(ledger_path=str(ledger_path)) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            r = driver.run("double", {"value": 21})
            assert r.verified is True and r.value == 42

        # Reopen the ledger and inspect it (read-only).
        with DirectiveLedger(ledger_path) as ledger:
            entries = ledger.all()
            assert ledger.count() == 2  # configure + run
            assert entries[-1]["correlation_id"].startswith("run-")
            assert entries[-1]["payload"]["task"] == "double"
            assert entries[-1]["response"]["terminal"] == "result"
            assert entries[-1]["response"]["terminal_value"] == 42

    def test_replay_ledger_reverifies_session(self, tmp_path: Path) -> None:
        """A fresh process reopens the durable ledger and re-verifies every
        run outcome (crash-safe recovery)."""
        ledger_path = tmp_path / "session.ledger.db"
        with FbpDriver(ledger_path=str(ledger_path)) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run("double", {"value": 21})
            driver.run("double", {"value": 5})

        # Replay from the persisted ledger (fresh driver, isolated state).
        result = replay_ledger(str(ledger_path))
        assert result["ok"] is True, result["failed"]
        assert result["runs"] == 2
        assert result["passed"] == 2

    def test_replay_ledger_auto_seeds_importable_callables(
        self, tmp_path: Path
    ) -> None:
        """replay_ledger re-seeds importable callables from the ledger
        manifest, so cross-process replay needs no manual registration."""
        ledger_path = tmp_path / "session.ledger.db"
        with FbpDriver(ledger_path=str(ledger_path)) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run("double", {"value": 21})

        # The module-level registry is empty (no manual seeding). replay_ledger
        # must import _double from its recorded module.qualname and re-verify.
        from agent_centric.fbp import agent as _agent

        _agent._REGISTRY.clear()
        result = replay_ledger(str(ledger_path))
        assert result["ok"] is True, result["failed"]
        assert result["passed"] == result["runs"]
        assert "double" in result["seeded_callables"]

    def test_replay_ledger_covers_delegated_stateful_tree(
        self, tmp_path: Path
    ) -> None:
        """A durable ledger of a stateful delegated run (bills) replays cleanly
        after the driver is gone, without touching the original store."""
        from agent_centric.fbp import store
        from agent_centric.fbp.bills_agent import TASK_ACCEPT, TASK_INTAKE

        ledger_path = tmp_path / "session.ledger.db"
        registry = tmp_path / "registry.db"
        with FbpDriver(ledger_path=str(ledger_path)) as driver:
            driver.spawn("bills", kind="bills")
            driver.run(
                "bills_setup",
                {"state": str(registry), "store_keys": ["b1"]},
                child="bills",
            )
            draft = driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b1",
                        "vendor": "GasCo",
                        "amount_cents": 12345,
                        "due_date": "2026-10-01",
                    }
                },
                child="bills",
            )
            driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")

        # Replay from the persisted ledger: the stateful bills tree must
        # re-verify cleanly.
        result = replay_ledger(str(ledger_path))
        assert result["ok"] is True, result["failed"]
        assert result["runs"] >= 2

        # The original registry is untouched by the replay.
        st = store.open_state(registry)
        assert st.get("b1")["status"] == "open"


class TestDeterminismEvidence:
    """First-class evidence for the core guarantee: same directives + same
    context ⇒ identical results, and replay reproduces outcomes exactly across
    a variety of payload shapes."""

    def test_identical_directives_produce_identical_results(self) -> None:
        """Two fresh drivers with the same registered callable + same directive
        must produce byte-identical verified results (deterministic by
        construction, not by luck)."""
        register_callable("double", _double)
        register_callable("even", _even)
        outcomes: list[tuple[bool, Any]] = []
        for _ in range(2):
            with FbpDriver() as driver:
                driver.register("double", _double)
                driver.register("even", _even)
                driver.configure(tasks=("double",), verifiers=("even",), verifier="even")
                r = driver.run("double", {"value": 21})
                outcomes.append((r.verified, r.value))
        assert outcomes[0] == outcomes[1]
        assert outcomes[0] == (True, 42)

    def test_table_driven_payload_shapes_replay_exactly(self) -> None:
        """A table of distinct payload shapes must each replay to the exact same
        outcome — the replay guarantee holds across input diversity."""
        register_callable("double", _double)
        register_callable("even", _even)
        payloads = [
            {"value": 0},
            {"value": 1},
            {"value": 21},
            {"value": 100},
            {"value": -4},
        ]
        for payload in payloads:
            with FbpDriver() as driver:
                driver.register("double", _double)
                driver.register("even", _even)
                driver.configure(tasks=("double",), verifiers=("even",), verifier="even")
                r = driver.run("double", payload)
                assert r.verified is True
                result = driver.replay()
                assert result["passed"] is True
                assert result["recorded"] == result["replayed"]

    def test_replay_equivalence_is_exact_not_approximate(self) -> None:
        """Replay must reproduce the recorded terminal kind, value, and error
        exactly — not just 'close enough'."""
        register_callable("double", _double)
        register_callable("even", _even)
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.register("even", _even)
            driver.configure(tasks=("double",), verifiers=("even",), verifier="even")
            driver.run("double", {"value": 21})
            result = driver.replay()
            rec = result["recorded"]
            rep = result["replayed"]
            assert rec["terminal"] == rep["terminal"] == "result"
            assert rec["terminal_value"] == rep["terminal_value"] == 42
            assert rec.get("terminal_error") == rep.get("terminal_error") is None