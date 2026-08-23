"""End-to-end expert provisioning (select → plan → train → account → settle).

This ties the Network-of-Experts pieces into one deterministic, workable path:

1. **select** — ``select_expert`` decides whether the domain warrants a learned
   (SLM) expert.
2. **plan** — ``plan_training`` picks the hardware tier and bounds the cost.
3. **train** — an external provider (``SlmProvider``) trains the expert.
4. **account** — the ``CostLedger`` records the run's cost + residue.
5. **settle** — the ``Settlement`` charges the cost under an explicit grant.

The whole flow is deterministic except the external training execution, and it
is fail-closed: an unverifiable domain, an over-budget plan, a provider failure,
or a settlement without a matching grant all abort with an explicit error. The
provider and settlement are opt-in; the default stub keeps the flow offline and
CI-safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .experts import CostAccount, CostLedger, Domain, select_expert
from .providers import get_provider
from .settlement import Settlement, SettlementGrant, settle_run
from .slm import SlmError, SlmExpert, SlmProvider
from .training_plan import TrainingError, plan_training


class ProvisionError(ValueError):
    """An expert-provisioning flow violated its contract (fail-closed)."""


@dataclass(frozen=True)
class ProvisionResult:
    """The outcome of a provisioning run.

    Attributes:
        domain: The domain id provisioned.
        selection: The expert-selection kind (``deterministic``/``learned``/``human``).
        plan: The chosen hardware tier (``cpu``/``single-gpu``/``multi-gpu``), or
            ``None`` when no training was warranted.
        expert: The trained ``SlmExpert`` (when a learned expert was trained).
        account: The recorded ``CostAccount``.
        settlement: The ``Settlement`` (when a charge was settled).
    """

    domain: str
    selection: str
    plan: str | None
    expert: SlmExpert | None
    account: CostAccount
    settlement: Settlement | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "selection": self.selection,
            "plan": self.plan,
            "expert": self.expert.to_dict() if self.expert else None,
            "account": self.account.to_dict(),
            "settlement": self.settlement.to_dict() if self.settlement else None,
        }


def provision_expert(
    domain: Domain,
    corpus: list[str],
    *,
    provider: str = "stub",
    provider_impl: SlmProvider | None = None,
    grant: SettlementGrant | None = None,
    ledger: CostLedger | None = None,
    method: str = "qlora",
    base_model: str = "llama-3.2-3b",
    max_examples: int = 10_000,
    quantize: bool = True,
    budget: int | None = None,
) -> ProvisionResult:
    """Provision a domain expert end-to-end (deterministic, fail-closed).

    Args:
        domain: The domain to provision (must be deterministically achievable).
        corpus: The training corpus (used only when a learned expert is warranted).
        provider: The named provider to use (``stub``/``modal``/``runpod``/``replicate``).
        provider_impl: An explicit ``SlmProvider`` to use instead of the named one
            (e.g. a Modal provider with an injected http_client). When given, it
            takes precedence over ``provider``.
        grant: An optional ``SettlementGrant`` authorizing the charge. When the
            flow settles a cost, a grant is required (fail-closed otherwise).
        ledger: An optional ``CostLedger`` to record the run's account.
        method/base_model/max_examples/quantize: The training recipe.
        budget: An optional cost cap for the training plan.

    Returns:
        A ``ProvisionResult`` describing what was selected, planned, trained,
        accounted, and settled.

    Raises:
        ProvisionError: If the flow cannot proceed (unverifiable domain,
            over-budget plan, provider failure, or missing grant).
    """
    # 1. Select the expert kind.
    selection = select_expert(domain)
    if selection.kind != "learned":
        # A deterministic or human selection needs no training. Account a
        # zero-cost run and return (no charge, no grant needed).
        account = CostAccount(
            domain=domain.id, kind=selection.kind, cost=0, residue=0.0
        )
        if ledger is not None:
            ledger.record(account)
        return ProvisionResult(
            domain=domain.id,
            selection=selection.kind,
            plan=None,
            expert=None,
            account=account,
            settlement=None,
        )

    # 2. Plan the training (deterministic hardware tier + cost bound).
    try:
        plan = plan_training(
            domain,
            len(corpus),
            method=method,
            base_model=base_model,
            max_examples=max_examples,
            quantize=quantize,
            budget=budget,
        )
    except TrainingError as exc:
        raise ProvisionError(str(exc)) from exc

    # 3. Train via the provider (opt-in; default stub is offline/CI-safe).
    impl = provider_impl or get_provider(provider)
    try:
        expert = impl.train(domain, corpus, plan.spec)
    except SlmError as exc:
        raise ProvisionError(f"training failed: {exc}") from exc

    # 4. Account the run's cost + residue.
    account = CostAccount(
        domain=domain.id,
        kind="learned",
        cost=plan.estimated_cost,
        residue=0.0,
        via=expert.artifact,
    )
    if ledger is not None:
        ledger.record(account)

    # 5. Settle the cost under the grant (fail-closed without a grant).
    settlement: Settlement | None = None
    if grant is not None:
        settlement = settle_run(account, grant)
    return ProvisionResult(
        domain=domain.id,
        selection=selection.kind,
        plan=plan.tier,
        expert=expert,
        account=account,
        settlement=settlement,
    )