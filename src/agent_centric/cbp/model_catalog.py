"""Recommended base models for domain adaptation — a deterministic catalog.

This is the **2026 reference catalog** (categorical by size / deployment class)
that the ``learned`` tier selects a *base model* from. It exists so the training
plan can account for the one dimension its tier ladder previously ignored:
**model size**. A 14B base cannot train on the same hardware tier as a 0.5B base
regardless of corpus size — so ``min_tier`` gating belongs in the plan.

The catalog is a **passive registry**: it only records *what* bases exist and
*where* they sit (size class, minimum tier, licensing). It never decides which
base to use — downstream planning (``plan_training``) does that from the caller's
explicit ``base_model`` choice, fail-closed on an unknown id.

Design rules (deterministic, offline, fail-closed):

- ``BaseModel`` is a frozen, validated record with a stable ``id``.
- ``SIZE_*`` are the five deployment classes from the 2026 categorical list.
- ``resolve_base(model_id)`` returns the record or raises ``CatalogError`` for an
  unknown id — never a silent guess.
- ``min_tier_for(model_id)`` is the hardware tier a base can at minimum train on
  (a ``min``-size base cannot go below ``single-gpu``/``mid``; an edge base can
  train anywhere).
- Everything is a pure function of the catalog; nothing reaches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Canonical size classes (2026 categorical recommendation).
SIZE_EDGE: Final = "edge"      # <= 2B
SIZE_SMALL: Final = "small"    # 2-4B
SIZE_MID: Final = "mid"        # 7-14B
SIZE_LARGE: Final = "large"    # >14B (used when more capacity is needed)
SIZE_CLASSES: Final = (SIZE_EDGE, SIZE_SMALL, SIZE_MID, SIZE_LARGE)

# Canonical hardware tiers (mirror training_plan).
TIER_CPU: Final = "cpu"
TIER_SINGLE_GPU: Final = "single-gpu"
TIER_MULTI_GPU: Final = "multi-gpu"


class CatalogError(ValueError):
    """A base-model catalog lookup failed (fail-closed)."""


@dataclass(frozen=True)
class BaseModel:
    """One recommended open base model for domain adaptation.

    Attributes:
        id: Stable, unique id (e.g. ``llama-3.2-3b``).
        family: The model family (``llama`` / ``qwen`` / ``gemma`` / ``mistral`` /
            ``phi`` / ``smol``).
        params: Approximate parameter count in billions (float).
        size_class: One of ``SIZE_CLASSES`` (edge / small / mid / large).
        min_tier: The smallest hardware tier that can train this base
            (cpu / single-gpu / multi-gpu).
        license_ok: True when the license is known permissive-for-domain-work.
        instruct_available: True when an instruction-tuned variant exists
            (preferred for SFT/DPO; base variants used for continued pretraining).
        note: A short human note (multilingual, reasoning, etc.).
    """

    id: str
    family: str
    params: float
    size_class: str
    min_tier: str
    license_ok: bool = True
    instruct_available: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "family": self.family,
            "params": self.params,
            "size_class": self.size_class,
            "min_tier": self.min_tier,
            "license_ok": self.license_ok,
            "instruct_available": self.instruct_available,
            "note": self.note,
        }


# The 2026 recommended base models, categorical by deployment class.
BASE_MODELS: dict[str, BaseModel] = {
    # --- Ultra-small / Edge (<=2B) ---
    "qwen2.5-0.5b": BaseModel(
        id="qwen2.5-0.5b", family="qwen", params=0.5, size_class=SIZE_EDGE,
        min_tier=TIER_CPU, note="strong multilingual; highly tunable",
    ),
    "qwen3-1.7b": BaseModel(
        id="qwen3-1.7b", family="qwen", params=1.7, size_class=SIZE_EDGE,
        min_tier=TIER_CPU, note="latest Qwen3 small; strong instruction-following",
    ),
    "llama-3.2-1b": BaseModel(
        id="llama-3.2-1b", family="llama", params=1.0, size_class=SIZE_EDGE,
        min_tier=TIER_CPU, note="efficient; large community",
    ),
    "smol": BaseModel(
        id="smol", family="smol", params=0.5, size_class=SIZE_EDGE,
        min_tier=TIER_CPU, note="lightweight inference focus",
    ),
    # --- Small (2-4B) ---
    "llama-3.2-3b": BaseModel(
        id="llama-3.2-3b", family="llama", params=3.0, size_class=SIZE_SMALL,
        min_tier=TIER_CPU, note="excellent general base; widely used for domain work",
    ),
    "qwen2.5-3b": BaseModel(
        id="qwen2.5-3b", family="qwen", params=3.0, size_class=SIZE_SMALL,
        min_tier=TIER_CPU, note="frequently tops fine-tuning benchmarks; multilingual",
    ),
    "qwen3-4b": BaseModel(
        id="qwen3-4b", family="qwen", params=4.0, size_class=SIZE_SMALL,
        min_tier=TIER_CPU, note="Qwen3 small; strong reasoning",
    ),
    "phi-4-mini": BaseModel(
        id="phi-4-mini", family="phi", params=3.8, size_class=SIZE_SMALL,
        min_tier=TIER_CPU, note="strong reasoning and math; clean licensing",
    ),
    "gemma-3-4b": BaseModel(
        id="gemma-3-4b", family="gemma", params=4.0, size_class=SIZE_SMALL,
        min_tier=TIER_CPU, note="multimodal options in later versions",
    ),
    # --- Mid-size (7-14B) — most practical for high-quality domain experts ---
    "llama-3.2-8b": BaseModel(
        id="llama-3.2-8b", family="llama", params=8.0, size_class=SIZE_MID,
        min_tier=TIER_SINGLE_GPU, note="strong ecosystem, long context",
    ),
    "qwen2.5-7b": BaseModel(
        id="qwen2.5-7b", family="qwen", params=7.0, size_class=SIZE_MID,
        min_tier=TIER_SINGLE_GPU, note="often best-in-class after fine-tuning",
    ),
    "qwen3-14b": BaseModel(
        id="qwen3-14b", family="qwen", params=14.0, size_class=SIZE_MID,
        min_tier=TIER_MULTI_GPU, note="strong reasoning at the mid ceiling",
    ),
    "gemma-3-12b": BaseModel(
        id="gemma-3-12b", family="gemma", params=12.0, size_class=SIZE_MID,
        min_tier=TIER_MULTI_GPU, note="solid performance and multimodal extensions",
    ),
    "mistral-7b": BaseModel(
        id="mistral-7b", family="mistral", params=7.0, size_class=SIZE_MID,
        min_tier=TIER_SINGLE_GPU, note="efficient architecture; permissive licensing",
    ),
    "mistral-nemo-12b": BaseModel(
        id="mistral-nemo-12b", family="mistral", params=12.0, size_class=SIZE_MID,
        min_tier=TIER_MULTI_GPU, note="12B; permissive licensing",
    ),
    "phi-4-14b": BaseModel(
        id="phi-4-14b", family="phi", params=14.0, size_class=SIZE_MID,
        min_tier=TIER_MULTI_GPU, note="strong overall NLP after adaptation",
    ),
    # --- Larger bases (when more capacity is needed) ---
    "llama-3.1-70b": BaseModel(
        id="llama-3.1-70b", family="llama", params=70.0, size_class=SIZE_LARGE,
        min_tier=TIER_MULTI_GPU, note="depth via distillation/quantization",
    ),
}


def resolve_base(model_id: str) -> BaseModel:
    """Return the catalog entry for ``model_id`` (fail-closed on unknown id).

    Raises:
        CatalogError: If ``model_id`` is not a known base in the catalog.
    """
    rec = BASE_MODELS.get(model_id)
    if rec is None:
        raise CatalogError(
            f"unknown base model {model_id!r}; expected one of {', '.join(sorted(BASE_MODELS))}"
        )
    return rec


def min_tier_for(model_id: str) -> str:
    """The minimum hardware tier that can train the named base (fail-closed)."""
    return resolve_base(model_id).min_tier


def catalog() -> dict[str, object]:
    """A read-only, deterministic summary of the whole catalog (for the page/CLI)."""
    return {
        "size_classes": list(SIZE_CLASSES),
        "bases": [
            b.to_dict()
            for b in sorted(BASE_MODELS.values(), key=lambda b: (b.params, b.id))
        ],
    }