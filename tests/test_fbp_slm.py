"""Tests for the domain-expert SLM provider contract (``fbp/slm.py``).

This is the ``learned`` tier of Network of Experts: an opt-in external provider
turns a domain corpus into a trained per-domain expert, carrying full
provenance. The default stub is offline and deterministic (CI-safe). These tests
are pure and offline.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.experts import Domain
from agent_centric.fbp.slm import (
    SlmError,
    SlmExpert,
    SlmSpec,
    StubSlmProvider,
    build_domain_expert,
)


def _domain(*, achievable=True) -> Domain:
    return Domain(
        id="bill-extract",
        name="bill extraction",
        input_schema={"fields": {"vendor": {"type": "str", "required": True}}},
        output_fields=("vendor", "amount_cents", "due_date") if achievable else (),
        determinism=0.5,
        residue=0.8,
    )


class TestStubSlmProvider:
    def test_trains_deterministic_expert(self) -> None:
        provider = StubSlmProvider()
        expert = provider.train(_domain(), ["corpus line 1", "corpus line 2"], SlmSpec())
        assert expert.domain == "bill-extract"
        assert expert.dataset_size == 2
        assert expert.base_model == "llama-3.2-3b"
        assert expert.method == "qlora"
        assert expert.artifact.startswith("stub://")

    def test_empty_corpus_fails_closed(self) -> None:
        provider = StubSlmProvider()
        with pytest.raises(SlmError):
            provider.train(_domain(), [], SlmSpec())

    def test_respects_max_examples(self) -> None:
        provider = StubSlmProvider()
        corpus = [f"line {i}" for i in range(50)]
        expert = provider.train(_domain(), corpus, SlmSpec(max_examples=10))
        assert expert.dataset_size == 10


class TestBuildDomainExpert:
    def test_builds_with_default_stub(self) -> None:
        expert = build_domain_expert(_domain(), ["a", "b", "c"])
        assert isinstance(expert, SlmExpert)
        assert expert.domain == "bill-extract"
        assert expert.dataset_size == 3

    def test_empty_corpus_fails_closed(self) -> None:
        with pytest.raises(SlmError):
            build_domain_expert(_domain(), [])

    def test_unverifiable_domain_fails_closed(self) -> None:
        with pytest.raises(SlmError):
            build_domain_expert(_domain(achievable=False), ["a"])

    def test_provider_failure_is_surfaced(self) -> None:
        class _Boom:
            def train(self, domain, corpus, spec):
                raise RuntimeError("gpu exploded")

        with pytest.raises(SlmError):
            build_domain_expert(_domain(), ["a"], provider=_Boom())

    def test_custom_spec_and_provider(self) -> None:
        class _Custom:
            def train(self, domain, corpus, spec):
                return SlmExpert(
                    domain=domain.id, base_model=spec.base_model, method=spec.method,
                    corpus="custom", dataset_size=len(corpus), benchmark=0.9,
                    artifact="custom://expert",
                )

        expert = build_domain_expert(
            _domain(), ["a"], provider=_Custom(), spec=SlmSpec(base_model="qwen-2.5-3b")
        )
        assert expert.base_model == "qwen-2.5-3b"
        assert expert.benchmark == 0.9

    def test_expert_to_dict(self) -> None:
        expert = build_domain_expert(_domain(), ["a"])
        d = expert.to_dict()
        assert d["domain"] == "bill-extract"
        assert "artifact" in d