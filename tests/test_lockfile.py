"""Tests for the design → pin → ``components.lock`` producer (SPEC-0008 Phase 3).

Offline and deterministic: a design is untrusted input; pinning resolves its
requested refs to immutable commits and content-addressed bundles, refuses an
invalid/unnamed/unsigned/unknown component fail-closed, and yields the same
``lock_hash`` every time. One end-to-end case pins the real example ``shell`` +
``registry`` components, records the signed lock, and boots the tree.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import boot_from_lock
from agent_centric.cbp.component_source import GitSource, pin_from_git
from agent_centric.cbp.lockfile import ComponentPin, PinError, pin_design
from agent_centric.cbp.signing import SignatureError
from agent_centric.cbp.signing_service import record_release
from agent_centric.cbp.transparency import TransparencyLog
from agent_centric.contracts.design import Design, RequestedComponent

_COMPONENTS = Path(__file__).resolve().parents[1] / "examples" / "components"
_SHELL_ENTRY = "agent_centric.cbp.shell_component:plan_delegation"
_REGISTRY_ENTRY = "agent_centric.cbp.registry_component:catalog_snapshot"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

_NETWORK = {
    "components": [
        {"id": "shell", "task": "plan", "args": {}},
        {"id": "registry", "task": "catalog", "args": {}},
    ],
    "edges": [],
}

_CYCLIC = {
    "components": [
        {"id": "a", "task": "t", "args": {"x": 1}},
        {"id": "b", "task": "t", "args": {"y": 1}},
    ],
    "edges": [
        {"source": "a", "source_field": "x", "target": "b", "target_arg": "y"},
        {"source": "b", "source_field": "y", "target": "a", "target_arg": "x"},
    ],
}


class _Acceptor:
    """A deterministic fake verifier: accepts only the signature ``good``."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"good":
            raise SignatureError("bad signature")


class _HashSigner:
    """Deterministic lock signer: signature = sha256(b'lock:' + payload)."""

    @property
    def key_id(self) -> str:
        return "lock-test-key"

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(b"lock:" + payload).hexdigest().encode()


