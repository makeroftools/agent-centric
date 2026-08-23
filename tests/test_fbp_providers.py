"""Tests for the external training-provider adapters (``fbp/providers.py``).

This is the execution side of the learned tier: an operator-selectable registry
of providers (Modal / Runpod / Replicate) behind the ``SlmProvider`` contract.
The default is the offline stub; a real provider is opt-in and never runs in the
offline test suite. These tests prove the registry is deterministic and
fail-closed, and that each adapter serves the same contract.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.experts import Domain
from agent_centric.fbp.providers import (
    ModalSlmProvider,
    ProviderRegistry,
    ReplicateSlmProvider,
    RunpodSlmProvider,
    get_provider,
    provider_names,
)
from agent_centric.fbp.slm import SlmError, SlmSpec, StubSlmProvider


def _domain() -> Domain:
    return Domain(id="bill-extract", name="bill extraction", output_fields=("vendor",))


class TestProviderRegistry:
    def test_names_are_deterministic(self) -> None:
        names = provider_names()
        assert "stub" in names
        assert "modal" in names
        assert "runpod" in names
        assert "replicate" in names
        assert names == tuple(sorted(names))

    def test_default_is_stub(self) -> None:
        assert isinstance(get_provider("stub"), StubSlmProvider)

    def test_get_modal_returns_modal(self) -> None:
        assert isinstance(get_provider("modal"), ModalSlmProvider)

    def test_get_runpod_returns_runpod(self) -> None:
        assert isinstance(get_provider("runpod"), RunpodSlmProvider)

    def test_get_replicate_returns_replicate(self) -> None:
        assert isinstance(get_provider("replicate"), ReplicateSlmProvider)

    def test_unknown_provider_fails_closed(self) -> None:
        with pytest.raises(SlmError, match="unknown training provider"):
            get_provider("aws-sagemaker")

    def test_custom_registry(self) -> None:
        reg = ProviderRegistry()
        assert set(reg.names()) == {"stub", "modal", "runpod", "replicate"}


class TestAdaptersServeContract:
    """Each adapter implements SlmProvider.train -> SlmExpert (provenance)."""

    def _corpus(self) -> list[str]:
        return ["vendor GasCo amount 12345", "vendor ElectricCo amount 9000"]

    def test_modal_returns_provenance(self) -> None:
        expert = ModalSlmProvider().train(_domain(), self._corpus(), SlmSpec())
        assert expert.domain == "bill-extract"
        assert expert.artifact.startswith("modal://")
        assert expert.dataset_size == 2

    def test_runpod_returns_provenance(self) -> None:
        expert = RunpodSlmProvider(pod_id="pod-1").train(_domain(), self._corpus(), SlmSpec())
        assert expert.artifact.startswith("runpod://pod-1/")

    def test_replicate_returns_provenance(self) -> None:
        provider = ReplicateSlmProvider(model_owner="acme")
        expert = provider.train(_domain(), self._corpus(), SlmSpec())
        assert expert.artifact.startswith("replicate://acme/")

    def test_empty_corpus_fails_closed(self) -> None:
        with pytest.raises(SlmError, match="empty corpus"):
            ModalSlmProvider().train(_domain(), [], SlmSpec())