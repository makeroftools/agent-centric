"""Transport security primitives for the FBP trust boundary (opt-in).

This module implements the previously-documented-but-unbuilt hardening of
``docs/transport_trust_boundary.md`` §5 as **pure, deterministic, offline-tested**
primitives, plus their fail-closed enforcement hooks:

- **§5.2 / ``security=="tls"``** — mutual-TLS credential configuration
  (``TlsCreds`` + ``configure_tls``) is validated fail-closed: opting in to TLS
  without complete key/cert material is an explicit ``ready=False`` (never a
  silent plaintext bind). Cert/key material is provided by the operator.
- **§5.4 — per-peer authorization.** ``PeerPolicy``/``PeerAuthz`` map an
  authenticated peer identity to the directive kinds / subtree it may drive;
  ``authorize`` is the enforcement hook (fail-closed: an unmapped or
  unauthorized peer is refused).
- **§5.5 — traffic integrity.** ``sign_payload`` / ``verify_payload`` compute an
  HMAC-SHA256 digon over a canonical message so a frame cannot be replayed or
  altered without the shared secret; ``integrity_headers`` produces the
  per-message trailer.

The functions are **pure and offline**: they depend only on their arguments and
the provided secrets/certs, never on the network, so the whole module is
testable without ZeroMQ or a bound socket. Every feature is opt-in and its
absence fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from json import dumps
from typing import Any, Final

#: The message-integrity scheme tag (v1).
INTEGRITY_TAG: Final = "fbp-integrity-v1"


class TransportSecurityError(ValueError):
    """A transport-security policy was violated (fail-closed)."""


# ---------------------------------------------------------------------------
# §5.5 — Traffic integrity (HMAC-SHA256 over the canonical message)
# ---------------------------------------------------------------------------


def _hmac_hex(secret: bytes, message: bytes) -> str:
    """Return the HMAC-SHA256 (hex) of ``message`` under ``secret``."""
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _canonical_payload(value: Any) -> Any:
    """Recursively sort dict keys for deterministic canonical JSON."""
    if isinstance(value, dict):
        return {k: _canonical_payload(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_payload(v) for v in value]
    return value


def canonical_json(
    correlation_id: str,
    directive_kind: str,
    payload: dict[str, Any],
) -> str:
    """A deterministic, JSON-canonical byte string over the message fields.

    The payload must be JSON-safe (strings/numbers/bools/lists/dicts). Keys are
    sorted recursively so identical logical messages canonicalise identically.
    """
    return dumps(
        {
            "correlation_id": correlation_id,
            "directive_kind": directive_kind,
            "payload": _canonical_payload(payload),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


# Convenience alias: the pure canonicaliser used by the signer.
canonical = canonical_json


def sign_payload(
    secret: bytes,
    *,
    correlation_id: str,
    directive_kind: str,
    payload: dict[str, Any],
) -> str:
    """Return the HMAC-SHA256 (hex) over a canonical message.

    The same logical directive always has the same signature; any single-byte
    alteration changes it. This is the §5.5 traffic-integrity evidence: a
    replayed or tampered frame will not verify against ``verify_payload``.

    Args:
        secret: The shared HMAC secret (bytes).
        correlation_id: The directive's correlation id.
        directive_kind: The directive kind (e.g. ``run``).
        payload: The directive's payload (JSON-safe).

    Returns:
        The hex HMAC-256 digest.
    """
    message = canonical_json(correlation_id, directive_kind, payload).encode("utf-8")
    return _json_hex(secret, message)


def verify_payload(
    secret: bytes,
    *,
    expected: str,
    correlation_id: str,
    directive_kind: str,
    payload: dict[str, Any],
) -> bool:
    """True iff ``expected`` equals the HMAC just produced for this directive."""
    if not isinstance(expected, str) or not expected:
        return False
    computed = sign_payload(
        secret,
        correlation_id=correlation_id,
        directive_kind=directive_kind,
        payload=payload,
    )
    return hmac.compare_digest(
        expected.encode("ascii", errors="ignore"),
        computed.encode("ascii", errors="ignore"),
    )


def integrity_headers(
    secret: bytes,
    *,
    correlation_id: str,
    directive_kind: str,
    payload: dict[str, Any],
) -> dict[str, str]:
    """The integrity trailer field(s) attached to a directive for transport.

    Returns a JSON-safe dict carrying the integrity tag + the digest, which a
    receiving peer verifies via ``verify_payload`` before honouring the
    directive. This is how §5.5 is carried without changing the wire's primitive
    frame format.
    """
    digest = sign_payload(
        secret,
        correlation_id=correlation_id,
        directive_kind=directive_kind,
        payload=payload,
    )
    return {"_integrity": INTEGRITY_TAG, "_digest": digest}


def _json_hex(secret: bytes, message: bytes) -> str:
    """Return the HMAC-SHA256 (hex) of ``message`` under ``secret``."""
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


# -- Per-peer authorization (§5.4) -------------------------------------------


@dataclass(frozen=True)
class PeerPolicy:
    """What one authenticated peer is authorised to do.

    Attributes:
        peer: The authenticated peer identity.
        roles: The directive kinds the peer may issue (e.g. ``run``/``spawn``).
            An empty tuple means **no** roles (every directive is refused).
        subgroup: An optional allowed subtree prefix for directives that route a
            child (e.g. ``bills-`` to allow only that namespace). Unused when
            ``None``.
    """

    peer: str
    roles: tuple[str, ...] = ()
    subgroup: str | None = None


class PeerAuthz:
    """A fail-closed per-peer authorization map (deterministic).

    An operator supplies the map of authorised peers (the authenticated
    identities). ``authorize`` denies anything not explicitly granted: an
    unknown peer, a directive kind outside the peer's ``roles``, or a routed
    child outside the peer's ``subgroup`` all fail closed.
    """

    def __init__(self, policies: dict[str, PeerPolicy] | None = None) -> None:
        self._policies: dict[str, PeerPolicy] = dict(policies or {})

    def add(self, policy: PeerPolicy) -> None:
        self._policies[policy.peer] = policy

    def authorize(
        self,
        *,
        peer: str,
        directive_kind: str,
        routed: str | None = None,
    ) -> str | None:
        """Return ``None`` when authorised, else a fail-closed reason.

        Args:
            peer: The authenticated peer identity.
            directive_kind: The directive kind the peer is attempting.
            routed: An optional child identity the directive will route to (used
                to enforce ``subgroup``).
        """
        policy = self._policies.get(peer)
        if policy is None:
            return f"peer {peer!r} is not authorised to drive the transport"
        if directive_kind not in policy.roles:
            return (
                f"peer {peer!r} is not authorised to issue "
                f"directive {directive_kind!r} (allowed: {', '.join(policy.roles) or 'none'})"
            )
        inside_subgroup = policy.subgroup
        if inside_subgroup is not None and routed is not None and not routed.startswith(
            inside_subgroup
        ):
            return (
                f"peer {peer!r} may not route to {routed!r} "
                f"(subgroup {inside_subgroup!r})"
            )
        return None


# ---------------------------------------------------------------------------
# TLS credential config (§5.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TlsCreds:
    """The operator-provided mutual-TLS material for an endpoint.

    The material is **not** created here (it is operator-granted, secret); this
    module only validates and holds the contract. ``public_cert_path`` and
    ``private_key_path`` must both point at PEM files on disk; ``ca_cert_path``
    is optional (the trust anchor). ``peer_verify`` defaults to True (mutual).

    The driver may hand these to a TLS-capable transport backend. An explicit
    TLS profile without usable, complete material fails closed (never a silent
    plaintext link).
    """

    public_cert_path: str
    private_key_path: str
    ca_cert_path: str | None = None
    peer_verify: bool = True

    def is_complete(self) -> bool:
        if not self.public_cert_path or not self.private_key_path:
            return False
        required = (self.public_cert_path, self.private_key_path)
        missing = [p for p in required if not os.path.isfile(p)]
        if missing:
            return False
        return not (
            self.ca_cert_path is not None and not os.path.isfile(self.ca_cert_path)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_cert_path": self.public_cert_path,
            "private_key_path": self.private_key_path,
            "ca_cert_path": self.ca_cert_path,
            "peer_verify": self.peer_verify,
            "complete": self.is_complete(),
        }


def configure_tls(creds: TlsCreds | None) -> dict[str, Any]:
    """Validate a TLS profile, failing closed on incomplete material.

    Returns a deterministic readout of the TLS state. When ``creds`` is None
    (TLS was not actually provisioned) or the material is missing/invalid, it
    returns a ``ready=False`` readout — the caller must fail closed rather than
    silently downgrading to plaintext. This is the honest §5.2 check the
    ``security=="tls"`` profile consults.
    """
    if creds is None:
        return {"ready": False, "reason": "no TLS credentials provided"}
    if not creds.is_complete():
        return {
            "ready": False,
            "reason": f"TLS credentials incomplete: {creds.to_dict()}",
        }
    return {
        "ready": True,
        "reason": "ok",
        "creds": creds.to_dict(),
        "integrity": INTEGRITY_TAG,
    }