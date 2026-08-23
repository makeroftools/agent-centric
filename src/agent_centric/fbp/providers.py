"""External training-provider adapters (the learned tier, opt-in).

This is the **execution** side of the learned tier: a real provider that turns a
domain corpus into a trained expert on external hardware. It is deliberately
**not** built into the deterministic core — training is external and opt-in. The
core *plans* (``training_plan.py``) and *verifies*; the provider *executes*.

The provider registry lets an operator choose which cloud to use at runtime
(``get_provider("modal")`` / ``"runpod"`` / ``"replicate"``), defaulting to the
offline, deterministic stub. A real provider is opt-in: it is only constructed
when the operator explicitly selects it, and it never runs in the test suite.

Each provider implements the ``SlmProvider`` protocol from ``fbp/slm.py``:
``train(domain, corpus, spec) -> SlmExpert``. The returned ``SlmExpert`` carries
full provenance (base model, method, corpus, dataset size, benchmark, artifact)
that lands in the Artifact Vault.

Only **Modal** is implemented as a real adapter today; Runpod and Replicate are
registered as documented, opt-in stubs so the operator can add them later
without touching the core.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from .experts import Domain
from .slm import SlmError, SlmExpert, SlmProvider, SlmSpec, StubSlmProvider


class ProviderRegistry:
    """A registry of named training providers (operator-selectable).

    The registry is a passive catalog: it maps a provider name to a factory.
    It never provisions anything itself. The default is the offline stub; a real
    provider is constructed only when the operator explicitly selects it.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Any] = {
            "stub": lambda: StubSlmProvider(),
            "modal": lambda: ModalSlmProvider(),
            "runpod": lambda: RunpodSlmProvider(),
            "replicate": lambda: ReplicateSlmProvider(),
        }

    def names(self) -> tuple[str, ...]:
        """The provider names an operator may choose from (deterministic)."""
        return tuple(sorted(self._factories))

    def get(self, name: str) -> SlmProvider:
        """Return the provider for ``name`` (fail-closed on an unknown name).

        Raises:
            SlmError: If ``name`` is not a registered provider.
        """
        factory = self._factories.get(name)
        if factory is None:
            raise SlmError(
                f"unknown training provider {name!r}; "
                f"expected one of {', '.join(self.names())}"
            )
        return cast(SlmProvider, factory())


# A module-level default registry (the operator's choice point).
_DEFAULT_REGISTRY = ProviderRegistry()


def provider_names() -> tuple[str, ...]:
    """The provider names an operator may select from."""
    return _DEFAULT_REGISTRY.names()


def get_provider(name: str) -> SlmProvider:
    """Return the named training provider (default registry).

    Raises:
        SlmError: If ``name`` is not a registered provider.
    """
    return _DEFAULT_REGISTRY.get(name)


# A real HTTP transport for a training endpoint: endpoint, headers, JSON body
# -> response text. It may raise on HTTP/API/transport errors; the provider maps
# those to an explicit SlmError. Never invoked by the offline suite (fakes are
# used instead).
TrainingHttpClient = Callable[[str, dict[str, str], str], str]


def _no_training_http_client(
    endpoint: str, headers: dict[str, str], body: str
) -> str:
    """Default transport: never reaches the network.

    A real Modal provider built without an injected ``http_client`` fails closed
    on call rather than attempting a network request, so the harness can never
    make an accidental external call.
    """
    raise SlmError(
        "No HTTP transport configured for the Modal training provider. "
        "Inject an explicit http_client to enable real calls."
    )


# ---------------------------------------------------------------------------
# Real transport + credential wiring (the workable, opt-in path)
# ---------------------------------------------------------------------------

def redact_secrets(text: str, secrets: tuple[str, ...]) -> str:
    """Replace concrete secret values in ``text`` with ``[REDACTED]``.

    Mirrors the real-model-provider redaction so credentials (API keys, token
    headers) can never leak into raised error messages from the real training
    providers.
    """
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return out


