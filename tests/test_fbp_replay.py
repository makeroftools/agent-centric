"""Tests for deterministic replay (re-verification after the fact)."""

from __future__ import annotations

from agent_centric.fbp import FbpDriver, register_callable


def _double(value: int) -> int:
    return value * 2


def _even(v) -> bool:
    return isinstance(v, int) and v % 2 == 0


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