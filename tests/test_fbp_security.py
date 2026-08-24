"""Tests for the FBP transport security primitives (``fbp/security.py``).

These prove the hardening steps of ``docs/transport_trust_boundary.md`` §5.2,
§5.4, and §5.5 that are **pure and offline-testable**:

- §5.5 — traffic integrity: HMAC-SHA256 signatures over a canonical message;
  a tampered or replayed message fails to verify.
- §5.4 — per-peer authorization: an unmapped peer, a disallowed directive kind,
  or an out-of-subtree route all fail closed.
- §5.2 — TLS credential config: opting in to TLS without complete material is
  ``ready=False`` (never a silent plaintext link).

All functions here are pure and deterministand never touch the network.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric.fbp.security import (
    INTEGRITY_TAG,
    PeerAuthz,
    PeerPolicy,
    TlsCreds,
    attach_integrity,
    canonical_json,
    configure_tls,
    integrity_headers,
    sign_payload,
    verify_and_strip_integrity,
    verify_payload,
)


class TestTrafficIntegrity:
    """§5.5 — HMAC-SHA256 over the canonical message."""

    def test_sign_and_verify_roundtrip(self) -> None:
        secret = b"super-secret"
        sig = sign_payload(
            secret, correlation_id="c1", directive_kind="run", payload={"value": 21}
        )
        assert verify_payload(
            secret, expected=sig, correlation_id="c1", directive_kind="run",
            payload={"value": 21},
        ) is True

    def test_signature_does_not_verify_on_tamper(self) -> None:
        secret = b"super-secret"
        sig = sign_payload(
            secret, correlation_id="c1", directive_kind="run", payload={"value": 21}
        )
        # A single-byte alteration to the value changes the digest.
        assert verify_payload(
            secret, expected=sig, correlation_id="c1", directive_kind="run",
            payload={"value": 22},
        ) is False

    def test_signature_does_not_verify_with_wrong_secret(self) -> None:
        sig = sign_payload(
            b"correct", correlation_id="c1", directive_kind="run", payload={"value": 21}
        )
        assert verify_payload(
            b"wrong", expected=sig, correlation_id="c1", directive_kind="run",
            payload={"value": 21},
        ) is False

    def test_signature_depends_on_correlation_and_kind(self) -> None:
        secret = b"super-secret"
        base = sign_payload(secret, correlation_id="c1", directive_kind="run", payload={})
        other_kind = sign_payload(
            secret, correlation_id="c1", directive_kind="spawn", payload={}
        )
        other_id = sign_payload(
            secret, correlation_id="c2", directive_kind="run", payload={}
        )
        assert base != other_kind
        assert base != other_id

    def test_empty_or_missing_expected_fails_closed(self) -> None:
        assert verify_payload(
            b"s", expected="", correlation_id="c1", directive_kind="run", payload={}
        ) is False

    def test_canonical_json_sorts_keys(self) -> None:
        # Identical logical payloads canonicalise identically regardless of key
        # insertion order.
        a = canonical_json("c", "run", {"b": 1, "a": {"y": 2, "x": 1}})
        b = canonical_json("c", "run", {"a": {"x": 1, "y": 2}, "b": 1})
        assert a == b

    def test_integrity_headers_carry_tag_and_match(self) -> None:
        secret = b"super-secret"
        headers = integrity_headers(
            secret, correlation_id="c1", directive_kind="run", payload={"value": 7}
        )
        assert headers["_integrity"] == INTEGRITY_TAG
        assert verify_payload(
            secret, expected=headers["_digest"], correlation_id="c1",
            directive_kind="run", payload={"value": 7},
        ) is True


class TestTrailerHelpers:
    """The payload trailer attach/verify helpers used by the wire wiring."""

    def test_attach_integrity_embeds_and_verifies(self) -> None:
        secret = b"s"
        signed = attach_integrity(
            secret, correlation_id="c1", directive_kind="run", payload={"value": 7}
        )
        # The trailer lives inside the payload (frame count unchanged).
        assert signed["_integrity"] == INTEGRITY_TAG
        assert signed["_digest"]
        assert signed["value"] == 7
        clean, ok = verify_and_strip_integrity(
            secret, correlation_id="c1", directive_kind="run", payload=signed
        )
        assert ok is True
        assert clean == {"value": 7}
        assert "_integrity" not in clean and "_digest" not in clean

    def test_attach_integrity_off_is_identity(self) -> None:
        # With no secret (integrity off) the payload is returned unchanged.
        payload = {"value": 7}
        signed = attach_integrity(
            None, correlation_id="c1", directive_kind="run", payload=payload
        )
        assert signed is payload

    def test_verify_strip_integrity_off_is_identity(self) -> None:
        payload = {"value": 7}
        clean, ok = verify_and_strip_integrity(
            None, correlation_id="c1", directive_kind="run", payload=payload
        )
        assert ok is True
        assert clean is payload

    def test_verify_rejects_missing_trailer(self) -> None:
        clean, ok = verify_and_strip_integrity(
            b"s", correlation_id="c1", directive_kind="run", payload={"value": 7}
        )
        assert ok is False
        assert "_integrity" not in clean and "_digest" not in clean
        assert clean == {"value": 7}

    def test_verify_rejects_tampered_payload(self) -> None:
        signed = attach_integrity(
            b"s", correlation_id="c1", directive_kind="run", payload={"value": 7}
        )
        tampered = dict(signed)
        tampered["value"] = 8  # single-byte alteration
        clean, ok = verify_and_strip_integrity(
            b"s", correlation_id="c1", directive_kind="run", payload=tampered
        )
        assert ok is False
        assert clean["value"] == 8

    def test_verify_rejects_wrong_secret(self) -> None:
        signed = attach_integrity(
            b"correct", correlation_id="c1", directive_kind="run", payload={"value": 7}
        )
        clean, ok = verify_and_strip_integrity(
            b"wrong", correlation_id="c1", directive_kind="run", payload=signed
        )
        assert ok is False

    def test_verify_requires_digest_string(self) -> None:
        clean, ok = verify_and_strip_integrity(
            b"s",
            correlation_id="c1",
            directive_kind="run",
            payload={"_integrity": INTEGRITY_TAG, "_digest": 123, "value": 7},
        )
        assert ok is False
        assert "_integrity" not in clean and "_digest" not in clean


class TestPeerAuthz:
    """§5.4 — per-peer authorization."""

    def _authz(self) -> PeerAuthz:
        authz = PeerAuthz()
        authz.add(PeerPolicy(peer="operator", roles=("run", "spawn", "ping")))
        authz.add(PeerPolicy(peer="bills-robot", roles=("run",), subgroup="bills-"))
        return authz

    def test_authorised_peer_runs(self) -> None:
        assert self._authz().authorize(peer="operator", directive_kind="run") is None

    def test_unknown_peer_fails_closed(self) -> None:
        reason = self._authz().authorize(peer="stranger", directive_kind="run")
        assert reason is not None
        assert "not authorised" in reason

    def test_unpermitted_role_fails_closed(self) -> None:
        reason = self._authz().authorize(peer="bills-robot", directive_kind="spawn")
        assert reason is not None
        assert "not authorised to issue" in reason

    def test_subtree_enforced(self) -> None:
        reason = self._authz().authorize(
            peer="bills-robot", directive_kind="run", routed="payments-x"
        )
        assert reason is not None
        assert "may not route to" in reason

    def test_subtree_allows_match(self) -> None:
        assert self._authz().authorize(
            peer="bills-robot", directive_kind="run", routed="bills-b1"
        ) is None

    def test_empty_role_policy_denies_all(self) -> None:
        authz = PeerAuthz()
        authz.add(PeerPolicy(peer="limited", roles=()))
        reason = authz.authorize(peer="limited", directive_kind="run")
        assert reason is not None
        assert "none" in reason


class TestTlsConfig:
    """§5.2 — mutual-TLS credential validation (fail-closed to plaintext)."""

    def test_missing_profile_fails_closed(self) -> None:
        state = configure_tls(None)
        assert state["ready"] is False

    def test_incomplete_credentials_fail_closed(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        priv = tmp_path / "key.pem"
        cert.write_bytes(b"cert")
        priv.write_bytes(b"key")
        creds = TlsCreds(public_cert_path=str(cert), private_key_path="missing")
        state = configure_tls(creds)
        assert state["ready"] is False
        assert "incomplete" in state["reason"]

    def test_missing_ca_fails_closed(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        priv = tmp_path / "key.pem"
        cert.write_bytes(b"c")
        priv.write_bytes(b"k")
        creds = TlsCreds(
            public_cert_path=str(cert),
            private_key_path=str(priv),
            ca_cert_path=str(tmp_path / "ca.pem"),
        )
        state = configure_tls(creds)
        assert state["ready"] is False

    def test_complete_credentials_ready(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        priv = tmp_path / "key.pem"
        cert.write_bytes(b"c")
        priv.write_bytes(b"k")
        creds = TlsCreds(public_cert_path=str(cert), private_key_path=str(priv))
        state = configure_tls(creds)
        assert state["ready"] is True
        assert state["integrity"] == INTEGRITY_TAG