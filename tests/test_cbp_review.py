"""Tests for the ``review.v1`` contract and the persistent Review queue.

The Review queue is the durable realization of SPEC-0002 §4.4: an outcome that
cannot be made deterministic is traceable, confidence-scored, and enqueued to a
persistent, append-only queue. The queue is write-once and idempotent (Law 10);
an item's status is derived from an appended resolution, never by mutation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.cbp import ReviewError, ReviewQueue, enqueue_for_review
from agent_centric.cbp.config import AgentConfig
from agent_centric.cbp.driver import CbpDriver
from agent_centric.cbp.message import (
    DIRECTIVE_RUN,
    RESPONSE_ERROR,
    RESPONSE_RESULT,
    Directive,
    Response,
    effective_confidence,
    stamp_confidence,
)
from agent_centric.cbp.model_agent import TASK_MODEL, ModelAgent
from agent_centric.contracts.review import (
    DEFAULT_REVIEW_THRESHOLD,
    REVIEW_SCHEMA,
    ReviewItem,
    ReviewResolution,
    ReviewStatus,
    is_content_hash,
    needs_review,
)

ZERO_HASH = "0" * 64


def _item(correlation_id: str = "c1", confidence: float = 0.5) -> ReviewItem:
    return ReviewItem(
        correlation_id=correlation_id,
        domain="model",
        confidence=confidence,
        provenance=("node:model", "model-record:" + ZERO_HASH),
    )


class TestReviewItemContract:
    def test_content_addressed_and_stable(self) -> None:
        a = _item()
        b = _item()
        assert a == b
        assert a.item_hash() == b.item_hash()
        assert is_content_hash(a.item_hash())
        assert a.schema == REVIEW_SCHEMA

    def test_distinct_content_has_distinct_address(self) -> None:
        assert _item(correlation_id="a").item_hash() != _item(correlation_id="b").item_hash()
        assert _item(confidence=0.4).item_hash() != _item(confidence=0.6).item_hash()

    def test_round_trips_through_dict(self) -> None:
        item = _item()
        assert ReviewItem.from_dict(item.to_dict()) == item

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"correlation_id": "", "domain": "d", "confidence": 0.5},
            {"correlation_id": "c", "domain": "", "confidence": 0.5},
            {"correlation_id": "c", "domain": "d", "confidence": 1.5},
            {"correlation_id": "c", "domain": "d", "confidence": -0.1},
            {"correlation_id": "c", "domain": "d", "confidence": True},
            {"correlation_id": "c", "domain": "d", "confidence": 0.5, "schema": "bad"},
        ],
    )
    def test_invalid_items_fail_closed(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            ReviewItem(**kwargs)  # type: ignore[arg-type]


class TestReviewResolutionContract:
    def test_content_addressed(self) -> None:
        r = ReviewResolution(item_hash=ZERO_HASH, resolution="accepted")
        assert is_content_hash(r.resolution_hash())
        assert r.resolution_hash() == ReviewResolution(
            item_hash=ZERO_HASH, resolution="accepted"
        ).resolution_hash()

    def test_requires_a_content_hash_and_nonempty_resolution(self) -> None:
        with pytest.raises(ValueError):
            ReviewResolution(item_hash="nope", resolution="accepted")
        with pytest.raises(ValueError):
            ReviewResolution(item_hash=ZERO_HASH, resolution="")

    def test_round_trips_through_dict(self) -> None:
        r = ReviewResolution(item_hash=ZERO_HASH, resolution="rejected", resolver="op")
        assert ReviewResolution.from_dict(r.to_dict()) == r


class TestNeedsReview:
    def test_unverified_always_needs_review(self) -> None:
        assert needs_review(verified=False) is True
        assert needs_review(verified=False, confidence=0.99) is True

    def test_verified_without_confidence_is_deterministic(self) -> None:
        assert needs_review(verified=True) is False

    def test_verified_below_threshold_needs_review(self) -> None:
        assert needs_review(verified=True, confidence=0.9) is True
        assert needs_review(verified=True, confidence=0.9, threshold=0.8) is False

    def test_full_confidence_bypasses_review(self) -> None:
        assert needs_review(verified=True, confidence=1.0) is False
        assert DEFAULT_REVIEW_THRESHOLD == 1.0

    def test_malformed_confidence_is_fail_closed(self) -> None:
        assert needs_review(verified=True, confidence="high") is True  # type: ignore[arg-type]
        assert needs_review(verified=True, confidence=2.0) is True

    def test_bad_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            needs_review(verified=True, threshold=1.5)
        with pytest.raises(ValueError):
            needs_review(verified=True, threshold=True)  # type: ignore[arg-type]


class TestReviewQueue:
    def test_requires_open(self, tmp_path: Path) -> None:
        q = ReviewQueue(tmp_path / "review.db")
        with pytest.raises(ReviewError):
            q.enqueue(_item())

    def test_enqueue_is_idempotent_by_content_address(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            seq_a, inserted_a = q.enqueue(_item())
            seq_b, inserted_b = q.enqueue(_item())
            assert inserted_a is True
            assert inserted_b is False
            assert seq_a == seq_b
            assert q.count() == 1

    def test_pending_derives_status_from_appended_resolution(
        self, tmp_path: Path
    ) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            item = _item()
            q.enqueue(item)
            assert q.status(item.item_hash()) == ReviewStatus.PENDING
            assert q.pending() == (item,)
            assert q.resolved() == ()
            _, inserted = q.resolve(item.item_hash(), "accepted", resolver="op")
            assert inserted is True
            assert q.status(item.item_hash()) == ReviewStatus.RESOLVED
            assert q.pending() == ()
            assert q.resolved() == (item,)
            # The original item is untouched (write-once).
            assert q.items() == (item,)

    def test_resolve_is_idempotent_and_refuses_unknown(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            item = _item()
            q.enqueue(item)
            seq_a, inserted_a = q.resolve(item.item_hash(), "ok")
            seq_b, inserted_b = q.resolve(item.item_hash(), "ok")
            assert inserted_a is True
            assert inserted_b is False
            assert seq_a == seq_b
            assert len(q.resolutions()) == 1
            with pytest.raises(ReviewError):
                q.resolve("f" * 64, "ghost")

    def test_ordering_is_insertion_order(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            first = _item(correlation_id="a")
            second = _item(correlation_id="b")
            q.enqueue(second)
            q.enqueue(first)
            assert [i.correlation_id for i in q.items()] == ["b", "a"]

    def test_persists_across_reopen_and_continues_sequence(self, tmp_path: Path) -> None:
        path = tmp_path / "review.db"
        item = _item()
        with ReviewQueue(path) as q:
            q.enqueue(item)
            resolved_seq, _ = q.resolve(item.item_hash(), "accepted")
        with ReviewQueue(path) as q:
            assert q.items() == (item,)
            assert q.resolved() == (item,)
            assert q.count() == 1
            # A new resolution continues the sequence, deterministically.
            other = _item(correlation_id="c")
            q.enqueue(other)
            new_seq, _ = q.resolve(other.item_hash(), "accepted")
            assert new_seq > resolved_seq

    def test_status_refuses_unknown_item(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q, pytest.raises(ReviewError):
            q.status("a" * 64)


class TestEnqueueForReview:
    def test_verified_low_confidence_is_enqueued(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            response = Response(
                correlation_id="c1",
                kind=RESPONSE_RESULT,
                value=1,
                verified=True,
                node="model",
                confidence=0.5,
                model_record=ZERO_HASH,
            )
            item = enqueue_for_review(q, response, domain="model")
            assert item is not None
            assert item.correlation_id == "c1"
            assert item.confidence == 0.5
            assert "model-record:" + ZERO_HASH in item.provenance
            assert "node:model" in item.provenance

    def test_verified_without_confidence_is_not_enqueued(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            response = Response(
                correlation_id="c2", kind=RESPONSE_RESULT, value=1, verified=True
            )
            assert enqueue_for_review(q, response, domain="model") is None
            assert q.count() == 0

    def test_unverified_is_enqueued_at_zero_confidence(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            response = Response(
                correlation_id="c3", kind=RESPONSE_ERROR, error="boom", verified=False
            )
            item = enqueue_for_review(q, response, domain="model")
            assert item is not None
            assert item.confidence == 0.0


class TestModelAgentReviewWiring:
    def test_model_call_is_queued_when_a_queue_is_supplied(
        self, tmp_path: Path
    ) -> None:
        config = AgentConfig(identity="model", parent_endpoint="inproc://p")
        with ReviewQueue(tmp_path / "review.db") as q:
            agent = ModelAgent(config, review_queue=q)
            response = agent._handle(
                Directive(
                    correlation_id="run-1",
                    kind=DIRECTIVE_RUN,
                    payload={"task": TASK_MODEL, "args": {"prompt": "hi"}},
                )
            )
            assert response.verified is True
            pending = q.pending()
            assert len(pending) == 1
            assert pending[0].correlation_id == "run-1"
            assert pending[0].domain == TASK_MODEL
            assert 0.0 < pending[0].confidence < 1.0

    def test_model_call_is_not_queued_without_a_queue(self) -> None:
        config = AgentConfig(identity="model", parent_endpoint="inproc://p")
        agent = ModelAgent(config)
        response = agent._handle(
            Directive(
                correlation_id="run-2",
                kind=DIRECTIVE_RUN,
                payload={"task": TASK_MODEL, "args": {"prompt": "hi"}},
            )
        )
        assert response.verified is True


def _double(value: int) -> int:
    return value * 2


class TestResponseConfidence:
    """Every response carries a deterministic confidence (SPEC-0002 §0, §4.4)."""

    def test_effective_confidence_rules(self) -> None:
        verified = Response(correlation_id="c", kind=RESPONSE_RESULT, verified=True)
        failed = Response(correlation_id="c", kind=RESPONSE_ERROR, verified=False)
        scored = Response(
            correlation_id="c", kind=RESPONSE_RESULT, verified=True, confidence=0.5
        )
        assert effective_confidence(verified) == 1.0
        assert effective_confidence(failed) == 0.0
        assert effective_confidence(scored) == 0.5

    def test_stamp_is_identity_when_already_scored(self) -> None:
        scored = Response(
            correlation_id="c", kind=RESPONSE_RESULT, verified=True, confidence=0.9
        )
        assert stamp_confidence(scored) is scored

    def test_stamp_sets_full_confidence_for_verified(self) -> None:
        stamped = stamp_confidence(
            Response(correlation_id="c", kind=RESPONSE_RESULT, verified=True)
        )
        assert stamped.confidence == 1.0

    def test_driver_run_response_carries_confidence(self) -> None:
        with CbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21})
            assert resp.verified is True
            assert resp.value == 42
            assert resp.confidence == 1.0

    def test_driver_error_response_carries_zero_confidence(self) -> None:
        with CbpDriver() as driver:
            resp = driver.run("ghost_task", {})
            assert resp.verified is False
            assert resp.confidence == 0.0

    def test_driver_enqueues_subthreshold_run_outcome(self, tmp_path: Path) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            with CbpDriver(review_queue=q) as driver:
                driver.spawn("model", kind="model")
                resp = driver.run(TASK_MODEL, {"prompt": "hi"}, child="model")
                assert resp.verified is True
                assert resp.confidence is not None
                assert resp.confidence < 1.0
            pending = q.pending()
            assert len(pending) == 1
            assert pending[0].domain == TASK_MODEL
            assert pending[0].confidence < 1.0

    def test_driver_does_not_enqueue_full_confidence_run(
        self, tmp_path: Path
    ) -> None:
        with ReviewQueue(tmp_path / "review.db") as q:
            with CbpDriver(review_queue=q) as driver:
                driver.register("double", _double)
                driver.configure(tasks=("double",))
                resp = driver.run("double", {"value": 21})
                assert resp.confidence == 1.0
            assert q.count() == 0
