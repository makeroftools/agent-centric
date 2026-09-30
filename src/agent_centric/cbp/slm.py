"""Domain-expert SLM provider contract (the ``learned`` tier of Network of Experts).

This is the opt-in, external seam that turns a domain corpus into a trained
per-domain expert — the "learned" tier that ``select_expert`` recommends when a
deterministic method cannot cover the residue. It is deliberately **not** built
into the deterministic core: training (DAPT / SFT / DPO) is an external,
fail-closed provider. The core *selects* and *verifies*; the provider *trains*.

The 2026 engineering reality (see ``docs/domain_slm.md``): with QLoRA a 3B-7B
open base is fine-tuned on a single consumer GPU, on hundreds to tens of
thousands of curated examples, in hours to days. The pipeline is corpus
construction -> domain-adaptive pretraining -> SFT -> alignment -> evaluation ->
quantized deployment. This module defines the contract that pipeline serves.

Key rules (deterministic, fail-closed):

- ``SlmProvider`` is a protocol: ``train(corpus, spec) -> SlmExpert``. A provider
  may be a stub (offline, deterministic) or a real external trainer.
- ``SlmExpert`` carries full **provenance**: base model, corpus id, method
  (DAPT/SFT/DPO), dataset size, benchmark score, and an artifact reference. This
  is what lands in the Artifact Vault.
- ``build_domain_expert`` is the entry point: given a domain, a corpus, and a
  provider, it produces a verified expert (or fails closed). It never bypasses
  the domain's verifier.
- The module is pure and offline by default; a real provider is opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .experts import Domain


class SlmError(ValueError):
    """An SLM-provider input violated its contract (fail-closed)."""


@dataclass(frozen=True)
class SlmSpec:
    """The training recipe for a domain-expert SLM.

    Attributes:
        base_model: The open base model id (e.g. ``llama-3.2-3b``).
        method: The adaptation method (``dapt`` / ``sft`` / ``dpo`` / ``qlora``).
        max_examples: An upper bound on training examples (deterministic cap).
        quantize: Whether to quantize for deployment (4-bit / 8-bit).
    """

    base_model: str = "llama-3.2-3b"
    method: str = "qlora"
    max_examples: int = 10_000
    quantize: bool = True


@dataclass(frozen=True)
class SlmExpert:
    """A trained domain-expert SLM, with full provenance.

    Attributes:
        domain: The domain id this expert serves.
        base_model: The base model it was adapted from.
        method: The adaptation method used.
        corpus: The corpus id / description it was trained on.
        dataset_size: The number of training examples.
        benchmark_score: A measured domain-benchmark score (0..1), if any.
        artifact: A reference to the deployed artifact (e.g. a model id / path).
    """

    domain: str
    base_model: str
    method: str
    corpus: str
    dataset_size: int
    benchmark: float | None = None
    artifact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "base_model": self.base_model,
            "method": self.method,
            "corpus": self.corpus,
            "dataset_size": self.dataset_size,
            "benchmark": self.benchmark,
            "artifact": self.artifact,
        }


class SlmProvider(Protocol):
    """The external training seam: corpus in, trained expert out.

    A provider is opt-in and fail-closed. A stub provider (the default) is
    offline and deterministic; a real provider calls an external training
    service. ``train`` raises ``SlmError`` on any failure.
    """

    def train(self, domain: Domain, corpus: list[str], spec: SlmSpec) -> SlmExpert:
        """Train a domain expert from ``corpus`` under ``spec``."""
        ...


class StubSlmProvider:
    """An offline, deterministic SLM provider (for tests / no-external-deps).

    It does not train a real model; it returns a deterministic ``SlmExpert``
    carrying the corpus size and a fixed benchmark, so the contract is exercised
    without any external dependency. This is the default and the CI-safe path.
    """

    def train(self, domain: Domain, corpus: list[str], spec: SlmSpec) -> SlmExpert:
        if not corpus:
            raise SlmError("cannot train a domain expert on an empty corpus")
        size = min(len(corpus), spec.max_examples)
        return SlmExpert(
            domain=domain.id,
            base_model=spec.base_model,
            method=spec.method,
            corpus=f"stub-corpus:{domain.id}",
            dataset_size=size,
            benchmark=0.0,
            artifact=f"stub://{domain.id}/{spec.base_model}",
        )


def build_domain_expert(
    domain: Domain,
    corpus: list[str],
    provider: SlmProvider | None = None,
    spec: SlmSpec | None = None,
) -> SlmExpert:
    """Build a domain-expert SLM from ``corpus`` via ``provider`` (fail-closed).

    This is the entry point of the ``learned`` tier. It is deterministic and
    fail-closed: an empty corpus, an unverifiable domain, or a provider failure
    raises ``SlmError``. The returned expert carries provenance for the Artifact
    Vault. A real provider is opt-in; the default stub is offline and CI-safe.
    """
    if not domain.achievable:
        raise SlmError(
            f"domain {domain.id!r} has no verifiable output; cannot train an expert"
        )
    if not corpus:
        raise SlmError(f"cannot build a domain expert for {domain.id!r}: empty corpus")
    provider = provider or StubSlmProvider()
    spec = spec or SlmSpec()
    try:
        return provider.train(domain, corpus, spec)
    except SlmError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        raise SlmError(f"domain-expert training failed: {exc}") from exc