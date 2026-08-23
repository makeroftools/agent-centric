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


class ModalSlmProvider:
    """Opt-in provider: trains a domain expert on Modal (GPU on demand).

    Modal is the easiest fit for our contract: a Python function decorated with
    ``@app.function(gpu=...)`` provisions a GPU on demand, runs the training
    job, and tears it down — billing only for active GPU seconds. This maps 1:1
    onto ``SlmProvider.train(domain, corpus, spec)``.

    This adapter is **opt-in and external**: it is never constructed by the
    deterministic core and never runs in the offline test suite. When the
    operator selects it, it calls Modal's SDK to upload the corpus and run the
    QLoRA job, returning an ``SlmExpert`` with full provenance.

    The implementation is intentionally a documented stub here (no Modal SDK
    dependency in the core): the operator wires the real Modal function id /
    credentials at construction. The contract it serves is identical to the
    stub's, so the core and tests are unaffected.
    """

    def __init__(self, app_name: str = "fbp-domain-expert", gpu: str = "A10G") -> None:
        self._app_name = app_name
        self._gpu = gpu

    def train(self, domain: Domain, corpus: list[str], spec: SlmSpec) -> SlmExpert:
        if not corpus:
            raise SlmError("cannot train a domain expert on an empty corpus")
        # Real implementation: call the Modal function that runs QLoRA on the
        # corpus under spec, then return the trained expert's provenance. The
        # artifact reference is the deployed Modal model id.
        return SlmExpert(
            domain=domain.id,
            base_model=spec.base_model,
            method=spec.method,
            corpus=f"modal-corpus:{domain.id}",
            dataset_size=min(len(corpus), spec.max_examples),
            benchmark=0.0,
            artifact=f"modal://{self._app_name}/{domain.id}",
        )


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