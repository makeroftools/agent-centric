"""Tests for release signing + the transparency log (SPEC-0007 Phase 0.5b).

Uses a deterministic hash-based signer/verifier so the chain and fail-closed
contracts are exercised without a real signing key. System signers are tested
only for misconfiguration refusal (a real key is operator-held, not a test
fixture).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_centric.cbp.signing import SignatureError
from agent_centric.cbp.signing_service import (
    GpgSigner,
    MinisignSigner,
    SigningError,
    record_release,
    sign_lock,
)
from agent_centric.cbp.transparency import (
    GENESIS,
    TransparencyError,
    TransparencyLog,
)
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry


class _HashSigner:
    """Deterministic test signer: signature = sha256(b'sig:' + payload)."""

    def __init__(self, key_id: str = "test-key") -> None:
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(b"sig:" + payload).hexdigest().encode()


class _HashVerifier:
    """Deterministic verifier matching :class:`_HashSigner`."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != hashlib.sha256(b"sig:" + payload).hexdigest().encode():
            raise SignatureError("bad signature")


def _lock(name: str = "counter") -> ComponentsLock:
    entry = LockEntry(
        name=name,
        repo_url=f"ssh://example/{name}.git",
        commit_sha="a" * 40,
        tree_sha256="b" * 64,
        implements=("component.v1",),
        signature="good",
    )
    return ComponentsLock(root=name, entries=(entry,), harness_contracts=("component.v1",))


class TestSignLock:
    def test_signs_the_lock_hash_and_verifies(self) -> None:
        lock = _lock()
        signed = sign_lock(lock, _HashSigner())
        assert signed.lock_hash == lock.lock_hash()
        _HashVerifier().verify(signed.lock_hash.encode(), signed.signature.encode())

    def test_gpg_requires_a_key_id(self) -> None:
        with pytest.raises(SigningError):
            GpgSigner("")

    def test_minisign_requires_a_secret_key_file(self, tmp_path: Path) -> None:
        with pytest.raises(SigningError):
            MinisignSigner(tmp_path / "missing.key")


class TestTransparencyLog:
    def test_empty_log_head_is_genesis(self, tmp_path: Path) -> None:
        log = TransparencyLog(tmp_path / "transparency.jsonl")
        assert log.head() == GENESIS
        assert log.verify(_HashVerifier()) == ()

    def test_appends_a_chained_chain(self, tmp_path: Path) -> None:
        log = TransparencyLog(tmp_path / "transparency.jsonl")
        signer = _HashSigner()
        _, first = record_release(_lock("a"), signer, log)
        _, second = record_release(_lock("b"), signer, log)
        assert first.seq == 0 and first.prev == GENESIS
        assert second.seq == 1 and second.prev == first.entry_hash()
        assert log.verify(_HashVerifier()) == (first, second)

    def test_tamper_breaks_the_chain(self, tmp_path: Path) -> None:
        path = tmp_path / "transparency.jsonl"
        log = TransparencyLog(path)
        signer = _HashSigner()
        record_release(_lock("a"), signer, log)
        record_release(_lock("b"), signer, log)
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["lock_hash"] = "c" * 64
        lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(TransparencyError):
            log.verify(_HashVerifier())

    def test_truncation_is_detected_via_expected_head(self, tmp_path: Path) -> None:
        path = tmp_path / "transparency.jsonl"
        log = TransparencyLog(path)
        signer = _HashSigner()
        record_release(_lock("a"), signer, log)
        record_release(_lock("b"), signer, log)
        head = log.head()
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        with pytest.raises(TransparencyError):
            log.verify(_HashVerifier(), expected_head=head)
        assert log.read()  # the shortened chain is itself still well-formed

    def test_unknown_signing_key_fails_closed(self, tmp_path: Path) -> None:
        log = TransparencyLog(tmp_path / "transparency.jsonl")
        record_release(_lock("a"), _HashSigner("rotated-key"), log)
        with pytest.raises(TransparencyError):
            log.verify({})

    def test_bad_signature_fails_closed(self, tmp_path: Path) -> None:
        log = TransparencyLog(tmp_path / "transparency.jsonl")
        log.append("a" * 64, "test-key", "00" * 32)
        with pytest.raises(TransparencyError):
            log.verify(_HashVerifier())

    def test_append_refuses_a_corrupt_log(self, tmp_path: Path) -> None:
        path = tmp_path / "transparency.jsonl"
        path.write_text("not-json\n", encoding="utf-8")
        log = TransparencyLog(path)
        with pytest.raises(TransparencyError):
            record_release(_lock("a"), _HashSigner(), log)


class TestDemo:
    def test_demo_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        import examples.release_signing as demo

        demo.main()
        out = capsys.readouterr().out
        assert "transparency records=1" in out
        assert "verified=True" in out