class _HashVerifier:
    """Deterministic lock verifier matching :class:`_HashSigner`."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != hashlib.sha256(b"lock:" + payload).hexdigest().encode():
            raise SignatureError("bad lock signature")


def _git(repo: Path, *args: str) -> None:
    flags = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(repo), *flags, *args], check=True)


def _repo_from(dir_: Path, dest: Path) -> Path:
    shutil.copytree(dir_, dest)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-qm", "component")
    return dest


def _manifest(name: str, *, implements: tuple[str, ...] = ("component.v1",)) -> dict[str, object]:
    return {
        "version": "component.v1",
        "name": name,
        "kind": "atomic",
        "implements": list(implements),
        "entry": {"runtime": "python", "entrypoint": _REGISTRY_ENTRY},
        "children": [],
        "directive": "",
        "capabilities": [],
        "state": None,
        "provenance": None,
        "ports": {},
    }


def _repo_with(tmp_path: Path, key: str, manifest: dict[str, object]) -> Path:
    repo = tmp_path / f"{key}-repo"
    repo.mkdir()
    (repo / "component.json").write_text(json.dumps(manifest), encoding="utf-8")
    (repo / "mod.py").write_text("def run(p):\n    return p\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "component")
    return repo


def _design(network: dict[str, object], *names: str) -> Design:
    return Design(
        id="d1",
        network=network,
        components=tuple(RequestedComponent(name, "HEAD") for name in names),
    )


def _example_component_pins(tmp_path: Path) -> dict[str, ComponentPin]:
    return {
        "shell": ComponentPin(
            repo=_repo_from(_COMPONENTS / "shell", tmp_path / "shell-repo"),
            repo_url="ssh://git@localhost/shell.git",
            signature="good",
        ),
        "registry": ComponentPin(
            repo=_repo_from(_COMPONENTS / "registry", tmp_path / "registry-repo"),
            repo_url="ssh://git@localhost/registry.git",
            signature="good",
        ),
    }


class TestPinDesign:
    def test_pin_is_deterministic_and_sorted(self, tmp_path: Path) -> None:
        sources = _example_component_pins(tmp_path)
        design = _design(_NETWORK, "registry", "shell")
        first = pin_design(design, sources, root="shell")
        second = pin_design(design, sources, root="shell")
        assert first.lock_hash() == second.lock_hash()
        assert [entry.name for entry in first.entries] == ["registry", "shell"]
        assert first.root == "shell"
        assert first.entry("shell").commit_sha == pin_from_git(sources["shell"].repo).commit_sha
        assert first.entry("registry").tree_sha256 == (
            pin_from_git(sources["registry"].repo).tree_sha256
        )

    def test_pin_record_boot_end_to_end(self, tmp_path: Path) -> None:
        sources = _example_component_pins(tmp_path)
        lock = pin_design(_design(_NETWORK, "shell", "registry"), sources, root="shell")
        log = TransparencyLog(tmp_path / "log.jsonl")
        signed, _entry = record_release(lock, _HashSigner(), log)

        tree = boot_from_lock(
            lock,
            source=GitSource({name: pin.repo for name, pin in sources.items()}),
            cache=ContentAddressedCache(tmp_path / "cache"),
            verifier=_Acceptor(),
            entry_allowlist=frozenset({_SHELL_ENTRY, _REGISTRY_ENTRY}),
            state_root=tmp_path / "state",
            expected_lock_hash=lock.lock_hash(),
            lock_signature=signed.signature,
            lock_verifier=_HashVerifier(),
            transparency=log,
            expected_transparency_head=log.head(),
        )
        assert tree.lock_hash == lock.lock_hash()
        assert tree.lock_verified is True
        assert [component.name for component in tree.components] == ["registry", "shell"]
        plan = tree.run({"mission": "pin", "components": ["registry"]})
        assert plan == {"mission": "pin", "order": ["registry"], "count": 1}


class TestPinFailClosed:
    def test_invalid_design_is_refused(self, tmp_path: Path) -> None:
        sources = _example_component_pins(tmp_path)
        with pytest.raises(PinError):
            pin_design(_design(_CYCLIC, "shell"), sources, root="shell")

    def test_no_components_is_refused(self) -> None:
        with pytest.raises(PinError):
            pin_design(Design(id="empty", network=_NETWORK), {}, root="shell")

    def test_root_not_requested_is_refused(self, tmp_path: Path) -> None:
        sources = _example_component_pins(tmp_path)
        with pytest.raises(PinError):
            pin_design(_design(_NETWORK, "registry"), sources, root="shell")

    def test_unknown_source_is_refused(self, tmp_path: Path) -> None:
        sources = _example_component_pins(tmp_path)
        del sources["registry"]
        with pytest.raises(PinError):
            pin_design(_design(_NETWORK, "shell", "registry"), sources, root="shell")

    def test_unsigned_component_is_refused(self, tmp_path: Path) -> None:
        repo = _repo_with(tmp_path, "shell", _manifest("shell"))
        sources = {"shell": ComponentPin(repo=repo, repo_url="ssh://x/shell.git")}
        with pytest.raises(PinError):
            pin_design(_design(_NETWORK, "shell"), sources, root="shell")

    def test_identity_mismatch_is_refused(self, tmp_path: Path) -> None:
        repo = _repo_with(tmp_path, "shell", _manifest("impostor"))
        sources = {
            "shell": ComponentPin(repo=repo, repo_url="ssh://x/shell.git", signature="good")
        }
        with pytest.raises(PinError):
            pin_design(_design(_NETWORK, "shell"), sources, root="shell")

    def test_unpinned_contract_is_refused(self, tmp_path: Path) -> None:
        repo = _repo_with(
            tmp_path, "shell", _manifest("shell", implements=("component.v1", "bogus.v1"))
        )
        sources = {
            "shell": ComponentPin(repo=repo, repo_url="ssh://x/shell.git", signature="good")
        }
        with pytest.raises(PinError):
            pin_design(_design(_NETWORK, "shell"), sources, root="shell")

    def test_unresolvable_ref_is_refused(self, tmp_path: Path) -> None:
        repo = _repo_with(tmp_path, "shell", _manifest("shell"))
        sources = {
            "shell": ComponentPin(repo=repo, repo_url="ssh://x/shell.git", signature="good")
        }
        design = Design(
            id="d1",
            network=_NETWORK,
            components=(RequestedComponent("shell", "no-such-ref"),),
        )
        with pytest.raises(PinError):
            pin_design(design, sources, root="shell")


class TestDemo:
    def test_demo_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        import examples.design_pin as demo

        demo.main()
        out = capsys.readouterr().out
        assert "lock_hash=" in out
        assert "entries=['registry', 'shell']" in out
        assert "booted order (children first): ['registry', 'shell']" in out
        assert "replay_ok=True" in out
