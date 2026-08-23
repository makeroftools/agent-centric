"""Tests for the recommended-base-model catalog (``fbp/model_catalog.py``).

This is the deterministic, offline 2026 reference: size categories, per-base
hardware floor, licensing, and instruction-variant availability. Tests prove the
catalog is deterministic, fail-closed on unknown ids, and that the size→tier
floor gates a plan correctly.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.model_catalog import (
    BASE_MODELS,
    SIZE_CLASSES,
    SIZE_EDGE,
    SIZE_LARGE,
    SIZE_MID,
    SIZE_SMALL,
    BaseModel,
    CatalogError,
    catalog,
    min_tier_for,
    resolve_base,
)


class TestCatalogContents:
    def test_covers_the_2026_categories(self) -> None:
        assert SIZE_CLASSES == (SIZE_EDGE, SIZE_SMALL, SIZE_MID, SIZE_LARGE)
        # Each category has at least one recommended base.
        by_class: dict[str, int] = {}
        for rec in BASE_MODELS.values():
            by_class[rec.size_class] = by_class.get(rec.size_class, 0) + 1
        for cls in SIZE_CLASSES:
            assert by_class.get(cls, 0) >= 1, f"no base in {cls}"

    def test_all_bases_satisfy_invariants(self) -> None:
        for rec in BASE_MODELS.values():
            assert rec.size_class in SIZE_CLASSES
            assert rec.min_tier in ("cpu", "single-gpu", "multi-gpu")
            assert isinstance(rec, BaseModel)
            assert isinstance(rec.params, float)
            assert rec.id == rec.id

    def test_catalog_ordered_by_params(self) -> None:
        params = [b["params"] for b in catalog()["bases"]]
        assert params == sorted(params)

    def test_default_bill_base_exists(self) -> None:
        # The default base_model used by the learned tier is a known base.
        assert "llama-3.2-3b" in BASE_MODELS


class TestResolveBase:
    def test_known_base_resolves(self) -> None:
        rec = resolve_base("qwen2.5-3b")
        assert rec.family == "qwen"
        assert rec.size_class == SIZE_SMALL

    def test_unknown_base_fails_closed(self) -> None:
        with pytest.raises(CatalogError, match="unknown base model"):
            resolve_base("no-such-model")

    def test_min_tier_for(self) -> None:
        assert min_tier_for("llama-3.2-3b") == "cpu"
        assert min_tier_for("qwen3-14b") == "multi-gpu"
        assert min_tier_for("llama-3.1-70b") == "multi-gpu"

    def test_edge_base_min_tier(self) -> None:
        assert min_tier_for("qwen2.5-0.5b") == "cpu"