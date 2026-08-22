"""Tests for deterministic replay (re-verification after the fact)."""

from __future__ import annotations

from pathlib import Path

from agent_centric.fbp import FbpDriver, register_callable


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