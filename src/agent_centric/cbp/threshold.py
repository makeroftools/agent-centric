"""Threshold signing — k-of-n detached signatures over a release (SPEC-0007 §6).

The root of trust is an operator-held signing key (SPEC-0007 §6). Rotation with
overlapping trust windows is delivered by :mod:`agent_centric.cbp.trust`; this
module adds the **optional second signer** the spec names: a release may require
**k distinct trusted keys of an eligible set of n** before its content address is
accepted. No single compromised key can then push a bad release.

Design (deterministic, fail-closed, additive):

- :class:`ThresholdPolicy` is a pure, content-addressable ``(threshold, key_ids)``
  declaration; it performs no I/O.
- :class:`ThresholdSignature` is a **canonical**, content-addressed bundle of
  detached signatures (schema ``threshold.sig/v1``), sorted by ``key_id`` and
  base64-encoded so arbitrary binary signatures round-trip. A bundle may not carry
  a key twice.
- :class:`ThresholdVerifier` implements the existing
  :class:`~agent_centric.cbp.signing.SignatureVerifier` protocol, so it drops into
  the resolver and ``boot_from_lock`` **without changing any caller**: only the
  signature bytes differ. It refuses a malformed/non-canonical bundle, an
  ineligible or unknown key, any part that fails to verify, and — finally — a
  bundle below the threshold. A bundle holding any invalid part is refused whole.
- :func:`sign_threshold` composes independent :class:`~agent_centric.cbp.signing_service.Signer`
  instances; no new cryptography is introduced (the same system-tool signers are
  reused).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .signing import SignatureError, SignatureVerifier
from .signing_service import Signer
from .trust import TrustStore

#: The schema of a canonical threshold-signature bundle.
THRESHOLD_SCHEMA = "threshold.sig/v1"


class ThresholdError(SignatureError):
    """A threshold policy or signature bundle violated an invariant (fail-closed).

    Subclasses :class:`~agent_centric.cbp.signing.SignatureError` so a
    :class:`ThresholdVerifier` drops into the existing verification paths
    (resolver, ``boot_from_lock``) unchanged and still refuses fail-closed.
    """


def _canonical_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ThresholdPolicy:
    """A pure ``k-of-n`` signature policy: ``threshold`` of ``key_ids`` must sign.

    The eligible key set is canonicalized (name-sorted, unique). The policy is
    content-addressed by :meth:`policy_hash`, so it can be pinned and recorded.
    """

    threshold: int
    key_ids: tuple[str, ...]
    schema: str = THRESHOLD_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != THRESHOLD_SCHEMA:
            raise ThresholdError(f"Unsupported threshold schema: {self.schema!r}")
        ordered = tuple(sorted(self.key_ids))
        if not ordered:
            raise ThresholdError("a threshold policy needs at least one key")
        if any(not key_id for key_id in ordered):
            raise ThresholdError("threshold key ids must be non-empty")
        if len(set(ordered)) != len(ordered):
            raise ThresholdError("threshold key ids must be unique")
        if isinstance(self.threshold, bool) or not isinstance(self.threshold, int):
            raise ThresholdError("threshold must be an integer")
        if self.threshold < 1:
            raise ThresholdError("threshold must be at least 1")
        if self.threshold > len(ordered):
            raise ThresholdError(
                f"threshold {self.threshold} exceeds the {len(ordered)} eligible keys"
            )
        object.__setattr__(self, "key_ids", ordered)

    def accepts(self, verified_key_ids: Iterable[str]) -> bool:
        """True iff ``verified_key_ids`` contains at least ``threshold`` eligible keys."""
        eligible = set(self.key_ids)
        return len({key for key in verified_key_ids if key in eligible}) >= self.threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "threshold": self.threshold,
            "key_ids": list(self.key_ids),
        }

    def policy_hash(self) -> str:
        """The content address of this policy (sha256, hex)."""
        return hashlib.sha256(_canonical_bytes(self.to_dict())).hexdigest()

    @classmethod
    def from_dict(cls, data: Any) -> ThresholdPolicy:
        if not isinstance(data, dict):
            raise ThresholdError("a threshold policy must be an object")
        raw_keys = data.get("key_ids", [])
        if not isinstance(raw_keys, list):
            raise ThresholdError("threshold key_ids must be a list")
        return cls(
            threshold=int(data["threshold"]),
            key_ids=tuple(str(key) for key in raw_keys),
            schema=str(data.get("schema", THRESHOLD_SCHEMA)),
        )


@dataclass(frozen=True)
class SignaturePart:
    """One detached signature in a bundle, keyed by the signing key's id."""

    key_id: str
    signature: bytes

    def __post_init__(self) -> None:
        if not self.key_id:
            raise ThresholdError("signature part key_id must be non-empty")
        if not self.signature:
            raise ThresholdError("signature part signature must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {
            "key_id": self.key_id,
            "signature": base64.b64encode(self.signature).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, data: Any) -> SignaturePart:
        if not isinstance(data, dict):
            raise ThresholdError("a signature part must be an object")
        raw = data.get("signature", "")
        if not isinstance(raw, str):
            raise ThresholdError("signature part signature must be a string")
        try:
            signature = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ThresholdError(f"signature part is not valid base64: {exc}") from exc
        return cls(key_id=str(data["key_id"]), signature=signature)


