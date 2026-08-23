"""Tests for the micro-payment / settlement adapter (``fbp/settlement.py``).

This is the opt-in, external seam that settles the per-run cost of a domain
expert (the paid tier of Network of Experts). These tests prove the contract is
deterministic and fail-closed:

- a settlement never happens without an explicit operator grant,
- a cost exceeding the grant's cap fails closed,
- a domain mismatch fails closed,
- a negative cost fails closed,
- the stub provider is offline and deterministic (CI-safe).
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.experts import EXPERT_DETERMINISTIC, CostAccount
from agent_centric.fbp.settlement import (
    Settlement,
    SettlementError,
    SettlementGrant,
    StubSettlementProvider,
    settle_run,
)


def _account(domain: str = "bill-extract", cost: int = 10) -> CostAccount:
    return CostAccount(
        domain=domain, kind=EXPERT_DETERMINISTIC, cost=cost, residue=0.1
    )


def _grant(domain: str = "bill-extract", max_amount: int = 100) -> SettlementGrant:
    return SettlementGrant(domain=domain, max_amount=max_amount, note="budget")


class TestSettleRun:
    def test_settles_within_grant(self) -> None:
        s = settle_run(_account(cost=10), _grant(max_amount=100))
        assert isinstance(s, Settlement)
        assert s.domain == "bill-extract"
        assert s.amount == 10
        assert s.status == "settled"

    def test_never_auto_charges_without_grant(self) -> None:
        # A grant is the operator's explicit authorization; without one the
        # entry point has no grant to check against, so it must fail closed.
        with pytest.raises(SettlementError):
            settle_run(_account(), _grant(domain="other-domain"))

    def test_domain_mismatch_fails_closed(self) -> None:
        with pytest.raises(SettlementError, match="mismatch"):
            settle_run(_account(domain="a"), _grant(domain="b"))

    def test_cost_exceeding_grant_fails_closed(self) -> None:
        with pytest.raises(SettlementError, match="exceeds"):
            settle_run(_account(cost=500), _grant(max_amount=100))

    def test_negative_cost_fails_closed(self) -> None:
        with pytest.raises(SettlementError, match="negative"):
            settle_run(_account(cost=-5), _grant(max_amount=100))

    def test_custom_provider_is_used(self) -> None:
        class _FakeProvider:
            def charge(self, account, grant):
                return Settlement(
                    domain=account.domain,
                    amount=account.cost,
                    settlement_id="real-settle:1",
                    status="settled",
                    via="fake-x402",
                )

        s = settle_run(_account(cost=10), _grant(), provider=_FakeProvider())
        assert s.settlement_id == "real-settle:1"
        assert s.via == "fake-x402"

    def test_provider_failure_fails_closed(self) -> None:
        class _BoomProvider:
            def charge(self, account, grant):
                raise RuntimeError("provider down")

        with pytest.raises(SettlementError, match="settlement failed"):
            settle_run(_account(), _grant(), provider=_BoomProvider())


class TestStubSettlementProvider:
    def test_deterministic(self) -> None:
        p = StubSettlementProvider()
        a = _account(cost=10)
        g = _grant(max_amount=100)
        assert p.charge(a, g) == p.charge(a, g)

    def test_rejects_over_cap(self) -> None:
        p = StubSettlementProvider()
        with pytest.raises(SettlementError, match="exceeds"):
            p.charge(_account(cost=500), _grant(max_amount=100))

    def test_to_dict(self) -> None:
        s = Settlement(domain="d", amount=10, settlement_id="s1", status="settled", via="stub")
        d = s.to_dict()
        assert d["settlement_id"] == "s1"
        assert d["amount"] == 10

    def test_grant_to_dict(self) -> None:
        g = SettlementGrant(domain="d", max_amount=50, note="budget")
        assert g.to_dict()["max_amount"] == 50