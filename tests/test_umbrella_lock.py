"""Tests for the offline umbrella `components.lock` (SPEC-0011).

Deterministic and offline: the committed lock is regenerated from the design and
the component directories; the committed bundles must hash to their pins; the
lock signature verifies; the tree boots from a **directory source** and replays
identically; and a tampered bundle is refused. No keys, no network.

The lifecycle uses the tool's clearly-labelled **offline test** signer/verifier
(never production).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import BootError, boot_from_lock
from agent_centric.cbp.component_bundle import build_bundle_from_dir, bundle_sha256
from agent_centric.cbp.design import validate_design
from agent_centric.cbp.lockfile import DirectoryPin, PinError, pin_design
from agent_centric.cbp.resolver import DirectorySource, ResolveError, Resolver
from agent_centric.contracts.components_lock import ComponentsLock
from agent_centric.contracts.design import Design

_REPO = Path(__file__).resolve().parents[1]
_COMPONENTS = _REPO / "examples" / "components"
_BUNDLES = _REPO / "artifacts" / "components"
_DESIGN = _REPO / "designs" / "core.v1.json"
_LOCK = _REPO / "components.lock"
_SIG = _REPO / "components.lock.sig"
_ENTRIES = frozenset(
    {
        "agent_centric.cbp.shell_component:plan_delegation",
        "agent_centric.cbp.registry_component:catalog_snapshot",
    }
)


def _tool():
    spec = importlib.util.spec_from_file_location(
        "cbp_umbrella_lock", _REPO / "tools" / "umbrella-lock.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TOOL = _tool()


def _design() -> Design:
    return Design.from_dict(json.loads(_DESIGN.read_text(encoding="utf-8")))


def _committed() -> ComponentsLock:
    return ComponentsLock.from_dict(json.loads(_LOCK.read_text(encoding="utf-8")))


def _regenerate() -> ComponentsLock:
    design = _design()
    signer = _TOOL.OfflineTestSigner()
    rel = "examples/components"
    sources = {
        component.name: DirectoryPin(
            path=_COMPONENTS / component.name,
            repo_url=f"dir://{rel}/{component.name}",
            signature=signer.sign(
                build_bundle_from_dir(_COMPONENTS / component.name)
            ).decode(),
        )
        for component in design.components
    }
    return pin_design(design, sources, root="shell")


class TestCommittedUmbrella:
    def test_design_is_valid(self) -> None:
        assert validate_design(_design()).ok is True

    def test_lock_is_reproducible_and_content_addressed(self) -> None:
        committed = _committed()
        regenerated = _regenerate()
        assert regenerated.lock_hash() == committed.lock_hash()
        assert [entry.name for entry in committed.entries] == ["registry", "shell"]
        assert committed.root == "shell"
        assert all(len(entry.tree_sha256) == 64 for entry in committed.entries)

    def test_committed_bundles_match_their_pins(self) -> None:
        for entry in _committed().entries:
            committed_bytes = (_BUNDLES / f"{entry.name}.tar").read_bytes()
            assert bundle_sha256(committed_bytes) == entry.tree_sha256
            assert committed_bytes == build_bundle_from_dir(_COMPONENTS / entry.name)

    def test_lock_signature_verifies(self) -> None:
        lock = _committed()
        signature = _SIG.read_text(encoding="utf-8").strip().encode("utf-8")
        _TOOL.OfflineTestVerifier().verify(lock.lock_hash().encode("utf-8"), signature)

    def test_verify_command_exit_zero(self) -> None:
        assert (
            _TOOL.main(
                [
                    "--repo-root",
                    str(_REPO),
                    "verify",
                    "--lock",
                    str(_LOCK),
                    "--sig",
                    str(_SIG),
                ]
            )
            == 0
        )


class TestOfflineBoot:
    def test_boot_and_replay(self, tmp_path: Path) -> None:
        lock = _committed()
        signature = _SIG.read_text(encoding="utf-8").strip()
        tree = boot_from_lock(
            lock,
            source=DirectorySource(_BUNDLES),
            cache=ContentAddressedCache(tmp_path / "cache"),
            verifier=_TOOL.OfflineTestVerifier(),
            entry_allowlist=_ENTRIES,
            state_root=tmp_path / "state",
            expected_lock_hash=lock.lock_hash(),
            lock_signature=signature,
            lock_verifier=_TOOL.OfflineTestVerifier(),
        )
        assert tree.lock_hash == lock.lock_hash()
        assert tree.lock_verified is True
        assert [component.name for component in tree.components] == ["registry", "shell"]
        work = {"mission": "umbrella", "components": ["registry"]}
        assert tree.run(work) == tree.run(work)

    def test_tampered_bundle_is_refused(self, tmp_path: Path) -> None:
        bundles = tmp_path / "bundles"
        shutil.copytree(_BUNDLES, bundles)
        target = bundles / "registry.tar"
        data = bytearray(target.read_bytes())
        data[-1] ^= 0xFF
        target.write_bytes(bytes(data))
        resolver = Resolver(
            _committed(),
            ContentAddressedCache(tmp_path / "cache"),
            source=DirectorySource(bundles),
            verifier=_TOOL.OfflineTestVerifier(),
        )
        with pytest.raises(ResolveError):
            resolver.resolve("registry")

    def test_bad_lock_signature_is_refused(self, tmp_path: Path) -> None:
        lock = _committed()
        with pytest.raises(BootError):
            boot_from_lock(
                lock,
                source=DirectorySource(_BUNDLES),
                cache=ContentAddressedCache(tmp_path / "cache"),
                verifier=_TOOL.OfflineTestVerifier(),
                entry_allowlist=_ENTRIES,
                state_root=tmp_path / "state",
                expected_lock_hash=lock.lock_hash(),
                lock_signature="not-a-signature",
                lock_verifier=_TOOL.OfflineTestVerifier(),
            )


class TestDirectoryPinFailClosed:
    def _component_dir(self, tmp_path: Path, name: str, declared: str) -> Path:
        directory = tmp_path / name
        directory.mkdir()
        manifest = {
            "version": "component.v1",
            "name": declared,
            "kind": "atomic",
            "implements": ["component.v1"],
            "entry": {"runtime": "python", "entrypoint": "mod:run"},
            "children": [],
            "directive": "",
            "capabilities": [],
            "state": None,
            "provenance": None,
            "ports": {},
        }
        (directory / "component.json").write_text(json.dumps(manifest), encoding="utf-8")
        (directory / "mod.py").write_text("def run(p):\n    return p\n", encoding="utf-8")
        return directory

    def test_missing_directory_is_refused(self, tmp_path: Path) -> None:
        sources = {
            "shell": DirectoryPin(
                path=tmp_path / "absent", repo_url="dir://x/shell", signature="sig"
            )
        }
        with pytest.raises(PinError):
            pin_design(_design(), sources, root="shell")

    def test_unsigned_directory_is_refused(self, tmp_path: Path) -> None:
        directory = self._component_dir(tmp_path, "shell", "shell")
        sources = {"shell": DirectoryPin(path=directory, repo_url="dir://x/shell")}
        with pytest.raises(PinError):
            pin_design(_design(), sources, root="shell")

    def test_identity_mismatch_is_refused(self, tmp_path: Path) -> None:
        directory = self._component_dir(tmp_path, "shell", "impostor")
        sources = {
            "shell": DirectoryPin(path=directory, repo_url="dir://x/shell", signature="sig")
        }
        with pytest.raises(PinError):
            pin_design(_design(), sources, root="shell")

    def test_directory_pin_is_deterministic(self, tmp_path: Path) -> None:
        directory = self._component_dir(tmp_path, "shell", "shell")
        first = bundle_sha256(build_bundle_from_dir(directory))
        assert first == bundle_sha256(build_bundle_from_dir(directory))
        (directory / "mod.py").write_text("def run(p):\n    return p + 1\n", encoding="utf-8")
        assert bundle_sha256(build_bundle_from_dir(directory)) != first
