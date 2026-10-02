"""Tests for the isolated holdout validator (SPEC-0019).

Deterministic and offline. These tests drive the **validator process** through
its public entrypoint with synthetic inputs; they must never read the real
``specs/holdout/`` scenarios (that path belongs to the validator, not the
author). The validator must fail closed on every drift and decide deterministically.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_centric.cbp import holdout as _M

_PASS = _M.RunOutcome.PASS
_FAIL = _M.RunOutcome.FAIL
_ERROR = _M.RunOutcome.ERROR
_HIGH = _M.Priority.HIGH
_NORMAL = _M.Priority.NORMAL
_VPASS = _M.ScenarioVerdict.PASS
_VFAIL = _M.ScenarioVerdict.FAIL
_VNONDET = _M.ScenarioVerdict.NONDETERMINISTIC
_VINSUFF = _M.ScenarioVerdict.INSUFFICIENT
_VALIDATOR_ENV = {_M.CONTEXT_ENV: _M.CONTEXT_VALUE}


@pytest.fixture(autouse=True)
def _isolate_private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Never write to the real operator-private root during tests.
    monkeypatch.setenv("CBP_HOLDOUT_ROOT", str(tmp_path / "priv"))


def _runs(sid, priority, *outcomes):
    return _M.ScenarioRuns(scenario_id=sid, priority=priority, runs=tuple(outcomes))


def _scenario(sid, priority, target="health.sh"):
    return _M.Scenario(
        scenario_id=sid,
        priority=priority,
        feature="feature",
        probe={"kind": "task", "target": target, "expect_exit": 0},
        digest="0" * 64,
    )


def _suite(*scenarios):
    return _M.Suite(scenarios=tuple(scenarios))


def _write_scenario(path: Path, sid: str, priority: str = "normal", target: str = "health.sh"):
    path.write_text(
        "---\n"
        f"id: {sid}\n"
        "service: cbp\n"
        "feature: health\n"
        f"priority: {priority}\n"
        "---\n\n"
        "The named task must exit as expected.\n\n"
        "```check\n"
        f'{{"kind": "task", "target": "{target}", "expect_exit": 0}}\n'
        "```\n",
        encoding="utf-8",
    )


class TestIsolation:
    def test_refuses_outside_the_validator_context(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = _M.main([], env={})
        assert code == _M.EXIT_REFUSED
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "refused"
        assert "validator context" in out["reason"]

    def test_refuses_when_context_has_the_wrong_value(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = _M.main([], env={_M.CONTEXT_ENV: "author"})
        assert code == _M.EXIT_REFUSED
        assert json.loads(capsys.readouterr().out)["verdict"] == "refused"

    def test_context_gate_accepts_only_the_validator_value(self) -> None:
        assert _M.context_is_validator(_VALIDATOR_ENV) is True
        assert _M.context_is_validator({}) is False


class TestFailClosed:
    def test_refuses_when_no_lock_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _M.main(["--lock", "/nonexistent/holdout.lock", "--rehearsal"], env=_VALIDATOR_ENV)
        assert code == _M.EXIT_REFUSED
        assert "no holdout lock" in json.loads(capsys.readouterr().out)["reason"]

    def test_refuses_when_the_suite_is_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lock = tmp_path / "holdout.lock"
        lock.write_text("{}\n", encoding="utf-8")
        suite = tmp_path / "suite"
        suite.mkdir()
        os.chmod(suite, 0o700)
        code = _M.main(
            ["--lock", str(lock), "--suite", str(suite), "--rehearsal"], env=_VALIDATOR_ENV
        )
        assert code == _M.EXIT_REFUSED
        assert "no scenarios" in json.loads(capsys.readouterr().out)["reason"]


class TestContract:
    def test_decision_constants_are_frozen(self) -> None:
        assert _M.RUNS == 3
        assert _M.SUPERMAJORITY == 2
        assert _M.HOLDOUT_RUN_SCHEMA == "holdout.run/v1"
        assert _M.HOLDOUT_LOCK_SCHEMA == "holdout.lock/v1"
        assert _M.PROBE_CONTRACT == "holdout.v1"

    def test_refusal_document_is_deterministic(self) -> None:
        assert _M._refusal_document("why") == _M._refusal_document("why")


class TestScenarioParsing:
    def test_parses_front_matter_and_check_block(self, tmp_path: Path) -> None:
        path = tmp_path / "HO-0001.md"
        _write_scenario(path, "HO-0001", "high", "health.sh")
        front, probe = _M.parse_scenario(path.read_text(encoding="utf-8"))
        assert front["id"] == "HO-0001" and front["priority"] == "high"
        assert probe == {"kind": "task", "target": "health.sh", "expect_exit": 0}

    def test_rejects_a_scenario_without_front_matter(self) -> None:
        with pytest.raises(_M.Refusal):
            _M.parse_scenario("no front matter here\n")

    def test_rejects_a_scenario_without_a_check_block(self) -> None:
        with pytest.raises(_M.Refusal):
            _M.parse_scenario("---\nid: HO-1\npriority: high\n---\n\nprose only\n")

    def test_rejects_an_unsupported_probe_kind(self) -> None:
        with pytest.raises(_M.Refusal):
            _M.parse_scenario(
                "---\nid: HO-1\npriority: high\n---\n\n```check\n"
                '{"kind": "network", "target": "x"}\n```\n'
            )


class TestSuiteAndLock:
    def test_load_and_verify_round_trip(self, tmp_path: Path) -> None:
        suite_dir = tmp_path / "suite"
        suite_dir.mkdir()
        _write_scenario(suite_dir / "HO-0001.md", "HO-0001", "high")
        _write_scenario(suite_dir / "HO-0002.md", "HO-0002", "normal")
        suite = _M.load_suite(suite_dir)
        lock = _M.build_lock(suite)
        _M.verify_suite(suite, lock)  # does not raise
        assert sorted(lock["scenarios"]) == ["HO-0001", "HO-0002"]

    def test_verification_refuses_on_scenario_drift(self, tmp_path: Path) -> None:
        suite_dir = tmp_path / "suite"
        suite_dir.mkdir()
        path = suite_dir / "HO-0001.md"
        _write_scenario(path, "HO-0001", "high")
        lock = _M.build_lock(_M.load_suite(suite_dir))
        path.write_text(path.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
        with pytest.raises(_M.Refusal):
            _M.verify_suite(_M.load_suite(suite_dir), lock)

    def test_verification_refuses_on_a_changed_scenario_set(self) -> None:
        suite = _suite(_scenario("HO-1", _HIGH))
        lock = _M.build_lock(suite)
        lock["scenarios"] = {"HO-9": "0" * 64}
        with pytest.raises(_M.Refusal):
            _M.verify_suite(suite, lock)

    def test_verification_refuses_a_wrong_probe_contract(self) -> None:
        suite = _suite(_scenario("HO-1", _HIGH))
        lock = _M.build_lock(suite)
        lock["probe_contract"] = "holdout.v2"
        with pytest.raises(_M.Refusal):
            _M.verify_suite(suite, lock)


class TestDecisionEngine:
    def test_high_requires_all_runs_to_pass(self) -> None:
        assert _M.evaluate_scenario(_runs("h", _HIGH, _PASS, _PASS, _PASS)) is _VPASS
        assert _M.evaluate_scenario(_runs("h", _HIGH, _PASS, _PASS, _ERROR)) is _VFAIL
        assert _M.evaluate_scenario(_runs("h", _HIGH, _PASS, _FAIL, _FAIL)) is _VNONDET
        assert _M.evaluate_scenario(_runs("h", _HIGH, _FAIL, _FAIL, _FAIL)) is _VFAIL

    def test_normal_absorbs_flake_but_not_disagreement(self) -> None:
        assert _M.evaluate_scenario(_runs("n", _NORMAL, _PASS, _PASS, _ERROR)) is _VPASS
        assert _M.evaluate_scenario(_runs("n", _NORMAL, _PASS, _PASS, _PASS)) is _VPASS
        assert _M.evaluate_scenario(_runs("n", _NORMAL, _PASS, _ERROR, _ERROR)) is _VFAIL
        assert _M.evaluate_scenario(_runs("n", _NORMAL, _PASS, _PASS, _FAIL)) is _VNONDET
        assert _M.evaluate_scenario(_runs("n", _NORMAL, _ERROR, _ERROR, _ERROR)) is _VINSUFF

    def test_empty_suite_is_red(self) -> None:
        verdict = _M.decide(())
        assert verdict.green is False and verdict.aggregate == 0.0


class _PlannedBackend:
    def __init__(self, plan, attempt: int) -> None:
        self._plan = plan
        self._attempt = attempt

    def run(self, probe):
        return self._plan[str(probe["target"])][self._attempt]


class TestRunSuite:
    def test_all_pass_is_green(self) -> None:
        suite = _suite(_scenario("a", _HIGH, "h.sh"), _scenario("b", _NORMAL, "n.sh"))
        plan = {"h.sh": [_PASS, _PASS, _PASS], "n.sh": [_PASS, _PASS, _PASS]}
        verdict = _M.run_suite(suite, make_backend=lambda i: _PlannedBackend(plan, i))
        assert verdict.green is True and verdict.aggregate == 1.0

    def test_normal_absorbs_one_flake(self) -> None:
        suite = _suite(_scenario("a", _NORMAL, "n.sh"))
        plan = {"n.sh": [_PASS, _PASS, _ERROR]}
        verdict = _M.run_suite(suite, make_backend=lambda i: _PlannedBackend(plan, i))
        assert verdict.green is True

    def test_nondeterminism_is_red(self) -> None:
        suite = _suite(_scenario("a", _NORMAL, "n.sh"))
        plan = {"n.sh": [_PASS, _PASS, _FAIL]}
        verdict = _M.run_suite(suite, make_backend=lambda i: _PlannedBackend(plan, i))
        assert verdict.green is False


class TestTaskBackend:
    def _backend(self, tmp_path: Path):
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        work = tmp_path / "work"
        work.mkdir()
        return tasks, work

    def _script(self, tasks: Path, name: str, body: str) -> None:
        path = tasks / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        os.chmod(path, 0o755)

    def test_zero_exit_passes(self, tmp_path: Path) -> None:
        tasks, work = self._backend(tmp_path)
        self._script(tasks, "health.sh", "exit 0")
        backend = _M.TaskBackend(tasks, work)
        assert backend.run({"kind": "task", "target": "health.sh"}) is _PASS

    def test_expected_nonzero_exit_passes(self, tmp_path: Path) -> None:
        tasks, work = self._backend(tmp_path)
        self._script(tasks, "check.sh", "exit 3")
        backend = _M.TaskBackend(tasks, work)
        probe = {"kind": "task", "target": "check.sh", "expect_exit": 3}
        assert backend.run(probe) is _PASS

    def test_wrong_exit_fails(self, tmp_path: Path) -> None:
        tasks, work = self._backend(tmp_path)
        self._script(tasks, "check.sh", "exit 3")
        backend = _M.TaskBackend(tasks, work)
        assert backend.run({"kind": "task", "target": "check.sh"}) is _FAIL

    def test_missing_task_is_a_flake(self, tmp_path: Path) -> None:
        tasks, work = self._backend(tmp_path)
        backend = _M.TaskBackend(tasks, work)
        assert backend.run({"kind": "task", "target": "nope.sh"}) is _ERROR


class TestCliEndToEnd:
    def _fixture(self, tmp_path: Path):
        suite = tmp_path / "suite"
        suite.mkdir()
        os.chmod(suite, 0o700)
        _write_scenario(suite / "HO-0001.md", "HO-0001", "normal", "health.sh")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        script = tasks / "health.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(script, 0o755)
        lock = tmp_path / "holdout.lock"
        lock.write_text(json.dumps(_M.build_lock(_M.load_suite(suite))), encoding="utf-8")
        return suite, tasks, lock

    def test_green_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        suite, tasks, lock = self._fixture(tmp_path)
        code = _M.main(
            ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks), "--rehearsal"],
            env=_VALIDATOR_ENV,
        )
        assert code == _M.EXIT_OK
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "green"
        assert out["scenarios"] == [{"id": "HO-0001", "verdict": "pass"}]
        assert len(out["lock_sha256"]) == 64

    def test_drift_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite, tasks, lock = self._fixture(tmp_path)
        scenario = suite / "HO-0001.md"
        scenario.write_text(scenario.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        code = _M.main(
            ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks), "--rehearsal"],
            env=_VALIDATOR_ENV,
        )
        assert code == _M.EXIT_REFUSED
        assert "drift" in json.loads(capsys.readouterr().out)["reason"]


class TestDecideCli:
    def _payload(self):
        return {
            "scenarios": [
                {"id": "HO-2", "priority": "normal", "runs": ["pass", "pass", "pass"]},
                {"id": "HO-1", "priority": "high", "runs": ["pass", "pass", "pass"]},
            ]
        }

    def test_decide_cli_is_green_and_sorted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "runs.json"
        path.write_text(json.dumps(self._payload()), encoding="utf-8")
        code = _M.main(["--decide", str(path)], env=_VALIDATOR_ENV)
        assert code == _M.EXIT_OK
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "green"
        assert [s["id"] for s in out["scenarios"]] == ["HO-1", "HO-2"]

    def test_decide_cli_still_requires_context(self, tmp_path: Path) -> None:
        path = tmp_path / "runs.json"
        path.write_text(json.dumps(self._payload()), encoding="utf-8")
        assert _M.main(["--decide", str(path)], env={}) == _M.EXIT_REFUSED

    def test_decide_cli_rejects_malformed_input(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "runs.json"
        path.write_text('{"scenarios": [{"id": "x"}]}', encoding="utf-8")
        code = _M.main(["--decide", str(path)], env=_VALIDATOR_ENV)
        assert code == _M.EXIT_REFUSED
        assert json.loads(capsys.readouterr().out)["verdict"] == "refused"

class TestPrivateSuite:
    def test_refuses_a_group_or_other_accessible_suite(self, tmp_path: Path) -> None:
        suite = tmp_path / "s"
        suite.mkdir()
        os.chmod(suite, 0o755)
        with pytest.raises(_M.Refusal):
            _M.enforce_private_suite(suite)

    def test_accepts_an_owner_only_suite(self, tmp_path: Path) -> None:
        suite = tmp_path / "s"
        suite.mkdir()
        os.chmod(suite, 0o700)
        _M.enforce_private_suite(suite)  # does not raise


class TestLedger:
    def test_append_is_idempotent(self, tmp_path: Path) -> None:
        ledger = tmp_path / "runs.jsonl"
        record = {"schema": "holdout.record/v1", "verdict": "green"}
        assert _M.append_record(ledger, record) is True
        assert _M.append_record(ledger, record) is False
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1

    def test_record_carries_a_fingerprint(self, tmp_path: Path) -> None:
        ledger = tmp_path / "runs.jsonl"
        _M.append_record(ledger, {"verdict": "green"})
        entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        assert len(entry["fingerprint"]) == 64


class TestSelfTest:
    """A synthetic known-good / known-bad pair: the gate must separate them."""

    def _run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], exit_code: int):
        suite = tmp_path / "s"
        suite.mkdir()
        os.chmod(suite, 0o700)
        _write_scenario(suite / "HO-0001.md", "HO-0001", "high", "health.sh")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        script = tasks / "health.sh"
        script.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n", encoding="utf-8")
        os.chmod(script, 0o755)
        lock = tmp_path / "holdout.lock"
        lock.write_text(json.dumps(_M.build_lock(_M.load_suite(suite))), encoding="utf-8")
        ledger = tmp_path / "runs.jsonl"
        code = _M.main(
            ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks),
             "--ledger", str(ledger), "--rehearsal"],
            env=_VALIDATOR_ENV,
        )
        return code, json.loads(capsys.readouterr().out), ledger

    def test_known_good_is_green(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, ledger = self._run(tmp_path, capsys, 0)
        assert code == _M.EXIT_OK and out["verdict"] == "green"
        entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        assert entry["validator_version"] == _M.VALIDATOR_VERSION
        assert entry["isolation"] == "rehearsal"
        assert len(entry["lock_sha256"]) == 64

    def test_known_bad_is_red(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = self._run(tmp_path, capsys, 1)
        assert code == _M.EXIT_RED and out["verdict"] == "red"

    def test_reruns_are_identical_and_ledger_is_single(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite = tmp_path / "s"
        suite.mkdir()
        os.chmod(suite, 0o700)
        _write_scenario(suite / "HO-0001.md", "HO-0001", "normal", "health.sh")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        script = tasks / "health.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(script, 0o755)
        lock = tmp_path / "holdout.lock"
        lock.write_text(json.dumps(_M.build_lock(_M.load_suite(suite))), encoding="utf-8")
        ledger = tmp_path / "runs.jsonl"
        args = ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks),
                "--ledger", str(ledger), "--rehearsal"]
        first = _M.main(args, env=_VALIDATOR_ENV)
        out_first = capsys.readouterr().out
        second = _M.main(args, env=_VALIDATOR_ENV)
        out_second = capsys.readouterr().out
        assert first == second == _M.EXIT_OK
        assert out_first == out_second
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


class TestPublicBoundaryGuard:
    def test_cli_refuses_a_world_readable_suite(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite = tmp_path / "s"
        suite.mkdir()
        os.chmod(suite, 0o755)
        _write_scenario(suite / "HO-0001.md", "HO-0001", "normal", "health.sh")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        script = tasks / "health.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(script, 0o755)
        lock = tmp_path / "holdout.lock"
        lock.write_text(json.dumps(_M.build_lock(_M.load_suite(suite))), encoding="utf-8")
        code = _M.main(
            ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks), "--rehearsal"],
            env=_VALIDATOR_ENV,
        )
        assert code == _M.EXIT_REFUSED
        assert "owner-only" in json.loads(capsys.readouterr().out)["reason"]

class TestBuildLockCli:
    def test_builds_a_lock_for_a_private_suite(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite = tmp_path / "s"
        suite.mkdir()
        os.chmod(suite, 0o700)
        _write_scenario(suite / "HO-0001.md", "HO-0001", "high")
        code = _M.main(["--build-lock", "--suite", str(suite)], env=_VALIDATOR_ENV)
        assert code == _M.EXIT_OK
        lock = json.loads(capsys.readouterr().out)
        assert lock["schema"] == "holdout.lock/v1"
        assert lock["probe_contract"] == _M.PROBE_CONTRACT
        assert sorted(lock["scenarios"]) == ["HO-0001"]
