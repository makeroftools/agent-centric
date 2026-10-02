"""Tests for the isolated holdout validator skeleton (SPEC-0019 Phase 1).

Deterministic and offline. These tests drive the **validator process** through
its public entrypoint with synthetic inputs; they must never read the real
``specs/holdout/`` scenarios (that path belongs to the validator, not the
author). The skeleton must fail closed in every current state.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "cbp_holdout_validate", _REPO / "tools" / "holdout-validate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M = _module()


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
        assert _M.context_is_validator({_M.CONTEXT_ENV: _M.CONTEXT_VALUE}) is True
        assert _M.context_is_validator({}) is False


class TestFailClosed:
    def test_refuses_when_no_suite_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _M.main([], env={_M.CONTEXT_ENV: _M.CONTEXT_VALUE})
        assert code == _M.EXIT_REFUSED
        out = json.loads(capsys.readouterr().out)
        assert "no suite" in out["reason"]

    def test_refuses_until_the_probe_vocabulary_exists(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lock = tmp_path / "holdout.lock"
        lock.write_text("{}\n", encoding="utf-8")
        code = _M.main(
            ["--lock", str(lock)], env={_M.CONTEXT_ENV: _M.CONTEXT_VALUE}
        )
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
