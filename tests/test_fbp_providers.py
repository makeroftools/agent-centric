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
    build_credential_client,
    build_modal_from_env,
    credentials_from_env,
    get_provider,
    provider_names,
    redact_secrets,
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

class TestCredentialWiring:
    """The opt-in, workable credential wiring for a real training provider."""

    def _corpus(self) -> list[str]:
        return ["vendor GasCo amount 12345", "vendor ElectricCo amount 9000"]

    def _creds(self, **over: str) -> dict:
        base = {
            "TRAIN_ENDPOINT": "https://training.example.test/run",
            "TRAIN_TOKEN": "sk-train-123456",
        }
        base.update(over)
        return base

    def test_credentials_from_env_reads_endpoint_and_token(self) -> None:
        creds = credentials_from_env(self._creds())
        assert creds.configured is True
        assert creds.endpoint == "https://training.example.test/run"
        assert creds.token == "sk-train-123456"
        assert creds.auth_header_value == "Bearer sk-train-123456"

    def test_credentials_subset_fails_closed(self) -> None:
        assert credentials_from_env({"TRAIN_TOKEN": "sk-x"}).configured is False
        assert credentials_from_env({"TRAIN_ENDPOINT": "https://e/run"}).configured is False

    def test_credentials_from_env_empty_fails_closed(self) -> None:
        assert credentials_from_env({}).configured is False

    def test_custom_prefix(self) -> None:
        creds = credentials_from_env(
            {"MODAL_ENDPOINT": "https://m/run", "MODAL_TOKEN": "t"}, prefix="MODAL"
        )
        assert creds.configured is True

    def test_credentials_to_dict_redacts_token(self) -> None:
        d = credentials_from_env(self._creds()).to_dict()
        assert d["token"] == "[REDACTED]"
        assert "sk-train-123456" not in str(d)

    def test_build_credential_client_requires_configured(self) -> None:
        with pytest.raises(SlmError, match="not configured"):
            build_credential_client(credentials_from_env({}))

    def test_build_credential_client_posts_with_auth(self) -> None:
        import agent_centric.fbp.providers as _p

        seen = {}
        original = _p.stdlib_http_client

        def _fake(endpoint: str, headers: dict[str, str], body: str, **kwargs: object) -> str:
            seen["endpoint"] = endpoint
            seen["headers"] = headers
            seen["body"] = body
            return '{"artifact": "expert-9"}'

        _p.stdlib_http_client = _fake
        try:
            client = build_credential_client(credentials_from_env(self._creds()))
            text = client("ignored", {"Content-Type": "application/json"}, "{}")
        finally:
            _p.stdlib_http_client = original
        assert seen["endpoint"] == "https://training.example.test/run"
        assert seen["headers"]["Authorization"] == "Bearer sk-train-123456"
        assert text == '{"artifact": "expert-9"}'

    def test_build_modal_from_env_without_creds_is_safe(self) -> None:
        p = build_modal_from_env({})
        assert isinstance(p, ModalSlmProvider)
        with pytest.raises(SlmError, match="No HTTP transport"):
            p.train(_domain(), self._corpus(), SlmSpec())

    def test_build_modal_from_env_with_creds_sends_auth_header(self) -> None:
        p = build_modal_from_env(self._creds())
        assert p._credentials is not None and p._credentials.configured is True
        seen = {}

        def _fake(endpoint, headers, body):
            seen["headers"] = headers
            return '{"artifact": "m"}'

        p._http_client = _fake
        expert = p.train(_domain(), self._corpus(), SlmSpec())
        assert expert.artifact.endswith("m")
        assert seen["headers"]["Authorization"] == "Bearer sk-train-123456"

    def test_redact_secrets(self) -> None:
        assert "sk-sec" not in redact_secrets("error with sk-sec value", ("sk-sec",))
