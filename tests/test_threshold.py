"""Tests for threshold signing (``cbp/threshold.py``; SPEC-0007 §6).

Deterministic and offline: fake signers/verifiers stand in for the system tools,
and the k-of-n bundle is canonical and content-addressed. Every refusal is
fail-closed, and the verifier drops into ``boot_from_lock`` unchanged.
"""

from __future__ import annotations

import hashlib

import pytest

from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import BootError, boot_from_lock
from agent_centric.cbp.component_bundle import build_bundle, bundle_sha256
from agent_centric.cbp.signing import SignatureError
from agent_centric.cbp.threshold import (
    THRESHOLD_SCHEMA,
    SignaturePart,
    ThresholdError,
    ThresholdPolicy,
    ThresholdSignature,
    ThresholdVerifier,
    sign_threshold,
)
from agent_centric.cbp.trust import TrustStore, TrustWindow
from agent_centric.contracts.component import (
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
)
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry

_ENTRY = "agent_centric.cbp.registry_component:catalog_snapshot"


class _FakeSigner:
    """Deterministic fake signer: signature = sha256(key_id ':' payload)."""

    def __init__(self, key_id: str) -> None:
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(self._key_id.encode() + b":" + payload).hexdigest().encode()


class _FakeVerifier:
    """Deterministic fake verifier matching :class:`_FakeSigner`."""

    def __init__(self, key_id: str) -> None:
        self._key_id = key_id

    def verify(self, payload: bytes, signature: bytes) -> None:
        expected = hashlib.sha256(self._key_id.encode() + b":" + payload).hexdigest().encode()
        if signature != expected:
            raise SignatureError(f"{self._key_id}: signature does not verify")


def _pair(key_id: str) -> tuple[_FakeSigner, _FakeVerifier]:
    return _FakeSigner(key_id), _FakeVerifier(key_id)


def _verifiers(*key_ids: str) -> dict[str, _FakeVerifier]:
    return {key_id: _FakeVerifier(key_id) for key_id in key_ids}


class TestThresholdPolicy:
    def test_canonicalizes_and_accepts(self) -> None:
        policy = ThresholdPolicy(2, ("k3", "k1", "k2"))
        assert policy.key_ids == ("k1", "k2", "k3")
        assert policy.accepts({"k1", "k2"}) is True
        assert policy.accepts({"k1", "k3", "k2"}) is True

    def test_counts_only_eligible_keys(self) -> None:
        policy = ThresholdPolicy(2, ("k1", "k2"))
        assert policy.accepts({"k1", "rogue"}) is False

    def test_hash_is_order_independent(self) -> None:
        assert ThresholdPolicy(2, ("k2", "k1")).policy_hash() == ThresholdPolicy(
            2, ("k1", "k2")
        ).policy_hash()

    def test_rejects_bad_thresholds(self) -> None:
        with pytest.raises(ThresholdError):
            ThresholdPolicy(0, ("k1",))
        with pytest.raises(ThresholdError):
            ThresholdPolicy(3, ("k1", "k2"))
        with pytest.raises(ThresholdError):
            ThresholdPolicy(True, ("k1",))

    def test_rejects_bad_key_sets(self) -> None:
        with pytest.raises(ThresholdError):
            ThresholdPolicy(1, ())
        with pytest.raises(ThresholdError):
            ThresholdPolicy(1, ("k1", "k1"))
        with pytest.raises(ThresholdError):
            ThresholdPolicy(1, ("",))

    def test_round_trips(self) -> None:
        policy = ThresholdPolicy(2, ("k1", "k2"))
        assert ThresholdPolicy.from_dict(policy.to_dict()) == policy
        assert policy.to_dict()["schema"] == THRESHOLD_SCHEMA