@dataclass(frozen=True)
class ThresholdSignature:
    """A canonical, content-addressed bundle of detached signatures.

    Parts are sorted by ``key_id`` and may not repeat a key id, so the encoding is
    byte-deterministic: the same signed parts always hash identically.
    """

    parts: tuple[SignaturePart, ...]
    schema: str = THRESHOLD_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != THRESHOLD_SCHEMA:
            raise ThresholdError(f"Unsupported threshold schema: {self.schema!r}")
        ordered = tuple(sorted(self.parts, key=lambda part: part.key_id))
        if not ordered:
            raise ThresholdError("a threshold signature needs at least one part")
        if len({part.key_id for part in ordered}) != len(ordered):
            raise ThresholdError("threshold signature parts must have unique key ids")
        object.__setattr__(self, "parts", ordered)

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(part.key_id for part in self.parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "signatures": [part.to_dict() for part in self.parts],
        }

    def to_bytes(self) -> bytes:
        """Canonical, deterministic serialization (the signature blob)."""
        return _canonical_bytes(self.to_dict())

    def to_text(self) -> str:
        """The signature as text, suitable for a ``lock_signature`` field."""
        return self.to_bytes().decode("utf-8")

    def fingerprint(self) -> str:
        """The content address of this bundle (sha256, hex)."""
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, bytes]]) -> ThresholdSignature:
        """Build a bundle from ``(key_id, signature_bytes)`` pairs (unique keys)."""
        return cls(parts=tuple(SignaturePart(key_id=key, signature=sig) for key, sig in pairs))

    @classmethod
    def from_binary(cls, data: bytes) -> ThresholdSignature:
        """Parse a canonical bundle blob (fail-closed on any deviation)."""
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ThresholdError(f"threshold signature is not JSON: {exc}") from exc
        if _canonical_bytes(parsed) != data:
            raise ThresholdError("threshold signature is not canonical JSON")
        if not isinstance(parsed, dict):
            raise ThresholdError("threshold signature must be an object")
        raw_parts = parsed.get("signatures", [])
        if not isinstance(raw_parts, list):
            raise ThresholdError("threshold signature 'signatures' must be a list")
        return cls(
            parts=tuple(SignaturePart.from_dict(part) for part in raw_parts),
            schema=str(parsed.get("schema", THRESHOLD_SCHEMA)),
        )


class ThresholdVerifier:
    """A :class:`SignatureVerifier` requiring ``k`` of ``n`` eligible signatures.

    Fail-closed: a malformed/non-canonical bundle, an ineligible or unknown key, a
    part that does not verify, or fewer than ``threshold`` valid parts all refuse
    with :class:`ThresholdError`. A bundle holding any invalid part is refused
    whole, even if the threshold could otherwise be met.
    """

    def __init__(
        self, policy: ThresholdPolicy, verifiers: Mapping[str, SignatureVerifier]
    ) -> None:
        self._policy = policy
        self._verifiers = dict(verifiers)
        missing = [key for key in policy.key_ids if key not in self._verifiers]
        if missing:
            raise ThresholdError(f"no verifier configured for policy keys: {missing}")

    @property
    def policy(self) -> ThresholdPolicy:
        return self._policy

    def verify(self, payload: bytes, signature: bytes) -> None:
        """Verify a canonical k-of-n bundle over ``payload`` (raise on refusal)."""
        bundle = ThresholdSignature.from_binary(signature)
        eligible = set(self._policy.key_ids)
        verified: set[str] = set()
        for part in bundle.parts:
            if part.key_id not in eligible:
                raise ThresholdError(
                    f"threshold signature carries ineligible key {part.key_id!r}"
                )
            verifier = self._verifiers[part.key_id]
            try:
                verifier.verify(payload, part.signature)
            except SignatureError as exc:
                raise ThresholdError(
                    f"threshold signature part for {part.key_id!r} failed: {exc}"
                ) from exc
            verified.add(part.key_id)
        if not self._policy.accepts(verified):
            raise ThresholdError(
                f"threshold not met: {len(verified)} of {self._policy.threshold} required"
            )

    @classmethod
    def from_trust_store(
        cls, store: TrustStore, policy: ThresholdPolicy
    ) -> ThresholdVerifier:
        """Build a verifier from a trust store's ``key_id -> verifier`` ring."""
        return cls(policy, store.log_verifiers())


def sign_threshold(
    payload: bytes, signers: Sequence[Signer]
) -> ThresholdSignature:
    """Sign ``payload`` with each signer and assemble a canonical bundle.

    Signers must have distinct ``key_id`` values (a key may sign a release once);
    duplicate key ids refuse.
    """
    if not signers:
        raise ThresholdError("threshold signing needs at least one signer")
    parts = [
        SignaturePart(key_id=signer.key_id, signature=signer.sign(payload))
        for signer in signers
    ]
    return ThresholdSignature(parts=tuple(parts))
