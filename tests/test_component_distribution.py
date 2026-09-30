"""End-to-end component distribution slice (SPEC-0007 Phase 1a, offline).

Proves: pin -> components.lock -> resolve (git source) -> verify hash+signature
-> load manifest -> resolve allowlisted entry -> run -> replay identical; and
that a hash mismatch, an unknown contract, and an un-allowlisted entry all
fail closed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_centric.agents.counter import create_counter_agent
from agent_centric.agents.interface import ToolContext
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry
from agent_centric.fbp.cache import ContentAddressedCache
from agent_centric.fbp.component_bundle import (
    build_bundle_from_dir,
    bundle_sha256,
    load_manifest,
)
from agent_centric.fbp.component_runtime import (
    AllowlistedEntryResolver,
    EntryNotAllowed,
)
from agent_centric.fbp.component_source import GitSource, pin_from_git
from agent_centric.fbp.resolver import ResolveError, Resolver
from agent_centric.fbp.signing import SignatureError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

_ENTRY = "agent_centric.agents.counter:create_counter_agent"
_MANIFEST = {
    "version": "component.v1",
    "name": "counter",
    "kind": "atomic",
    "implements": ["component.v1"],
    "entry": {"runtime": "python", "entrypoint": _ENTRY},
    "children": [],
    "directive": "",
    "capabilities": [{"name": "count", "version": "1"}],
    "state": None,
    "provenance": None,
    "ports": {},
}


class _Acceptor:
    """A deterministic fake verifier: accepts only the signature ``good``."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"good":
            raise SignatureError("bad signature")


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "counter-repo"
    repo.mkdir()
    (repo / "component.json").write_text(json.dumps(_MANIFEST), encoding="utf-8")
    (repo / "counter.py").write_text("def count(t, c):\n    return t.count(c)\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    flags = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(repo), *flags, "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), *flags, "commit", "-qm", "counter"], check=True)
    return repo


def _lock(entry: LockEntry, contracts: tuple[str, ...] = ("component.v1",)) -> ComponentsLock:
    return ComponentsLock(root=entry.name, entries=(entry,), harness_contracts=contracts)


def _resolver(tmp_path: Path, lock: ComponentsLock, repo: Path, verifier: object) -> Resolver:
    return Resolver(
        lock,
        ContentAddressedCache(tmp_path / "cache"),
        source=GitSource({"counter": repo}),
        verifier=verifier,  # type: ignore[arg-type]
    )


def _run_counter(payload: dict[str, str]) -> object:
    generator = create_counter_agent()(payload, 100, ToolContext())
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value.output
    raise AssertionError("unreachable")


class TestPinning:
    def test_pin_is_deterministic(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        a = pin_from_git(repo)
        b = pin_from_git(repo)
        assert a.commit_sha == b.commit_sha
        assert a.tree_sha256 == b.tree_sha256
        assert bundle_sha256(a.bundle) == a.tree_sha256

    def test_bundle_from_dir_matches_pin(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pinned = pin_from_git(repo)
        assert bundle_sha256(build_bundle_from_dir(repo)) == pinned.tree_sha256


class TestEndToEnd:
    def test_resolve_verify_load_run(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pinned = pin_from_git(repo)
        entry = LockEntry(
            name="counter",
            repo_url="ssh://git@localhost/counter.git",
            commit_sha=pinned.commit_sha,
            tree_sha256=pinned.tree_sha256,
            implements=("component.v1",),
            signature="good",
        )
        lock = _lock(entry)
        resolver = _resolver(tmp_path, lock, repo, _Acceptor())

        resolved = resolver.resolve("counter")
        assert resolved.digest == pinned.tree_sha256
        assert resolver.resolve("counter").digest == resolved.digest  # deterministic

        cached = ContentAddressedCache(tmp_path / "cache").get(resolved.digest)
        assert cached is not None
        manifest = load_manifest(cached)
        assert manifest.name == "counter"
        assert manifest.entry.entrypoint == _ENTRY

        callable_ = AllowlistedEntryResolver(frozenset({_ENTRY})).resolve(manifest.entry)
        assert callable_ is create_counter_agent

        payload = {"text": "banana", "target": "a"}
        assert _run_counter(payload) == 3
        assert _run_counter(payload) == 3  # replay identical

    def test_hash_mismatch_fails_closed(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pinned = pin_from_git(repo)
        entry = LockEntry(
            name="counter",
            repo_url="ssh://git@localhost/counter.git",
            commit_sha=pinned.commit_sha,
            tree_sha256="0" * 64,
            implements=("component.v1",),
            signature="good",
        )
        with pytest.raises(ResolveError):
            _resolver(tmp_path, _lock(entry), repo, _Acceptor()).resolve("counter")

    def test_unknown_contract_fails_closed(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pinned = pin_from_git(repo)
        entry = LockEntry(
            name="counter",
            repo_url="ssh://git@localhost/counter.git",
            commit_sha=pinned.commit_sha,
            tree_sha256=pinned.tree_sha256,
            implements=("bogus.v1",),
            signature="good",
        )
        with pytest.raises(ResolveError):
            _resolver(tmp_path, _lock(entry), repo, _Acceptor()).resolve("counter")

    def test_bad_signature_fails_closed(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pinned = pin_from_git(repo)
        entry = LockEntry(
            name="counter",
            repo_url="ssh://git@localhost/counter.git",
            commit_sha=pinned.commit_sha,
            tree_sha256=pinned.tree_sha256,
            implements=("component.v1",),
            signature="bad",
        )
        with pytest.raises(ResolveError):
            _resolver(tmp_path, _lock(entry), repo, _Acceptor()).resolve("counter")


class TestEntryAllowlist:
    def test_unallowlisted_entry_is_refused(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        manifest = load_manifest(pin_from_git(repo).bundle)
        with pytest.raises(EntryNotAllowed):
            AllowlistedEntryResolver(frozenset()).resolve(manifest.entry)
