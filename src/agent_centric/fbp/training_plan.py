"""Deterministic training-plan tier for the learned expert (Network of Experts).

This is the **strategic provisioning decision**: given a domain, a corpus size,
and a training recipe, choose *which hardware tier* should train the expert and
bound its cost — deterministically and fail-closed. The actual execution is an
external, opt-in provider (see ``providers.py``); this module only *plans*.

The plan is a pure function of measurable inputs (corpus size, method, budget),
so it is deterministic and testable. It never provisions anything itself; it
produces a ``TrainingPlan`` whose ``spec`` (an ``SlmSpec``) the caller hands to a
provider. This is the same discipline as ``select_expert``: the core *decides*,
the provider *executes*.

Provisioning is a **grant, not a reflex** — a training run must never spin up
hardware opportunistically. ``plan_training`` therefore validates the plan
against an explicit budget (the natural source is a ``SettlementGrant``) and
fails closed if the estimated cost exceeds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .experts import Domain
from .slm import SlmSpec


class TrainingError(ValueError):
    """A training-plan input violated its contract (fail-closed)."""


# The hardware tiers, ordered from cheapest to most capable. A tier is chosen
# deterministically from the corpus size and the adaptation method.
TIER_CPU = "cpu"
TIER_SINGLE_GPU = "single-gpu"
TIER_MULTI_GPU = "multi-gpu"
TIER_KINDS = (TIER_CPU, TIER_SINGLE_GPU, TIER_MULTI_GPU)

#: Corpus-size thresholds that move a plan up the ladder.
_CPU_MAX_EXAMPLES = 1_000
_SINGLE_GPU_MAX_EXAMPLES = 10_000
#: Methods that need a deeper (multi-GPU) pass regardless of corpus size.
_DEEP_METHODS = frozenset({"dapt", "dpo"})


@dataclass(frozen=True)
class TrainingTier:
    """One hardware tier a training run may use.

    Attributes:
        id: The tier id (``cpu`` / ``single-gpu`` / ``multi-gpu``).
        label: A short human-readable label (e.g. the GPU class).
        gpu: The GPU class to request (``None`` for CPU-only).
        max_examples: The largest corpus this tier should train in one pass.
        est_cost: The estimated cost of a run in the caller's chosen unit.
        description: A short explanation of when this tier is warranted.
    """

    id: str
    label: str
    gpu: str | None
    max_examples: int
    est_cost: int
    description: str


TIERS: dict[str, TrainingTier] = {
    TIER_CPU: TrainingTier(
        id=TIER_CPU,
        label="CPU / tiny GPU",
        gpu=None,
        max_examples=_CPU_MAX_EXAMPLES,
        est_cost=1,
        description="small corpus; QLoRA on CPU or a tiny GPU is enough",
    ),
    TIER_SINGLE_GPU: TrainingTier(
        id=TIER_SINGLE_GPU,
        label="Single GPU (A10G)",
        gpu="A10G",
        max_examples=_SINGLE_GPU_MAX_EXAMPLES,
        est_cost=10,
        description="the QLoRA sweet spot for a 3B-7B base on a few thousand examples",
    ),
    TIER_MULTI_GPU: TrainingTier(
        id=TIER_MULTI_GPU,
        label="Multi-GPU (H100 x2)",
        gpu="H100",
        max_examples=100_000,
        est_cost=50,
        description="deep adaptation (DAPT/SFT/DPO) or a large corpus",
    ),
}


@dataclass(frozen=True)
class TrainingPlan:
    """A deterministic decision of how (and at what cost) to train an expert.

    Attributes:
        domain: The domain id this plan trains for.
        tier: The chosen hardware tier id.
        corpus_size: The corpus size the plan was made for.
        method: The adaptation method (``qlora`` / ``dapt`` / ``sft`` / ``dpo``).
        estimated_cost: The estimated cost in the caller's chosen unit.
        spec: The ``SlmSpec`` to hand to a provider (recipe + capped examples).
        within_budget: True when the plan fits the given budget (or none given).
    """

    domain: str
    tier: str
    corpus_size: int
    method: str
    estimated_cost: int
    spec: SlmSpec
    within_budget: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "tier": self.tier,
            "tier_label": TIERS[self.tier].label,
            "corpus_size": self.corpus_size,
            "method": self.method,
            "estimated_cost": self.estimated_cost,
            "within_budget": self.within_budget,
            "spec": {
                "base_model": self.spec.base_model,
                "method": self.spec.method,
                "max_examples": self.spec.max_examples,
                "quantize": self.spec.quantize,
            },
        }


def _choose_tier(corpus_size: int, method: str, base_min_tier: str | None = None) -> str:
    """Pick the hardware tier deterministically from corpus + method + base size.

    The base model's ``min_tier`` is a floor: a ``mid``-size base cannot train
    below ``single-gpu``/``multi-gpu`` regardless of corpus size. The ladder is
    the max of the corpus/method decision and the base's own floor.
    """
    if method in _DEEP_METHODS or corpus_size > _SINGLE_GPU_MAX_EXAMPLES:
        tier = TIER_MULTI_GPU
    elif corpus_size > _CPU_MAX_EXAMPLES:
        tier = TIER_SINGLE_GPU
    else:
        tier = TIER_CPU
    if base_min_tier is not None:
        # Respect the base's hardware floor (cpu < single-gpu < multi-gpu).
        order = (TIER_CPU, TIER_SINGLE_GPU, TIER_MULTI_GPU)
        if order.index(base_min_tier) > order.index(tier):
            tier = base_min_tier
    return tier


def plan_training(
    domain: Domain,
    corpus_size: int,
    *,
    method: str = "qlora",
    base_model: str = "llama-3.2-3b",
    max_examples: int = 10_000,
    quantize: bool = True,
    budget: int | None = None,
) -> TrainingPlan:
    """Plan which hardware tier should train a domain expert (fail-closed).

    Deterministic and fail-closed:

    - A negative corpus size raises ``TrainingError``.
    - The tier is chosen from ``corpus_size`` and ``method`` (a pure function).
    - ``spec.max_examples`` is capped at the tier's ``max_examples`` so a plan
      never asks a provider to train beyond the tier's one-pass capacity.
    - When ``budget`` is given and the estimated cost exceeds it, the plan
      fails closed (``TrainingError``) rather than silently over-spending.

    Args:
        domain: The domain to train for (must be deterministically achievable).
        corpus_size: The number of training examples available.
        method: The adaptation method (``qlora`` / ``dapt`` / ``sft`` / ``dpo``).
        base_model: The open base model id.
        max_examples: The caller's requested example cap (further capped by tier).
        quantize: Whether to quantize for deployment.
        budget: An optional cost cap (the caller's unit). The natural source is
            a ``SettlementGrant.max_amount``. ``None`` means no explicit cap.

    Returns:
        A deterministic ``TrainingPlan``.

    Raises:
        TrainingError: If the domain is unverifiable, the corpus size is
            negative, or the estimated cost exceeds ``budget``.
    """
    if not domain.achievable:
        raise TrainingError(
            f"domain {domain.id!r} has no verifiable output; cannot plan training"
        )
    if corpus_size < 0:
        raise TrainingError(f"corpus size must be non-negative (got {corpus_size})")
    # Resolve the base model from the catalog (fail-closed on an unknown base),
    # and gate the hardware tier by the base's size-class floor.
    from .model_catalog import CatalogError, min_tier_for

    try:
        base_min_tier = min_tier_for(base_model)
    except CatalogError as exc:
        raise TrainingError(str(exc)) from exc
    tier_id = _choose_tier(corpus_size, method, base_min_tier=base_min_tier)
    tier = TIERS[tier_id]
    capped = min(max_examples, tier.max_examples)
    spec = SlmSpec(
        base_model=base_model,
        method=method,
        max_examples=capped,
        quantize=quantize,
    )
    est = tier.est_cost
    within = budget is None or est <= budget
    if budget is not None and est > budget:
        raise TrainingError(
            f"training plan for {domain.id!r} exceeds the budget: "
            f"estimated {est} > budget {budget} (tier {tier_id!r})"
        )
    return TrainingPlan(
        domain=domain.id,
        tier=tier_id,
        corpus_size=corpus_size,
        method=method,
        estimated_cost=est,
        spec=spec,
        within_budget=within,
    )