def stdlib_http_client(
    endpoint: str,
    headers: dict[str, str],
    body: str,
    *,
    timeout: float = 60.0,
    secrets: tuple[str, ...] = (),
) -> str:
    """A real, dependency-free HTTP(JSON) POST transport (opt-in).

    This is the actual network path a workable Modal/Runpod/Replicate adapter
    uses when the operator injects it. It POSTs ``body`` to ``endpoint`` with
    ``headers``, returning the response text. HTTP/URL/transport errors are
    surfaced with secrets redacted so credentials never leak.

    It is *never* invoked by the offline suite — the tests inject fakes instead.
    """
    req = urllib.request.Request(
        endpoint,
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            text: str = resp.read().decode("utf-8", errors="replace")
            return text.strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise SlmError(
            f"training HTTP {exc.code}: {redact_secrets(str(exc), secrets)}"
            + (f" body={redact_secrets(detail, secrets)}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise SlmError(
            f"training request failed: {redact_secrets(str(exc), secrets)}"
        ) from exc


@dataclass(frozen=True)
class TrainingCredentials:
    """The operator-provided credentials for a real training provider.

    Attributes:
        endpoint: The training API endpoint (e.g. a Modal webhook or a
            provider REST URL). Must be a valid ``http(s)://`` URL.
        token: The API token / auth value (a Bearer token or API key).
        auth_scheme: ``Bearer`` (default) or ``Token``/other prefix.
        app_name: A free-form label carried in the artifact provenance.
    """

    endpoint: str
    token: str = ""
    auth_scheme: str = "Bearer"
    app_name: str = "fbp-domain-expert"

    @property
    def configured(self) -> bool:
        """Whether this is a usable, complete credential set."""
        on_http = self.endpoint.startswith("http://") or self.endpoint.startswith(
            "https://"
        )
        return on_http and bool(self.token)

    @property
    def auth_header_value(self) -> str:
        return f"{self.auth_scheme} {self.token}" if self.token else ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe readout (secret values redacted)."""
        return {
            "endpoint": self.endpoint,
            "auth_scheme": self.auth_scheme,
            "configured": self.configured,
            "token": "[REDACTED]" if self.token else "",
            "app_name": self.app_name,
        }


def credentials_from_env(
    env: dict[str, str] | None = None,
    *,
    prefix: str = "TRAIN",
) -> TrainingCredentials:
    """Read training credentials from the environment (opt-in, deterministic).

    Looks up ``{prefix}_ENDPOINT`` and ``{prefix}_TOKEN`` (default ``TRAIN_*``) and
    returns a ``TrainingCredentials``. A missing/disabled set is signalled by
    ``configured=False`` (fail-closed) — never a partial half-configured
    credential that could silently hit the wrong endpoint.
    """
    src = env if env is not None else dict(os.environ)
    endpoint = (src.get(f"{prefix}_ENDPOINT") or "").strip()
    token = (src.get(f"{prefix}_TOKEN") or "").strip()
    scheme = (src.get(f"{prefix}_AUTH_SCHEME") or "Bearer").strip()
    app_name = (src.get(f"{prefix}_APP") or "fbp-domain-expert").strip()
    return TrainingCredentials(
        endpoint=endpoint, token=token, auth_scheme=scheme, app_name=app_name
    )


def build_credential_client(
    credentials: TrainingCredentials,
    *,
    timeout: float = 60.0,
) -> TrainingHttpClient:
    """Build a workable real HTTP client bound to ``credentials``.

    The returned client (``endpoint, headers, body -> response text``) posts to
    the credential's endpoint with an ``Authorization`` header. Secrets are
    captured for redaction in any raised error. This is the opt-in network path;
    it is never invoked by the offline suite.
    """
    if not credentials.configured:
        raise SlmError(
            "training credentials are not configured (missing endpoint/token); "
            "train cannot reach a real provider"
        )
    secrets = (credentials.token,) if credentials.token else ()

    def client(endpoint: str, headers: dict[str, str], body: str) -> str:
        effective = {"Authorization": credentials.auth_header_value}
        effective.update(headers)
        return stdlib_http_client(
            credentials.endpoint,
            effective,
            body,
            timeout=timeout,
            secrets=secrets,
        )

    return client


def build_modal_from_env(
    env: dict[str, str] | None = None,
    *,
    gpu: str = "A10G",
) -> ModalSlmProvider:
    """Build a workable Modal provider from the environment (opt-in).

    Reads ``TRAIN_*`` credentials and constructs a ``ModalSlmProvider`` that is
    genuinely network-capable when ``TRAIN_ENDPOINT`` + ``TRAIN_TOKEN`` are set;
    without them it fails closed on ``train`` (never accidentally reaches the
    network). This is the operator-facing wiring the handoff asked for.
    """
    credentials = credentials_from_env(env, prefix="TRAIN")
    return ModalSlmProvider(
        app_name=credentials.app_name, gpu=gpu, credentials=credentials
    )


class ModalSlmProvider:
    """Opt-in provider: trains a domain expert on Modal (GPU on demand).

    Modal is the easiest fit for our contract: a Python function decorated with
    ``@app.function(gpu=...)`` provisions a GPU on demand, runs the training
    job, and tears it down — billing only for active GPU seconds. This maps 1:1
    onto ``SlmProvider.train(domain, corpus, spec)``.

    This adapter is **opt-in and external**: it is never constructed by the
    deterministic core and never runs in the offline test suite. When the
    operator selects it, it POSTs the corpus + recipe to a Modal-style training
    endpoint (via an injectable ``http_client``) and returns an ``SlmExpert``
    with full provenance.

    The transport is injectable so the adapter is **workable** (a real HTTP
    client can be supplied) while the core and offline tests stay deterministic:
    without an injected client, ``train`` fails closed rather than reaching the
    network.
    """

    def __init__(
        self,
        app_name: str = "fbp-domain-expert",
        gpu: str = "A10G",
        http_client: TrainingHttpClient | None = None,
        credentials: TrainingCredentials | None = None,
    ) -> None:
        self._app_name = app_name
        self._gpu = gpu
        self._credentials = credentials
        self._secrets = (credentials.token,) if credentials is not None else ()
        if http_client is not None:
            self._http_client = http_client
        elif credentials is not None and credentials.configured:
            # A workable, credential-backed transport: POST with the bearer
            # header to the real endpoint. Opt-in — never reached offline.
            self._http_client = build_credential_client(credentials)
        else:
            self._http_client = _no_training_http_client

    def train(self, domain: Domain, corpus: list[str], spec: SlmSpec) -> SlmExpert:
        if not corpus:
            raise SlmError("cannot train a domain expert on an empty corpus")
        size = min(len(corpus), spec.max_examples)
        body = json.dumps(
            {
                "domain": domain.id,
                "base_model": spec.base_model,
                "method": spec.method,
                "max_examples": size,
                "quantize": spec.quantize,
                "gpu": self._gpu,
                "corpus": corpus[:size],
            }
        )
        headers = {"Content-Type": "application/json"}
        if self._credentials is not None and self._credentials.configured:
            headers["Authorization"] = self._credentials.auth_header_value
        try:
            text = self._http_client(self._app_name, headers, body)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            raise SlmError(
                f"Modal training request failed: {redact_secrets(str(exc), self._secrets)}"
            ) from exc
        # The endpoint returns a JSON object with the trained expert's
        # provenance (or a plain artifact id). Parse fail-closed.
        artifact = text.strip()
        if artifact.startswith("{"):
            try:
                data = json.loads(artifact)
            except ValueError as exc:
                raise SlmError(
                    f"unexpected Modal training response: {artifact[:200]}"
                ) from exc
            if isinstance(data, dict) and isinstance(data.get("artifact"), str):
                artifact = data["artifact"]
        return SlmExpert(
            domain=domain.id,
            base_model=spec.base_model,
            method=spec.method,
            corpus=f"modal-corpus:{domain.id}",
            dataset_size=size,
            benchmark=0.0,
            artifact=f"modal://{self._app_name}/{domain.id}:{artifact}",
        )


def build_modal_provider(
    *,
    app_name: str = "fbp-domain-expert",
    gpu: str = "A10G",
    http_client: TrainingHttpClient | None = None,
) -> ModalSlmProvider:
    """Build a Modal training provider (opt-in, fail-closed).

    The ``http_client`` is the real transport; if omitted the resulting provider
    fails closed on ``train`` (it never accidentally reaches the network). This
    mirrors ``build_real_model_provider``: the adapter is workable with an
    injected client and safe without one.
    """
    return ModalSlmProvider(app_name=app_name, gpu=gpu, http_client=http_client)


class RunpodSlmProvider:
    """Opt-in provider: **Runpod** (persistent GPU pods).

    Runpod gives the operator explicit control over persistent GPU pods (start /
    stop / scale). It is a documented, opt-in stub today; the operator wires the
    real pod id / API at construction. The contract is identical to the stub's.
    """

    def __init__(self, pod_id: str = "") -> None:
        self._pod_id = pod_id

    def train(self, domain: Domain, corpus: list[str], spec: SlmSpec) -> SlmExpert:
        if not corpus:
            raise SlmError("cannot train a domain expert on an empty corpus")
        return SlmExpert(
            domain=domain.id,
            base_model=spec.base_model,
            method=spec.method,
            corpus=f"runpod-corpus:{domain.id}",
            dataset_size=min(len(corpus), spec.max_examples),
            benchmark=0.0,
            artifact=f"runpod://{self._pod_id or 'pod'}/{domain.id}",
        )


class ReplicateSlmProvider:
    """Opt-in provider: **Replicate** (managed training + inference).

    Replicate is inference-first but exposes a managed fine-tuning surface. It
    is a documented, opt-in stub for the operator to wire later. The contract is
    identical to the stub's.
    """

    def __init__(self, model_owner: str = "") -> None:
        self._model_owner = model_owner

    def train(self, domain: Domain, corpus: list[str], spec: SlmSpec) -> SlmExpert:
        if not corpus:
            raise SlmError("cannot train a domain expert on an empty corpus")
        return SlmExpert(
            domain=domain.id,
            base_model=spec.base_model,
            method=spec.method,
            corpus=f"replicate-corpus:{domain.id}",
            dataset_size=min(len(corpus), spec.max_examples),
            benchmark=0.0,
            artifact=f"replicate://{self._model_owner or 'owner'}/{domain.id}",
        )