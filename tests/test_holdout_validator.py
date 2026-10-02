"""Tests for the isolated holdout validator (SPEC-0019).

Deterministic and offline. These tests drive the **validator process** through
its public entrypoint with synthetic inputs; they must never read the real
``specs/holdout/`` scenarios (that path belongs to the validator, not the
author). The skeleton must fail closed in every current state, and the decision
engine must be deterministic.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "cbp_holdout_validate", _REPO / "tools" / "holdout-validate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


_M = _module()

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


def _runs(sid, priority, *outcomes):
    return _M.ScenarioRuns(scenario_id=sid, priority=priority, runs=tuple(outcomes))


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
    def test_refuses_when_no_suite_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _M.main([], env=_VALIDATOR_ENV)
        assert code == _M.EXIT_REFUSED
        assert "no suite" in json.loads(capsys.readouterr().out)["reason"]

    def test_refuses_until_the_probe_vocabulary_exists(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lock = tmp_path / "holdout.lock"
        lock.write_text("{}\n", encoding="utf-8")
        code = _M.main(["--lock", str(lock)], env=_VALIDATOR_ENV)
        assert code == _M.EXIT_REFUSED
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "refused"
        assert "Phase 2" in out["reason"]


class TestContract:
    def test_decision_constants_are_frozen(self) -> None:
        assert _M.RUNS == 3
        assert _M.SUPERMAJORITY == 2
        assert _M.HOLDOUT_RUN_SCHEMA == "holdout.run/v1"
        assert _M.HOLDOUT_LOCK_SCHEMA == "holdout.lock/v1"

    def test_refusal_document_is_deterministic(self) -> None:
        first = _M._refusal_document("why")
        second = _M._refusal_document("why")
        assert first == second
        assert first["runs"] == 3 and first["supermajority"] == 2


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


class TestDecide:
    def test_empty_suite_is_red(self) -> None:
        verdict = _M.decide(())
        assert verdict.green is False
        assert verdict.aggregate == 0.0

    def test_all_pass_is_green(self) -> None:
        verdict = _M.decide(
            (_runs("a", _HIGH, _PASS, _PASS, _PASS), _runs("b", _NORMAL, _PASS, _PASS, _PASS))
        )
        assert verdict.green is True
        assert verdict.aggregate == 1.0

    def test_a_high_failure_blocks_green(self) -> None:
        verdict = _M.decide(
            (_runs("a", _HIGH, _FAIL, _FAIL, _FAIL), _runs("b", _NORMAL, _PASS, _PASS, _PASS))
        )
        assert verdict.green is False

    def test_threshold_governs_normal_scenarios(self) -> None:
        scenarios = (
            _runs("a", _NORMAL, _PASS, _PASS, _PASS),
            _runs("b", _NORMAL, _FAIL, _FAIL, _FAIL),
        )
        assert _M.decide(scenarios, threshold=0.5).green is True
        assert _M.decide(scenarios, threshold=1.0).green is False


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

    def test_decide_cli_still_requires_context(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
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