class TestThresholdSignature:
    def test_is_canonical_and_order_independent(self) -> None:
        a = ThresholdSignature.from_pairs([("k2", b"bb"), ("k1", b"aa")])
        b = ThresholdSignature.from_pairs([("k1", b"aa"), ("k2", b"bb")])
        assert a.to_bytes() == b.to_bytes()
        assert a.key_ids == ("k1", "k2")

    def test_round_trips_through_binary(self) -> None:
        bundle = ThresholdSignature.from_pairs([("k1", b"binary\x00sig")])
        assert ThresholdSignature.from_binary(bundle.to_bytes()) == bundle

    def test_rejects_duplicate_keys(self) -> None:
        with pytest.raises(ThresholdError):
            ThresholdSignature.from_pairs([("k1", b"a"), ("k1", b"b")])

    def test_rejects_empty(self) -> None:
        with pytest.raises(ThresholdError):
            ThresholdSignature(parts=())

    def test_rejects_noncanonical_bytes(self) -> None:
        bundle = ThresholdSignature.from_pairs([("k1", b"a")])
        pretty = bundle.to_bytes().replace(b",", b", ")
        with pytest.raises(ThresholdError):
            ThresholdSignature.from_binary(pretty)

    def test_rejects_malformed_json(self) -> None:
        with pytest.raises(ThresholdError):
            ThresholdSignature.from_binary(b"not json")

    def test_rejects_non_base64_signature(self) -> None:
        with pytest.raises(ThresholdError):
            SignaturePart.from_dict({"key_id": "k1", "signature": "not base64!!"})

    def test_fingerprint_is_stable(self) -> None:
        bundle = ThresholdSignature.from_pairs([("k1", b"a"), ("k2", b"b")])
        assert bundle.fingerprint() == bundle.fingerprint()


class TestThresholdVerifier:
    def test_accepts_at_threshold(self) -> None:
        policy = ThresholdPolicy(2, ("k1", "k2", "k3"))
        verifier = ThresholdVerifier(policy, _verifiers("k1", "k2", "k3"))
        payload = b"release"
        bundle = ThresholdSignature.from_pairs(
            [("k1", _FakeSigner("k1").sign(payload)), ("k2", _FakeSigner("k2").sign(payload))]
        )
        verifier.verify(payload, bundle.to_bytes())

    def test_accepts_more_than_threshold(self) -> None:
        policy = ThresholdPolicy(2, ("k1", "k2", "k3"))
        verifier = ThresholdVerifier(policy, _verifiers("k1", "k2", "k3"))
        payload = b"release"
        bundle = sign_threshold(payload, [_FakeSigner("k3"), _FakeSigner("k1"), _FakeSigner("k2")])
        verifier.verify(payload, bundle.to_bytes())

    def test_refuses_below_threshold(self) -> None:
        policy = ThresholdPolicy(2, ("k1", "k2"))
        verifier = ThresholdVerifier(policy, _verifiers("k1", "k2"))
        payload = b"release"
        bundle = sign_threshold(payload, [_FakeSigner("k1")])
        with pytest.raises(ThresholdError):
            verifier.verify(payload, bundle.to_bytes())

    def test_refuses_an_ineligible_key(self) -> None:
        policy = ThresholdPolicy(1, ("k1",))
        verifier = ThresholdVerifier(policy, _verifiers("k1"))
        payload = b"release"
        bundle = sign_threshold(payload, [_FakeSigner("rogue")])
        with pytest.raises(ThresholdError):
            verifier.verify(payload, bundle.to_bytes())

    def test_refuses_any_invalid_part(self) -> None:
        policy = ThresholdPolicy(1, ("k1", "k2"))
        verifier = ThresholdVerifier(policy, _verifiers("k1", "k2"))
        payload = b"release"
        bundle = ThresholdSignature.from_pairs(
            [
                ("k1", _FakeSigner("k1").sign(payload)),
                ("k2", _FakeSigner("k2").sign(b"a different payload")),
            ]
        )
        with pytest.raises(ThresholdError):
            verifier.verify(payload, bundle.to_bytes())

    def test_refuses_noncanonical_bundle(self) -> None:
        policy = ThresholdPolicy(1, ("k1",))
        verifier = ThresholdVerifier(policy, _verifiers("k1"))
        payload = b"release"
        bundle = sign_threshold(payload, [_FakeSigner("k1")])
        with pytest.raises(ThresholdError):
            verifier.verify(payload, bundle.to_bytes().replace(b",", b", "))

    def test_requires_a_verifier_for_each_policy_key(self) -> None:
        with pytest.raises(ThresholdError):
            ThresholdVerifier(ThresholdPolicy(2, ("k1", "k2")), _verifiers("k1"))

    def test_from_trust_store(self) -> None:
        store = TrustStore(
            [
                TrustWindow("k1", _FakeVerifier("k1"), 0, 100),
                TrustWindow("k2", _FakeVerifier("k2"), 0, 100),
            ]
        )
        policy = ThresholdPolicy(2, ("k1", "k2"))
        verifier = ThresholdVerifier.from_trust_store(store, policy)
        payload = b"release"
        bundle = sign_threshold(payload, [_FakeSigner("k1"), _FakeSigner("k2")])
        verifier.verify(payload, bundle.to_bytes())

    def test_sign_threshold_requires_a_signer(self) -> None:
        with pytest.raises(ThresholdError):
            sign_threshold(b"x", [])


