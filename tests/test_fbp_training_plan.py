"""Tests for the deterministic training-plan tier (``fbp/training_plan.py``).

This is the strategic hardware-provisioning decision: given a domain, corpus
size, and recipe, choose which hardware tier trains the expert and bound its
cost — deterministically and fail-closed. These tests are pure and offline.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.experts import Domain
from agent_centric.fbp.training_plan import (
    TIER_CPU,
    TIER_KINDS,
    TIER_MULTI_GPU,
    TIER_SINGLE_GPU,
    TIERS,
    TrainingError,
    plan_training,
)


def _domain(output_fields=("vendor", "amount_cents", "due_date")) -> Domain:
    return Domain(
        id="bill-extract",
        name="bill extraction",
        output_fields=tuple(output_fields),
    )


class TestPlanTraining:
    def test_small_corpus_uses_cpu_tier(self) -> None:
        plan = plan_training(_domain(), corpus_size=100)
        assert plan.tier == TIER_CPU
        assert plan.within_budget is True

    def test_medium_corpus_uses_single_gpu(self) -> None:
        plan = plan_training(_domain(), corpus_size=5_000)
        assert plan.tier == TIER_SINGLE_GPU

    def test_large_corpus_uses_multi_gpu(self) -> None:
        plan = plan_training(_domain(), corpus_size=50_000)
        assert plan.tier == TIER_MULTI_GPU

    def test_deep_method_uses_multi_gpu(self) -> None:
        # DAPT is a deep method; even a small corpus moves to multi-GPU.
        plan = plan_training(_domain(), corpus_size=100, method="dapt")
        assert plan.tier == TIER_MULTI_GPU

    def test_spec_max_examples_capped_by_tier(self) -> None:
        # A caller asking for 100k examples on the CPU tier is capped to the
        # tier's one-pass capacity (1k), never over-asked.
        plan = plan_training(_domain(), corpus_size=100, max_examples=100_000)
        assert plan.spec.max_examples <= TIERS[plan.tier].max_examples

    def test_estimated_cost_matches_tier(self) -> None:
        plan = plan_training(_domain(), corpus_size=5_000)
        assert plan.estimated_cost == TIERS[TIER_SINGLE_GPU].est_cost

    def test_within_budget_when_under(self) -> None:
        plan = plan_training(_domain(), corpus_size=100, budget=10)
        assert plan.within_budget is True

    def test_exceeding_budget_fails_closed(self) -> None:
        with pytest.raises(TrainingError, match="exceeds the budget"):
            plan_training(_domain(), corpus_size=50_000, budget=5)

    def test_unverifiable_domain_fails_closed(self) -> None:
        with pytest.raises(TrainingError, match="no verifiable output"):
            plan_training(_domain(output_fields=()), corpus_size=100)

    def test_negative_corpus_fails_closed(self) -> None:
        with pytest.raises(TrainingError, match="non-negative"):
            plan_training(_domain(), corpus_size=-1)

    def test_to_dict_is_json_safe(self) -> None:
        plan = plan_training(_domain(), corpus_size=5_000)
        d = plan.to_dict()
        assert d["tier"] == TIER_SINGLE_GPU
        assert d["spec"]["method"] == "qlora"
        assert "tier_label" in d

    def test_tiers_are_deterministic_and_ordered(self) -> None:
        # The tier kinds are a stable, ordered tuple.
        assert TIER_KINDS == (TIER_CPU, TIER_SINGLE_GPU, TIER_MULTI_GPU)
        # Estimated cost increases with capability.
        costs = [TIERS[t].est_cost for t in TIER_KINDS]
        assert costs == sorted(costs)

class TestBaseSizeGating:
    """The tier ladder now accounts for the base model's size-class floor."""

    def test_mid_size_base_gates_from_cpu_to_single_gpu(self) -> None:
        # A 7B base cannot train on the cpu tier even for a tiny corpus.
        plan = plan_training(_domain(), corpus_size=100, base_model="llama-3.2-8b")
        assert plan.tier == TIER_SINGLE_GPU

    def test_large_mid_base_gates_to_multi_gpu(self) -> None:
        # A 14B base needs a multi-gpu pass regardless of corpus size.
        plan = plan_training(_domain(), corpus_size=100, base_model="qwen3-14b")
        assert plan.tier == TIER_MULTI_GPU

    def test_large_70b_base_gates_to_multi_gpu(self) -> None:
        plan = plan_training(_domain(), corpus_size=100, base_model="llama-3.1-70b")
        assert plan.tier == TIER_MULTI_GPU

    def test_small_base_can_still_use_cpu(self) -> None:
        # A small base (default) on a tiny corpus stays on cpu (unchanged).
        plan = plan_training(_domain(), corpus_size=100, base_model="llama-3.2-3b")
        assert plan.tier == TIER_CPU

    def test_unknown_base_fails_closed(self) -> None:
        with pytest.raises(TrainingError, match="unknown base model"):
            plan_training(_domain(), corpus_size=100, base_model="no-such-model")

    def test_deep_method_still_wins_even_on_tiny_base(self) -> None:
        # A deep method (dapt) on a small base still escalates to multi-gpu.
        plan = plan_training(
            _domain(), corpus_size=100, base_model="llama-3.2-3b", method="dapt"
        )
        assert plan.tier == TIER_MULTI_GPU
