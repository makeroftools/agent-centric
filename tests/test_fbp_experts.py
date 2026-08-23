"""Tests for expert selection + cost/residue ledger (``fbp/experts.py``).

This is the deterministic core of "Network of Experts AI": a component's domain
is assigned the right kind of expert (deterministic method -> learned SLM ->
human) and its cost + irreducible residue are accounted per run. These tests are
pure, deterministic, and offline.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.experts import (
    EXPERT_DETERMINISTIC,
    EXPERT_HUMAN,
    EXPERT_KINDS,
    EXPERT_LEARNED,
    CostAccount,
    CostLedger,
    Domain,
    ExpertsError,
    domain_from_dict,
    select_expert,
)


def _domain(
    *,
    output_fields=("vendor", "amount_cents", "due_date"),
    determinism=None,
    residue=None,
) -> Domain:
    return Domain(
        id="bill-extract",
        name="bill extraction",
        input_schema={"fields": {"vendor": {"type": "str", "required": True}}},
        output_fields=tuple(output_fields),
        determinism=determinism,
        residue=residue,
    )


class TestDomain:
    def test_achievable_when_verifiable(self) -> None:
        assert _domain().achievable is True

    def test_not_achievable_when_no_output_fields(self) -> None:
        d = _domain(output_fields=())
        assert d.achievable is False

    def test_to_dict_roundtrip(self) -> None:
        d = _domain(determinism=0.8, residue=0.1)
        back = domain_from_dict(d.to_dict())
        assert back == d

    def test_domain_from_dict_fails_closed_on_bad(self) -> None:
        with pytest.raises(ExpertsError):
            domain_from_dict({"name": "no id"})
        with pytest.raises(ExpertsError):
            domain_from_dict({"id": "x"})


class TestSelectExpert:
    def test_low_residue_is_deterministic(self) -> None:
        s = select_expert(_domain(residue=0.1))
        assert s.kind == EXPERT_DETERMINISTIC
        assert s.warranted is False

    def test_high_residue_warrants_learned(self) -> None:
        s = select_expert(_domain(determinism=0.5, residue=0.8))
        assert s.kind == EXPERT_LEARNED
        assert s.warranted is True

    def test_unverifiable_is_human(self) -> None:
        s = select_expert(_domain(output_fields=()))
        assert s.kind == EXPERT_HUMAN

    def test_must_learn_forces_learned(self) -> None:
        s = select_expert(_domain(residue=0.1), must_learn=True)
        assert s.kind == EXPERT_LEARNED
        assert s.warranted is True

    def test_residue_above_threshold_with_high_determinism_is_human(self) -> None:
        # Residue > max, but determinism is high (domain itself is reliable) —
        # the residue is not safely auto-resolvable by a learned expert, so it
        # reaches a human rather than being silently learned.
        s = select_expert(_domain(determinism=0.98, residue=0.35))
        assert s.kind == EXPERT_HUMAN

    def test_residue_clamped(self) -> None:
        # residue 3.0 clamps to 1.0 (> max_residue); with determinism left at its
        # high default (>= ceiling) the residue is not safely auto-resolvable.
        s = select_expert(_domain(residue=3.0))
        assert s.kind == EXPERT_HUMAN
        assert s.kind in EXPERT_KINDS

    def test_high_residue_with_low_determinism_warrants_learned(self) -> None:
        s = select_expert(_domain(determinism=0.4, residue=0.8))
        assert s.kind == EXPERT_LEARNED
        assert s.warranted is True


class TestCostLedger:
    def test_accumulates_and_filters(self) -> None:
        led = CostLedger(max_entries=100)
        led.record(
            CostAccount(domain="bills-extract", kind=EXPERT_DETERMINISTIC, cost=10, residue=0.1)
        )
        led.record(
            CostAccount(domain="bills-extract", kind=EXPERT_LEARNED, cost=500, residue=0.05)
        )
        led.record(
            CostAccount(domain="other", kind=EXPERT_DETERMINISTIC, cost=7, residue=0.0)
        )
        assert led.total_cost() == 517
        assert led.total_cost(domain="bills-extract") == 510
        assert led.runs(domain="bills-extract") == 2
        assert round(led.residue_avg(domain="bills-extract"), 3) == 0.075

    def test_empty_residue_avg_is_zero(self) -> None:
        led = CostLedger()
        assert led.residue_avg() == 0.0
        assert led.total_cost() == 0

    def test_rejects_unknown_kind(self) -> None:
        led = CostLedger()
        with pytest.raises(ExpertsError):
            led.record(CostAccount(domain="d", kind="magic", cost=1, residue=0.0))

    def test_bounds_entries(self) -> None:
        led = CostLedger(max_entries=5)
        for i in range(20):
            led.record(CostAccount(domain="d", kind=EXPERT_DETERMINISTIC, cost=i, residue=0.0))
        assert len(led.entries()) == 5

    def test_cost_account_to_dict(self) -> None:
        a = CostAccount(domain="d", kind=EXPERT_HUMAN, cost=3, residue=0.9, via="human-op")
        assert a.to_dict()["via"] == "human-op"