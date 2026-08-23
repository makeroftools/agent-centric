"""Tests for end-to-end expert provisioning (``fbp/provision.py``).

This ties select -> plan -> train -> account -> settle into one deterministic,
workable path. These tests prove the flow is fail-closed and that the default
stub keeps it offline and CI-safe.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.experts import (
    EXPERT_DETERMINISTIC,
    EXPERT_HUMAN,
    CostLedger,
    Domain,
)
from agent_centric.fbp.provision import (
    ProvisionError,
    ProvisionResult,
    provision_expert,
)
from agent_centric.fbp.settlement import SettlementGrant
from agent_centric.fbp.slm import SlmError, SlmExpert, SlmSpec


def _domain(output_fields=("vendor", "amount_cents", "due_date")) -> Domain:
    return Domain(
        id="bill-extract",
        name="bill extraction",
        output_fields=tuple(output_fields),
    )


def _corpus(n: int = 100) -> list[str]:
    return [f"vendor GasCo amount {i}" for i in range(n)]


def _stub_expert(domain: Domain, corpus: list[str], spec: SlmSpec) -> SlmExpert:
    return SlmExpert(
        domain=domain.id,
        base_model=spec.base_model,
        method=spec.method,
        corpus=f"test-corpus:{domain.id}",
        dataset_size=min(len(corpus), spec.max_examples),
        benchmark=0.0,
        artifact="test://expert-1",
    )


class TestProvisionExpert:
    def test_deterministic_selection_needs_no_training(self) -> None:
        # A low-residue domain is deterministic; no training, no charge.
        result = provision_expert(_domain(), _corpus())
        assert isinstance(result, ProvisionResult)
        assert result.selection == EXPERT_DETERMINISTIC
        assert result.plan is None
        assert result.expert is None
        assert result.settlement is None
        assert result.account.cost == 0

    def test_unverifiable_domain_is_human(self) -> None:
        result = provision_expert(_domain(output_fields=()), _corpus())
        assert result.selection == EXPERT_HUMAN
        assert result.expert is None

    def test_learned_selection_trains_and_accounts(self) -> None:
        # A high-residue domain warrants a learned expert; the stub trains it.
        d = Domain(id="bill-extract", name="bill extraction",
                   output_fields=("vendor",), residue=0.8, determinism=0.5)
        ledger = CostLedger()
        result = provision_expert(d, _corpus(), ledger=ledger)
        assert result.selection == "learned"
        assert result.plan is not None
        assert result.expert is not None
        assert result.expert.domain == "bill-extract"
        assert result.account.cost > 0
        assert ledger.runs(domain="bill-extract") == 1

    def test_settles_under_grant(self) -> None:
        d = Domain(id="bill-extract", name="bill extraction",
                   output_fields=("vendor",), residue=0.8, determinism=0.5)
        grant = SettlementGrant(domain="bill-extract", max_amount=100)
        result = provision_expert(d, _corpus(), grant=grant)
        assert result.settlement is not None
        assert result.settlement.domain == "bill-extract"
        assert result.settlement.status == "settled"

    def test_provider_impl_is_used(self) -> None:
        d = Domain(id="bill-extract", name="bill extraction",
                   output_fields=("vendor",), residue=0.8, determinism=0.5)
        result = provision_expert(d, _corpus(), provider_impl=_StubImpl())
        assert result.expert is not None
        assert result.expert.artifact == "custom://expert"

    def test_over_budget_fails_closed(self) -> None:
        d = Domain(id="bill-extract", name="bill extraction",
                   output_fields=("vendor",), residue=0.8, determinism=0.5)
        with pytest.raises(ProvisionError, match="exceeds the budget"):
            provision_expert(d, _corpus(50_000), budget=5)

    def test_provider_failure_fails_closed(self) -> None:
        d = Domain(id="bill-extract", name="bill extraction",
                   output_fields=("vendor",), residue=0.8, determinism=0.5)

        class _Boom:
            def train(self, domain, corpus, spec):
                raise SlmError("provider down")

        with pytest.raises(ProvisionError, match="training failed"):
            provision_expert(d, _corpus(), provider_impl=_Boom())

    def test_to_dict_is_json_safe(self) -> None:
        result = provision_expert(_domain(), _corpus())
        d = result.to_dict()
        assert d["selection"] == EXPERT_DETERMINISTIC
        assert d["account"]["cost"] == 0


class _StubImpl:
    """A custom SlmProvider used to prove provider_impl is honored."""

    def train(self, domain, corpus, spec):
        return SlmExpert(
            domain=domain.id,
            base_model=spec.base_model,
            method=spec.method,
            corpus="custom",
            dataset_size=len(corpus),
            benchmark=0.0,
            artifact="custom://expert",
        )