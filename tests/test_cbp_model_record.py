"""Tests for the deterministic model boundary (SPEC-0009 §4).

A model call is recorded as an immutable, content-addressed record and scored
with a deterministic, scoped confidence. Recording is append-only and idempotent
by content address; scoring is a pure function and never returns 1.0.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp import (
    MAX_MODEL_CONFIDENCE,
    MODEL_RECORD_SCHEMA,
    PROVIDER_EXTERNAL,
    PROVIDER_STUB,
    ModelConfidence,
    ModelRecord,
    ModelRecordError,
    ModelRecordLog,
    is_content_hash,
    score_model_confidence,
)

ZERO_HASH = "0" * 64


def _record(prompt: str = "hello", output: str = "world") -> ModelRecord:
    return ModelRecord.build(
        model_id="stub-model",
        provider_kind=PROVIDER_STUB,
        deterministic=True,
        prompt=prompt,
        output=output,
    )


class TestScoreModelConfidence:
    def test_stub_is_high_but_never_certain(self) -> None:
        c = score_model_confidence(
            provider_kind=PROVIDER_STUB,
            deterministic=True,
            prompt="p",
            output="o",
        )
        assert 0.0 < c.score < 1.0
        assert c.score <= MAX_MODEL_CONFIDENCE

    def test_external_is_lower_than_stub(self) -> None:
        stub = score_model_confidence(
            provider_kind=PROVIDER_STUB, deterministic=True, prompt="p", output="o"
        )
        ext = score_model_confidence(
            provider_kind=PROVIDER_EXTERNAL, deterministic=False, prompt="p", output="o"
        )
        assert 0.0 < ext.score < stub.score < 1.0

    def test_empty_prompt_or_output_scores_zero(self) -> None:
        assert score_model_confidence(
            provider_kind=PROVIDER_STUB, deterministic=True, prompt="", output="o"
        ).score == 0.0
        assert score_model_confidence(
            provider_kind=PROVIDER_STUB, deterministic=True, prompt="p", output=""
        ).score == 0.0

    def test_unknown_provider_kind_fails_closed(self) -> None:
        with pytest.raises(ModelRecordError):
            score_model_confidence(
                provider_kind="bogus",
                deterministic=True,
                prompt="p",
                output="o",
            )

    def test_confidence_rejects_out_of_range(self) -> None:
        with pytest.raises(ModelRecordError):
            ModelConfidence(1.0, "certain")
        with pytest.raises(ModelRecordError):
            ModelConfidence(-0.1, "negative")
        with pytest.raises(ModelRecordError):
            ModelConfidence(0.5, "")


class TestModelRecord:
    def test_build_is_deterministic_and_content_addressed(self) -> None:
        a = _record()
        b = _record()
        assert a == b
        assert a.record_hash() == b.record_hash()
        assert is_content_hash(a.record_hash())
        assert a.schema == MODEL_RECORD_SCHEMA

    def test_distinct_calls_have_distinct_hashes(self) -> None:
        assert _record(prompt="a").record_hash() != _record(prompt="b").record_hash()
        assert _record(output="a").record_hash() != _record(output="b").record_hash()

    def test_build_rejects_empty_identity_or_bad_provider(self) -> None:
        with pytest.raises(ModelRecordError):
            ModelRecord.build(
                model_id="",
                provider_kind=PROVIDER_STUB,
                deterministic=True,
                prompt="p",
                output="o",
            )
        with pytest.raises(ModelRecordError):
            ModelRecord.build(
                model_id="m",
                provider_kind="bogus",
                deterministic=True,
                prompt="p",
                output="o",
            )

    def test_record_validates_hashes_and_confidence(self) -> None:
        with pytest.raises(ModelRecordError):
            ModelRecord(
                model_id="m",
                provider_kind=PROVIDER_STUB,
                deterministic=True,
                prompt_sha256="not-a-hash",
                output_sha256=ZERO_HASH,
                confidence=0.5,
                confidence_reason="x",
            )
        with pytest.raises(ModelRecordError):
            ModelRecord(
                model_id="m",
                provider_kind=PROVIDER_STUB,
                deterministic=True,
                prompt_sha256=ZERO_HASH,
                output_sha256=ZERO_HASH,
                confidence=1.0,
                confidence_reason="x",
            )

    def test_canonical_bytes_are_stable(self) -> None:
        r = _record()
        assert r.canonical_bytes() == r.canonical_bytes()

    def test_to_dict_is_json_ready(self) -> None:
        d = _record().to_dict()
        assert d["schema"] == MODEL_RECORD_SCHEMA
        assert d["provider_kind"] == PROVIDER_STUB
        assert isinstance(d["confidence"], float)


class TestModelRecordLog:
    def test_record_is_idempotent_by_content_address(self) -> None:
        log = ModelRecordLog()
        r = _record()
        assert log.record(r) is True
        assert log.record(r) is False  # same content -> no duplicate
        assert len(log) == 1
        assert r.record_hash() in log

    def test_entries_are_sorted_by_content_address(self) -> None:
        log = ModelRecordLog()
        log.record(_record(prompt="b"))
        log.record(_record(prompt="a"))
        hashes = [r.record_hash() for r in log.entries()]
        assert hashes == sorted(hashes)

    def test_get_returns_record(self) -> None:
        log = ModelRecordLog()
        r = _record()
        log.record(r)
        assert log.get(r.record_hash()) == r
        assert log.get("f" * 64) is None

    def test_to_list_carries_record_hash(self) -> None:
        log = ModelRecordLog()
        r = _record()
        log.record(r)
        rows = log.to_list()
        assert len(rows) == 1
        assert rows[0]["record_hash"] == r.record_hash()
