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
        def _fake(endpoint, headers, body):
            return '{"artifact": "expert-1"}'
        expert = ModalSlmProvider(http_client=_fake).train(
            _domain(), self._corpus(), SlmSpec()
        )
        assert expert.domain == "bill-extract"
        assert expert.artifact.startswith("modal://")
        assert expert.dataset_size == 2

    def test_modal_without_client_fails_closed(self) -> None:
        # A Modal provider built without an injected http_client must fail
        # closed on train (never accidentally reaches the network).
        with pytest.raises(SlmError, match="No HTTP transport"):
            ModalSlmProvider().train(_domain(), self._corpus(), SlmSpec())

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

class TestModalWorkableTransport:
    """The Modal adapter is genuinely workable with an injected HTTP client."""

    def _corpus(self):
        return ["vendor GasCo amount 12345", "vendor ElectricCo amount 9000"]

    def test_posts_corpus_and_parses_artifact(self) -> None:
        seen = {}

        def _fake(endpoint, headers, body):
            seen["endpoint"] = endpoint
            seen["headers"] = headers
            seen["body"] = body
            return '{"artifact": "modal-model-7b"}'

        expert = ModalSlmProvider(
            app_name="fbp-domain-expert", gpu="A10G", http_client=_fake
        ).train(_domain(), self._corpus(), SlmSpec())
        assert expert.artifact.endswith("modal-model-7b")
        assert seen["endpoint"] == "fbp-domain-expert"
        assert seen["headers"]["Content-Type"] == "application/json"
        import json as _json
        payload = _json.loads(seen["body"])
        assert payload["domain"] == "bill-extract"
        assert payload["gpu"] == "A10G"
        assert payload["base_model"] == "llama-3.2-3b"
        assert len(payload["corpus"]) == 2

    def test_plain_artifact_id_response(self) -> None:
        def _fake(endpoint, headers, body):
            return "modal-model-7b"
        expert = ModalSlmProvider(http_client=_fake).train(
            _domain(), self._corpus(), SlmSpec()
        )
        assert expert.artifact.endswith("modal-model-7b")

    def test_bad_response_fails_closed(self) -> None:
        def _fake(endpoint, headers, body):
            return "{not json"
        with pytest.raises(SlmError, match="unexpected Modal training response"):
            ModalSlmProvider(http_client=_fake).train(
                _domain(), self._corpus(), SlmSpec()
            )

    def test_client_failure_fails_closed(self) -> None:
        def _boom(endpoint, headers, body):
            raise RuntimeError("provider down")
        with pytest.raises(SlmError, match="Modal training request failed"):
            ModalSlmProvider(http_client=_boom).train(
                _domain(), self._corpus(), SlmSpec()
            )

    def test_build_modal_provider_factory(self) -> None:
        from agent_centric.fbp.providers import build_modal_provider
        p = build_modal_provider(http_client=lambda e, h, b: "model-1")
        assert isinstance(p, ModalSlmProvider)
        expert = p.train(_domain(), self._corpus(), SlmSpec())
        assert expert.artifact.endswith("model-1")

    def test_modal_returns_provenance(self) -> None:
        def _fake(endpoint, headers, body):
            return '{"artifact": "expert-1"}'
        expert = ModalSlmProvider(http_client=_fake).train(
            _domain(), self._corpus(), SlmSpec()
        )
        assert expert.domain == "bill-extract"
        assert expert.artifact.startswith("modal://")
        assert expert.dataset_size == 2

    def test_modal_without_client_fails_closed(self) -> None:
        # A Modal provider built without an injected http_client must fail
        # closed on train (never accidentally reaches the network).
        with pytest.raises(SlmError, match="No HTTP transport"):
            ModalSlmProvider().train(_domain(), self._corpus(), SlmSpec())