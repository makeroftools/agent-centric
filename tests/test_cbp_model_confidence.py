"""Integration tests: a ``model`` component is recorded and confidence-scored.

SPEC-0009 §4 requires that non-determinism is never silent: a model component's
output is recorded (content-addressed, append-only, idempotent) and
confidence-scored, and is still re-verified by the parent (it is never relied
upon on its own word).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent_centric.cbp import (
    PROVIDER_EXTERNAL,
    PROVIDER_STUB,
    CbpDriver,
)
from agent_centric.cbp.model_agent import TASK_MODEL


class TestModelComponentRecording:
    def test_model_call_is_recorded_and_confidence_scored(self) -> None:
        with CbpDriver() as driver:
            driver.spawn("model", kind="model")
            r = driver.run(TASK_MODEL, {"prompt": "summarize that"}, child="model")
            assert r.verified is True
            assert isinstance(r.confidence, float)
            assert 0.0 < r.confidence < 1.0
            assert r.model_record is not None
            records = driver.model_records("model")
            assert len(records) == 1
            assert records[0].record_hash() == r.model_record
            assert records[0].confidence == r.confidence
            assert records[0].provider_kind == PROVIDER_STUB
            assert records[0].deterministic is True

    def test_identical_calls_are_recorded_once(self) -> None:
        with CbpDriver() as driver:
            driver.spawn("model", kind="model")
            a = driver.run(TASK_MODEL, {"prompt": "same"}, child="model")
            b = driver.run(TASK_MODEL, {"prompt": "same"}, child="model")
            assert a.model_record == b.model_record
            assert len(driver.model_records("model")) == 1

    def test_distinct_calls_are_distinct_records(self) -> None:
        with CbpDriver() as driver:
            driver.spawn("model", kind="model")
            driver.run(TASK_MODEL, {"prompt": "one"}, child="model")
            driver.run(TASK_MODEL, {"prompt": "two"}, child="model")
            records = driver.model_records("model")
            assert len(records) == 2
            hashes = {rec.record_hash() for rec in records}
            assert len(hashes) == 2

    def test_external_provider_is_recorded_nondeterministic(self) -> None:
        with CbpDriver() as driver:
            driver.spawn("model", kind="model")
            driver.configure_provider(
                "model", lambda prompt: f"real:{prompt}", model_id="real-v1"
            )
            r = driver.run(TASK_MODEL, {"prompt": "x"}, child="model")
            assert r.verified is True
            rec = driver.model_records("model")[0]
            assert rec.provider_kind == PROVIDER_EXTERNAL
            assert rec.deterministic is False
            assert rec.model_id == "real-v1"
            assert rec.confidence < 0.9

    def test_parent_verifier_still_demotes_model_output(self) -> None:
        """Confidence never makes a model's output a trusted success."""

        def _reject(_value: object) -> bool:
            return False

        with CbpDriver() as driver:
            driver.spawn("model", kind="model")
            driver.register("no_model_verify", _reject)
            driver.configure(
                verifiers=("no_model_verify",), verifier="no_model_verify"
            )
            r = driver.run(TASK_MODEL, {"prompt": "x"}, child="model")
            assert r.verified is False
            assert r.error is not None
            # The call was still recorded at the boundary.
            assert len(driver.model_records("model")) == 1

    def test_model_records_on_non_model_child_fails_closed(self) -> None:
        with CbpDriver() as driver:
            driver.spawn("store", kind="store")
            with pytest.raises(ValueError):
                driver.model_records("store")
            with pytest.raises(ValueError):
                driver.model_records("ghost")

    def test_record_is_persisted_to_trajectory_when_granted(self, tmp_path) -> None:
        traj = tmp_path / "model.db"
        with CbpDriver() as driver:
            driver.spawn("model", kind="model")
            driver.configure_child("model", trajectory=str(traj))
            r = driver.run(TASK_MODEL, {"prompt": "audit me"}, child="model")
            assert r.verified is True
        con = sqlite3.connect(str(traj))
        try:
            rows = con.execute("SELECT kind, sources FROM events").fetchall()
        finally:
            con.close()
        model_rows = [row for row in rows if row[0] == "result"]
        assert model_rows, rows
        sources = json.loads(model_rows[0][1])
        record_entries = [s for s in sources if s.get("kind") == "model-record"]
        assert record_entries
        assert record_entries[0]["id"] == r.model_record