class _Acceptor:
    """Component-verifier double: accepts only the signature ``good``."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"good":
            raise SignatureError("bad component signature")


class _MemorySource:
    def __init__(self, bundles: dict[str, bytes]) -> None:
        self._bundles = bundles

    def fetch(self, entry: LockEntry) -> bytes:
        return self._bundles[entry.name]


def _bundle_and_lock() -> tuple[bytes, ComponentsLock]:
    manifest = ComponentManifest(
        version=ComponentVersion.V1,
        name="shell",
        kind=ComponentKind.ATOMIC,
        entry=EntryDescriptor(runtime="python", entrypoint=_ENTRY),
        implements=("component.v1",),
    )
    bundle = build_bundle(manifest)
    entry = LockEntry(
        name="shell",
        repo_url="ssh://example/shell.git",
        commit_sha="a" * 40,
        tree_sha256=bundle_sha256(bundle),
        implements=("component.v1",),
        signature="good",
    )
    lock = ComponentsLock(root="shell", entries=(entry,), harness_contracts=("component.v1",))
    return bundle, lock


class TestBootIntegration:
    def test_a_k_of_n_signed_lock_boots(self, tmp_path: pytest.TempPathFactory) -> None:
        bundle, lock = _bundle_and_lock()
        policy = ThresholdPolicy(2, ("k1", "k2"))
        verifier = ThresholdVerifier(policy, _verifiers("k1", "k2"))
        signed = sign_threshold(
            lock.lock_hash().encode("utf-8"), [_FakeSigner("k1"), _FakeSigner("k2")]
        )

        tree = boot_from_lock(
            lock,
            source=_MemorySource({"shell": bundle}),
            cache=ContentAddressedCache(tmp_path / "cache"),
            verifier=_Acceptor(),
            entry_allowlist=frozenset({_ENTRY}),
            state_root=tmp_path / "state",
            expected_lock_hash=lock.lock_hash(),
            lock_signature=signed.to_text(),
            lock_verifier=verifier,
        )
        assert tree.lock_verified is True
        assert [component.name for component in tree.components] == ["shell"]

    def test_a_below_threshold_lock_refuses_to_boot(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        bundle, lock = _bundle_and_lock()
        policy = ThresholdPolicy(2, ("k1", "k2"))
        verifier = ThresholdVerifier(policy, _verifiers("k1", "k2"))
        only_one = sign_threshold(lock.lock_hash().encode("utf-8"), [_FakeSigner("k1")])

        with pytest.raises(BootError):
            boot_from_lock(
                lock,
                source=_MemorySource({"shell": bundle}),
                cache=ContentAddressedCache(tmp_path / "cache"),
                verifier=_Acceptor(),
                entry_allowlist=frozenset({_ENTRY}),
                state_root=tmp_path / "state",
                expected_lock_hash=lock.lock_hash(),
                lock_signature=only_one.to_text(),
                lock_verifier=verifier,
            )
