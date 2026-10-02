"""Review contract (versioned) — ``review.v1`` (SPEC-0002 §4, §6).

An outcome that cannot be made deterministic must never be silent. SPEC-0002
requires every such outcome to be **traceable**, **confidence-scored**, and
enqueued to a **persistent, append-only Review queue** (``review.v1``). This
module freezes the pure, content-hashable part of that contract; the persistent
queue itself lives in :mod:`agent_centric.cbp.review`.

- :class:`ReviewItem` is the immutable, content-addressed record of one outcome
  that needs review: its correlation id, domain, deterministic confidence, and
  the provenance chain that lets a reviewer reconstruct what happened.
- :class:`ReviewResolution` is a *distinct, immutable* record that resolves an
  item. A resolution is **appended**, never written in place, so the queue is
  strictly write-once (PRINCIPLES Law 10): an item's status is **derived** from
  the presence of a resolution that references its content address.
- :func:`needs_review` is the pure, deterministic predicate that decides whether
  an outcome must enter the queue. It is fail-closed: an unverified outcome (or
  one whose confidence cannot be trusted) always needs review.

Contracts are additive-only; this file performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

REVIEW_SCHEMA = "review.v1"

# An outcome at or above this confidence — and verified — bypasses review. The
# default is deliberately the maximum: only a fully certain, verified outcome
# skips the queue, so anything less is fail-closed into review.
DEFAULT_REVIEW_THRESHOLD = 1.0

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReviewVersion:
    """The supported review contract version."""

    V1 = REVIEW_SCHEMA

    __slots__ = ()


class ReviewStatus:
    """The derived status of a review item."""

    PENDING = "pending"
    RESOLVED = "resolved"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.PENDING, cls.RESOLVED)


def is_content_hash(value: object) -> bool:
    """True if ``value`` is a 64-character lowercase hexadecimal digest."""
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ReviewItem:
    """An immutable, content-addressed outcome that needs review."""

    correlation_id: str
    domain: str
    confidence: float
    provenance: tuple[str, ...] = ()
    schema: str = REVIEW_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != REVIEW_SCHEMA:
            raise ValueError(f"Unsupported review schema: {self.schema!r}")
        if not self.correlation_id:
            raise ValueError("ReviewItem correlation_id must be non-empty.")
        if not self.domain:
            raise ValueError("ReviewItem domain must be non-empty.")
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise ValueError(
                f"ReviewItem confidence must be numeric: {self.confidence!r}"
            )
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(
                f"ReviewItem confidence must be in [0.0, 1.0]: {self.confidence!r}"
            )
        for entry in self.provenance:
            if not isinstance(entry, str) or not entry:
                raise ValueError(
                    "ReviewItem provenance entries must be non-empty strings."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "correlation_id": self.correlation_id,
            "domain": self.domain,
            "confidence": float(self.confidence),
            "provenance": list(self.provenance),
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for the content address."""
        return _canonical_bytes(self.to_dict())

    def item_hash(self) -> str:
        """The content address of this item (sha256, hex) — write-once evidence."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewItem:
        try:
            correlation_id = str(data["correlation_id"])
            domain = str(data["domain"])
            confidence = data["confidence"]
        except KeyError as exc:
            raise ValueError(f"ReviewItem missing field: {exc!r}") from exc
        provenance_raw = data.get("provenance", [])
        if not isinstance(provenance_raw, list):
            raise ValueError("ReviewItem provenance must be a list.")
        return cls(
            correlation_id=correlation_id,
            domain=domain,
            confidence=confidence,
            provenance=tuple(str(entry) for entry in provenance_raw),
            schema=str(data.get("schema", REVIEW_SCHEMA)),
        )


@dataclass(frozen=True)
class ReviewResolution:
    """An immutable record that resolves a :class:`ReviewItem` (append-only)."""

    item_hash: str
    resolution: str
    resolver: str = ""
    schema: str = REVIEW_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != REVIEW_SCHEMA:
            raise ValueError(f"Unsupported review schema: {self.schema!r}")
        if not is_content_hash(self.item_hash):
            raise ValueError("ReviewResolution item_hash must be a 64-hex sha256.")
        if not self.resolution:
            raise ValueError("ReviewResolution resolution must be non-empty.")
        if not isinstance(self.resolver, str):
            raise ValueError("ReviewResolution resolver must be a string.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "item_hash": self.item_hash,
            "resolution": self.resolution,
            "resolver": self.resolver,
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for the content address."""
        return _canonical_bytes(self.to_dict())

    def resolution_hash(self) -> str:
        """The content address of this resolution (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewResolution:
        try:
            item_hash = str(data["item_hash"])
            resolution = str(data["resolution"])
        except KeyError as exc:
            raise ValueError(f"ReviewResolution missing field: {exc!r}") from exc
        return cls(
            item_hash=item_hash,
            resolution=resolution,
            resolver=str(data.get("resolver", "")),
            schema=str(data.get("schema", REVIEW_SCHEMA)),
        )


def needs_review(
    *,
    verified: bool,
    confidence: float | None = None,
    threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> bool:
    """Decide, deterministically, whether an outcome must enter the Review queue.

    Fail-closed: an **unverified** outcome always needs review, and a
    ``confidence`` that is present but malformed (non-numeric or out of range)
    also needs review rather than being trusted. A **verified** outcome with no
    confidence is a deterministic result and does not need review; a verified
    outcome with a confidence below ``threshold`` does.

    Raises:
        ValueError: If ``threshold`` is not a number in [0.0, 1.0].
    """
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError(f"review threshold must be numeric: {threshold!r}")
    if not (0.0 <= float(threshold) <= 1.0):
        raise ValueError(f"review threshold must be in [0.0, 1.0]: {threshold!r}")
    if not verified:
        return True
    if confidence is None:
        return False
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return True
    if not (0.0 <= float(confidence) <= 1.0):
        return True
    return float(confidence) < float(threshold)
