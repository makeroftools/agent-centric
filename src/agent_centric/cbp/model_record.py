"""Model boundary — deterministic recording and confidence scoring for model
components (SPEC-0009 §4, SPEC-0002 §4).

SPEC-0009 requires that a ``model`` component's output is **recorded and
confidence-scored**, and SPEC-0002 requires that irreducible non-determinism is
**never silent** and that a model's output is **never relied upon directly**. This
module is the deterministic boundary that makes those rules operational and
auditable:

- :func:`score_model_confidence` is a **pure, deterministic** scorer; its output
  is a function of the recorded facts, never of a live model.
- :class:`ModelRecord` is an immutable, content-addressed record of one model
  call: the model identity, its provider kind, the prompt/output hashes, the
  deterministic confidence, and the reason for it. Its ``record_hash`` is a
  content address (Law 10: evidence is write-once and immutable).
- :class:`ModelRecordLog` is an **append-only, idempotent** log keyed by the
  record hash: recording the same call twice is a no-op, never a duplicate.

A model record never promotes a model's output to a verified success. The upward
verification spine is unchanged: the parent still re-verifies the value. The
confidence here is a scoped, evidence-backed statement about reproducibility, not
a claim that the output is true.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

MODEL_RECORD_SCHEMA = "cbp.model-record.v1"

# Provider kinds. A ``stub`` provider is deterministic by construction; any
# external provider is treated as non-deterministic unless proven otherwise, so
# the default posture is fail-closed.
PROVIDER_STUB = "stub"
PROVIDER_EXTERNAL = "external"
PROVIDER_KINDS = frozenset({PROVIDER_STUB, PROVIDER_EXTERNAL})

# Confidence is a scoped reproducibility score in [0, 1], capped strictly below
# 1.0: a model output is never conclusive on its own word.
MAX_MODEL_CONFIDENCE = 0.99

_HEX_DIGITS = frozenset("0123456789abcdef")


class ModelRecordError(ValueError):
    """A model record or its inputs is invalid (fail-closed)."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_content_hash(value: object) -> bool:
    """True if ``value`` is a 64-character lowercase hexadecimal digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in _HEX_DIGITS for ch in value)
    )


@dataclass(frozen=True)
class ModelConfidence:
    """A deterministic, scoped confidence for one model output.

    Attributes:
        score: 0.0 .. ``MAX_MODEL_CONFIDENCE``. Never 1.0: model output is never
            conclusive alone.
        reason: A short, deterministic explanation of the score.
    """

    score: float
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ModelRecordError(f"confidence must be numeric, got {self.score!r}")
        if not (0.0 <= float(self.score) <= MAX_MODEL_CONFIDENCE):
            raise ModelRecordError(
                f"confidence must be in [0.0, {MAX_MODEL_CONFIDENCE}], "
                f"got {self.score!r}"
            )
        if not self.reason:
            raise ModelRecordError("confidence reason must be non-empty")


def score_model_confidence(
    *, provider_kind: str, deterministic: bool, prompt: str, output: str
) -> ModelConfidence:
    """Score a model output deterministically (pure; never consults a model).

    The score is a scoped reproducibility statement. A deterministic provider's
    output reproduces byte-for-byte and scores high; an external provider's
    output is recorded and re-verified but must not be relied upon, so it scores
    lower. Empty input or output scores zero. The score is always strictly below
    1.0.

    Raises:
        ModelRecordError: If ``provider_kind`` is unknown.
    """
    if provider_kind not in PROVIDER_KINDS:
        raise ModelRecordError(f"unknown provider kind {provider_kind!r}")
    if not isinstance(prompt, str) or not prompt.strip():
        return ModelConfidence(0.0, "empty prompt; nothing to reproduce")
    if not isinstance(output, str) or not output.strip():
        return ModelConfidence(0.0, "empty output; nothing to reproduce")
    if not deterministic:
        return ModelConfidence(
            0.5,
            "external provider output; recorded and re-verified, not relied upon",
        )
    return ModelConfidence(
        0.9, "deterministic provider output; reproducible byte-for-byte"
    )


@dataclass(frozen=True)
class ModelRecord:
    """An immutable, content-addressed record of one model call."""

    model_id: str
    provider_kind: str
    deterministic: bool
    prompt_sha256: str
    output_sha256: str
    confidence: float
    confidence_reason: str
    schema: str = MODEL_RECORD_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MODEL_RECORD_SCHEMA:
            raise ModelRecordError(f"unsupported model record schema: {self.schema!r}")
        if not self.model_id:
            raise ModelRecordError("model_id must be non-empty")
        if self.provider_kind not in PROVIDER_KINDS:
            raise ModelRecordError(f"unknown provider kind {self.provider_kind!r}")
        if not isinstance(self.deterministic, bool):
            raise ModelRecordError("deterministic must be a bool")
        if not is_content_hash(self.prompt_sha256):
            raise ModelRecordError(
                "prompt_sha256 must be a 64-character lowercase hex digest"
            )
        if not is_content_hash(self.output_sha256):
            raise ModelRecordError(
                "output_sha256 must be a 64-character lowercase hex digest"
            )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise ModelRecordError(f"confidence must be numeric, got {self.confidence!r}")
        if not (0.0 <= float(self.confidence) <= MAX_MODEL_CONFIDENCE):
            raise ModelRecordError(
                f"confidence must be in [0.0, {MAX_MODEL_CONFIDENCE}]"
            )
        if not self.confidence_reason:
            raise ModelRecordError("confidence_reason must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "model_id": self.model_id,
            "provider_kind": self.provider_kind,
            "deterministic": self.deterministic,
            "prompt_sha256": self.prompt_sha256,
            "output_sha256": self.output_sha256,
            "confidence": float(self.confidence),
            "confidence_reason": self.confidence_reason,
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for the content address."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def record_hash(self) -> str:
        """The content address of this record (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        model_id: str,
        provider_kind: str,
        deterministic: bool,
        prompt: str,
        output: str,
    ) -> ModelRecord:
        """Build a record deterministically, scoring it at the boundary.

        Raises:
            ModelRecordError: If any field is invalid (fail-closed).
        """
        confidence = score_model_confidence(
            provider_kind=provider_kind,
            deterministic=deterministic,
            prompt=prompt,
            output=output,
        )
        return cls(
            model_id=model_id,
            provider_kind=provider_kind,
            deterministic=deterministic,
            prompt_sha256=_sha256_text(prompt),
            output_sha256=_sha256_text(output),
            confidence=confidence.score,
            confidence_reason=confidence.reason,
        )


class ModelRecordLog:
    """An append-only, idempotent log of model records (write-once, Law 10)."""

    def __init__(self) -> None:
        self._records: dict[str, ModelRecord] = {}

    def record(self, record: ModelRecord) -> bool:
        """Append ``record``; idempotent by content address.

        Returns:
            True when the record is new; False when an identical record (same
            ``record_hash``) was already present. An existing record is never
            mutated (write-once).
        """
        content_hash = record.record_hash()
        if content_hash in self._records:
            return False
        self._records[content_hash] = record
        return True

    def get(self, record_hash: str) -> ModelRecord | None:
        """Return the record at ``record_hash``, or None."""
        return self._records.get(record_hash)

    def entries(self) -> tuple[ModelRecord, ...]:
        """Every record, deterministically ordered by content address."""
        return tuple(self._records[h] for h in sorted(self._records))

    def to_list(self) -> list[dict[str, Any]]:
        """A deterministic, JSON-ready view of the log."""
        return [
            {**r.to_dict(), "record_hash": r.record_hash()} for r in self.entries()
        ]

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, record_hash: object) -> bool:
        return record_hash in self._records
