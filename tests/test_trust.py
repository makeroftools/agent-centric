"""Tests for the rotating trust store (SPEC-0007 §6 key rotation).

Deterministic and offline: keys have explicit trust windows; overlap during a
rotation lets the outgoing key keep *verifying* while only the incoming key
*signs*; every refusal is fail-closed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import boot_from_lock
from agent_centric.cbp.component_bundle import build_bundle, bundle_sha256
from agent_centric.cbp.signing import SignatureError
from agent_centric.cbp.signing_service import record_release
from agent_centric.cbp.transparency import TransparencyError, TransparencyLog
from agent_centric.cbp.trust import TrustError, TrustStore, TrustWindow
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


def _overlap_store() -> TrustStore:
    """k1 retires from signing at 100; k2 signs from 50; both verify over [50,100)."""
    _, k1v = _pair("k1")
    k2s, k2v = _pair("k2")
    return TrustStore(
        [
            TrustWindow("k1", k1v, 0, 100),
            TrustWindow("k2", k2v, 50, 200, signer=k2s),
        ]
    )


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


class TestTrustWindow:
    def test_empty_key_id_is_rejected(self) -> None:
        _, verifier = _pair("k1")
        with pytest.raises(TrustError):
            TrustWindow("", verifier, 0, 10)

    def test_inverted_window_is_rejected(self) -> None:
        _, verifier = _pair("k1")
        with pytest.raises(TrustError):
            TrustWindow("k1", verifier, 10, 10)

    def test_signer_key_id_must_match(self) -> None:
        signer, verifier = _pair("k1")
        with pytest.raises(TrustError):
            TrustWindow("k2", verifier, 0, 10, signer=signer)

    def test_covers_is_half_open(self) -> None:
        _, verifier = _pair("k1")
        window = TrustWindow("k1", verifier, 0, 10)
        assert window.covers(0) and window.covers(9)
        assert not window.covers(10)


class TestTrustStore:
    def test_requires_at_least_one_window(self) -> None:
        with pytest.raises(TrustError):
            TrustStore([])

    def test_duplicate_key_ids_are_rejected(self) -> None:
        _, v1 = _pair("k1")
        _, v2 = _pair("k1")
        with pytest.raises(TrustError):
            TrustStore([TrustWindow("k1", v1, 0, 10), TrustWindow("k1", v2, 5, 20)])

    def test_signer_at_selects_the_single_signing_key(self) -> None:
        store = _overlap_store()
        assert store.signer_at(120).key_id == "k2"
        assert store.signer_at(60).key_id == "k2"  # overlap: k1 is verify-only

    def test_signer_at_refuses_before_the_signing_window(self) -> None:
        store = _overlap_store()
        with pytest.raises(TrustError):
            store.signer_at(10)  # k1 is retired for signing; k2 not started

    def test_signer_at_refuses_outside_every_window(self) -> None:
        _, v1 = _pair("k1")
        store = TrustStore([TrustWindow("k1", v1, 0, 100)])
        with pytest.raises(TrustError):
            store.signer_at(100)

    def test_ambiguous_signing_keys_refuse(self) -> None:
        k1s, k1v = _pair("k1")
        k2s, k2v = _pair("k2")
        store = TrustStore(
            [
                TrustWindow("k1", k1v, 0, 100, signer=k1s),
                TrustWindow("k2", k2v, 50, 200, signer=k2s),
            ]
        )
        with pytest.raises(TrustError):
            store.signer_at(60)

    def test_active_key_ids_overlap(self) -> None:
        store = _overlap_store()
        assert store.active_key_ids(60) == ("k1", "k2")
        assert store.active_key_ids(10) == ("k1",)
        assert store.active_key_ids(150) == ("k2",)

    def test_verifier_for_unknown_key_refuses(self) -> None:
        _, v1 = _pair("k1")
        store = TrustStore([TrustWindow("k1", v1, 0, 10)])
        with pytest.raises(TrustError):
            store.verifier_for("nope")

    def test_verified_key_accepts_either_overlapping_key(self) -> None:
        store = _overlap_store()
        payload = b"payload"
        assert store.verified_key(payload, _FakeSigner("k1").sign(payload), at=60) == "k1"
        assert store.verified_key(payload, _FakeSigner("k2").sign(payload), at=60) == "k2"

    def test_verified_key_respects_the_window(self) -> None:
        store = _overlap_store()
        with pytest.raises(SignatureError):
            # k2 has not started at t=10, so its signature is not trusted there.
            store.verified_key(b"p", _FakeSigner("k2").sign(b"p"), at=10)

    def test_unknown_signature_fails_closed(self) -> None:
        _, v1 = _pair("k1")
        store = TrustStore([TrustWindow("k1", v1, 0, 100)])
        with pytest.raises(SignatureError):
            store.verify(b"p", b"not-a-signature")

    def test_revoking_a_key_is_removal_from_the_store(self) -> None:
        _, k1v = _pair("k1")
        payload = b"p"
        signature = _FakeSigner("k1").sign(payload)
        TrustStore([TrustWindow("k1", k1v, 0, 100)]).verify(payload, signature)
        # Same signature, but k1 has been revoked (removed):
        _, k2v = _pair("k2")
        with pytest.raises(SignatureError):
            TrustStore([TrustWindow("k2", k2v, 0, 100)]).verify(payload, signature)


class TestRotationIntegration:
    def test_rotation_records_key_ids_and_verifies_the_log(self, tmp_path: Path) -> None:
        _bundle, lock = _bundle_and_lock()
        store = _overlap_store()
        log = TransparencyLog(tmp_path / "log.jsonl")

        # A release before the incoming key exists, signed by the outgoing key.
        record_release(lock, _FakeSigner("k1"), log)
        # A release after rotation: only the incoming key signs now.
        record_release(lock, store.signer_at(120), log)

        entries = log.verify(store.log_verifiers())
        assert [entry.key_id for entry in entries] == ["k1", "k2"]

    def test_signed_rotated_lock_boots(self, tmp_path: Path) -> None:
        bundle, lock = _bundle_and_lock()
        store = _overlap_store()
        log = TransparencyLog(tmp_path / "log.jsonl")
        signed, _entry = record_release(lock, store.signer_at(120), log)

        tree = boot_from_lock(
            lock,
            source=_MemorySource({"shell": bundle}),
            cache=ContentAddressedCache(tmp_path / "cache"),
            verifier=_Acceptor(),
            entry_allowlist=frozenset({_ENTRY}),
            state_root=tmp_path / "state",
            expected_lock_hash=lock.lock_hash(),
            lock_signature=signed.signature,
            lock_verifier=store,
            transparency=log,
            expected_transparency_head=log.head(),
        )
        assert tree.lock_verified is True
        assert [component.name for component in tree.components] == ["shell"]

    def test_unknown_log_key_fails_closed(self, tmp_path: Path) -> None:
        _bundle, lock = _bundle_and_lock()
        log = TransparencyLog(tmp_path / "log.jsonl")
        record_release(lock, _FakeSigner("rogue"), log)
        store = _overlap_store()
        with pytest.raises(TransparencyError):
            log.verify(store.log_verifiers())


class TestDemo:
    def test_demo_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        import examples.key_rotation as demo

        demo.main()
        out = capsys.readouterr().out
        assert "before signer=release-key-1 active=('release-key-1',)" in out
        assert "after signer=release-key-2 active=('release-key-1', 'release-key-2')" in out
        assert "log keys=['release-key-1', 'release-key-2'] verified=True" in out
