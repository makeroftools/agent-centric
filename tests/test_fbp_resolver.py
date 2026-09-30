"""Tests for the deterministic component resolver (SPEC-0007 §4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.contracts.components_lock import ComponentsLock, LockEntry
from agent_centric.fbp.cache import ContentAddressedCache, sha256_bytes
from agent_centric.fbp.resolver import (
    DirectorySource,
    ResolveError,
    Resolver,
)
from agent_centric.fbp.signing import SignatureError

_COMMIT = "e" * 40


class _Acceptor:
    """A deterministic fake verifier: accepts only the signature ``good``."""

    def __init__(self) -> None:
        self.calls = 0

    def verify(self, payload: bytes, signature: bytes) -> None:
        self.calls += 1
        if signature != b"good":
            raise SignatureError("bad signature")


def _entry(name: str, data: bytes, *, implements: tuple[str, ...] = ("component.v1",),
           sig: str = "good") -> LockEntry:
    return LockEntry(
        name=name,
        repo_url=f"ssh://git@localhost/{name}.git",
        commit_sha=_COMMIT,
        tree_sha256=sha256_bytes(data),
        implements=implements,
        signature=sig,
    )


def _lock(*entries: LockEntry, contracts: tuple[str, ...] = ("component.v1",)) -> ComponentsLock:
    return ComponentsLock(
        root=entries[0].name, entries=entries, harness_contracts=contracts
    )


def _resolver(
    tmp_path: Path, lock: ComponentsLock, *, verifier: _Acceptor | None
) -> Resolver:
    return Resolver(
        lock,
        ContentAddressedCache(tmp_path / "cache"),
        source=DirectorySource(tmp_path),
        verifier=verifier,
    )


class TestResolver:
    def test_resolves_and_caches(self, tmp_path: Path) -> None:
        data = b"counter-artifact"
        (tmp_path / "counter.tar").write_bytes(data)
        lock = _lock(_entry("counter", data))
        verifier = _Acceptor()
        resolved = _resolver(tmp_path, lock, verifier=verifier).resolve("counter")
        assert resolved.digest == sha256_bytes(data)
        assert resolved.size == len(data)
        assert verifier.calls == 1
        assert (tmp_path / "cache").exists()

    def test_hash_mismatch_fails_closed(self, tmp_path: Path) -> None:
        lock = _lock(_entry("counter", b"good"))
        (tmp_path / "counter.tar").write_bytes(b"evil")
        with pytest.raises(ResolveError):
            _resolver(tmp_path, lock, verifier=_Acceptor()).resolve("counter")

    def test_bad_signature_fails_closed(self, tmp_path: Path) -> None:
        data = b"good"
        (tmp_path / "counter.tar").write_bytes(data)
        lock = _lock(_entry("counter", data, sig="bad"))
        with pytest.raises(ResolveError):
            _resolver(tmp_path, lock, verifier=_Acceptor()).resolve("counter")

    def test_no_verifier_fails_closed(self, tmp_path: Path) -> None:
        data = b"good"
        (tmp_path / "counter.tar").write_bytes(data)
        lock = _lock(_entry("counter", data))
        with pytest.raises(ResolveError):
            _resolver(tmp_path, lock, verifier=None).resolve("counter")

    def test_unknown_contract_fails_closed(self, tmp_path: Path) -> None:
        data = b"good"
        (tmp_path / "counter.tar").write_bytes(data)
        lock = _lock(_entry("counter", data, implements=("bogus.v1",)))
        with pytest.raises(ResolveError):
            _resolver(tmp_path, lock, verifier=_Acceptor()).resolve("counter")

    def test_missing_artifact_fails_closed(self, tmp_path: Path) -> None:
        lock = _lock(_entry("counter", b"good"))
        with pytest.raises(ResolveError):
            _resolver(tmp_path, lock, verifier=_Acceptor()).resolve("counter")

    def test_resolve_all_is_name_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "b.tar").write_bytes(b"b")
        (tmp_path / "a.tar").write_bytes(b"a")
        lock = _lock(_entry("b", b"b"), _entry("a", b"a"))
        resolved = _resolver(tmp_path, lock, verifier=_Acceptor()).resolve_all()
        assert [r.entry.name for r in resolved] == ["a", "b"]
