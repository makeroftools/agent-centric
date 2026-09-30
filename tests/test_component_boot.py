"""Tests for boot-from-lock (SPEC-0007 §4, Phase 2).

Unit cases build in-memory bundles (no git) to exercise each fail-closed gate;
one end-to-end case pins the real example ``shell`` + ``registry`` components
from local git and boots the tree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import BootedTree, BootError, boot_from_lock
from agent_centric.cbp.component_bundle import build_bundle, bundle_sha256
from agent_centric.cbp.component_state import StateError
from agent_centric.cbp.registry_component import catalog_snapshot
from agent_centric.cbp.resolver import ResolveError
from agent_centric.cbp.shell_component import plan_delegation
from agent_centric.cbp.signing import SignatureError
from agent_centric.contracts.component import (
    ChildRef,
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
    StateDescriptor,
)
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry

_COMPONENTS = Path(__file__).resolve().parents[1] / "examples" / "components"

_SHELL_ENTRY = "agent_centric.cbp.shell_component:plan_delegation"
_REGISTRY_ENTRY = "agent_centric.cbp.registry_component:catalog_snapshot"


class _Acceptor:
    """A deterministic fake verifier: accepts only the signature ``good``."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"good":
            raise SignatureError("bad signature")


class _MemorySource:
    def __init__(self, bundles: dict[str, bytes]) -> None:
        self._bundles = dict(bundles)

    def fetch(self, entry: LockEntry) -> bytes:
        return self._bundles[entry.name]


def _manifest(
    name: str,
    *,
    kind: str = ComponentKind.ATOMIC,
    entry: str = _REGISTRY_ENTRY,
    implements: tuple[str, ...] = ("component.v1",),
    children: tuple[ChildRef, ...] = (),
    state: StateDescriptor | None = None,
) -> ComponentManifest:
    return ComponentManifest(
        version=ComponentVersion.V1,
        name=name,
        kind=kind,
        entry=EntryDescriptor(runtime="python", entrypoint=entry),
        implements=implements,
        children=children,
        state=state,
    )


def _boot(
    tmp_path: Path,
    root: str,
    manifests: dict[str, ComponentManifest],
    *,
    allowlist: frozenset[str],
    expected_lock_hash: str | None = None,
    entry_implements: tuple[str, ...] = ("component.v1",),
    contracts: tuple[str, ...] = ("component.v1",),
    payloads: dict[str, dict[str, bytes]] | None = None,
    allow_subprocess: bool = False,
    process_timeout: float = 30.0,
) -> BootedTree:
    payload_files = payloads or {}
    bundles = {
        name: build_bundle(manifest, payload_files.get(name))
        for name, manifest in manifests.items()
    }
    entries = tuple(
        LockEntry(
            name=name,
            repo_url=f"ssh://example/{name}.git",
            commit_sha="a" * 40,
            tree_sha256=bundle_sha256(bundle),
            implements=entry_implements,
            signature="good",
        )
        for name, bundle in sorted(bundles.items())
    )
    lock = ComponentsLock(root=root, entries=entries, harness_contracts=contracts)
    return boot_from_lock(
        lock,
        source=_MemorySource(bundles),
        cache=ContentAddressedCache(tmp_path / "cache"),
        verifier=_Acceptor(),
        entry_allowlist=allowlist,
        state_root=tmp_path / "state",
        expected_lock_hash=expected_lock_hash,
        allow_subprocess=allow_subprocess,
        process_timeout=process_timeout,
    )


class TestBoot:
    def test_boots_children_first_and_runs_root(self, tmp_path: Path) -> None:
        shell = _manifest(
            "shell",
            kind=ComponentKind.COMPOSITE,
            entry=_SHELL_ENTRY,
            children=(ChildRef(ref="registry"),),
            state=StateDescriptor(kind="sqlite", path="state/shell.db"),
        )
        registry = _manifest(
            "registry",
            entry=_REGISTRY_ENTRY,
            state=StateDescriptor(kind="sqlite", path="state/registry.db"),
        )
        tree = _boot(
            tmp_path,
            "shell",
            {"shell": shell, "registry": registry},
            allowlist=frozenset({_SHELL_ENTRY, _REGISTRY_ENTRY}),
        )
        assert [c.name for c in tree.components] == ["registry", "shell"]
        assert tree.by_name["shell"].kind == ComponentKind.COMPOSITE
        assert tree.root_component.entry is plan_delegation

        plan = tree.run({"mission": "collect", "components": ["b", "a"]})
        assert plan == {"mission": "collect", "order": ["a", "b"], "count": 2}
        assert tree.run({"mission": "collect", "components": ["b", "a"]}) == plan

    def test_state_is_namespaced_per_component(self, tmp_path: Path) -> None:
        shell = _manifest(
            "shell",
            kind=ComponentKind.COMPOSITE,
            entry=_SHELL_ENTRY,
            children=(ChildRef(ref="registry"),),
            state=StateDescriptor(kind="sqlite", path="state/shell.db"),
        )
        registry = _manifest(
            "registry",
            entry=_REGISTRY_ENTRY,
            state=StateDescriptor(kind="sqlite", path="state/registry.db"),
        )
        tree = _boot(
            tmp_path,
            "shell",
            {"shell": shell, "registry": registry},
            allowlist=frozenset({_SHELL_ENTRY, _REGISTRY_ENTRY}),
        )
        shell_state = tree.by_name["shell"].state_path
        registry_state = tree.by_name["registry"].state_path
        assert shell_state is not None and shell_state.is_file()
        assert registry_state is not None and registry_state.is_file()
        assert shell_state.parent.parent.name == "shell"
        assert registry_state.parent.parent.name == "registry"
        assert shell_state != registry_state

    def test_lock_hash_mismatch_fails_closed(self, tmp_path: Path) -> None:
        shell = _manifest("shell", entry=_SHELL_ENTRY)
        with pytest.raises(BootError):
            _boot(
                tmp_path,
                "shell",
                {"shell": shell},
                allowlist=frozenset({_SHELL_ENTRY}),
                expected_lock_hash="0" * 64,
            )

    def test_unallowlisted_root_fails_closed(self, tmp_path: Path) -> None:
        shell = _manifest("shell", entry=_SHELL_ENTRY)
        with pytest.raises(BootError):
            _boot(tmp_path, "shell", {"shell": shell}, allowlist=frozenset())

    def test_identity_mismatch_fails_closed(self, tmp_path: Path) -> None:
        # The lock entry is named ``shell`` but the verified manifest is not.
        impostor = _manifest("impostor", entry=_SHELL_ENTRY)
        with pytest.raises(BootError):
            _boot(
                tmp_path,
                "shell",
                {"shell": impostor},
                allowlist=frozenset({_SHELL_ENTRY}),
            )

    def test_manifest_contract_not_pinned_fails_closed(self, tmp_path: Path) -> None:
        shell = _manifest(
            "shell", entry=_SHELL_ENTRY, implements=("component.v1", "component.v2")
        )
        with pytest.raises(BootError):
            _boot(
                tmp_path,
                "shell",
                {"shell": shell},
                allowlist=frozenset({_SHELL_ENTRY}),
            )

    def test_unknown_lock_contract_fails_closed(self, tmp_path: Path) -> None:
        shell = _manifest("shell", entry=_SHELL_ENTRY)
        with pytest.raises(ResolveError):
            _boot(
                tmp_path,
                "shell",
                {"shell": shell},
                allowlist=frozenset({_SHELL_ENTRY}),
                entry_implements=("bogus.v1",),
            )

    def test_unsafe_state_path_fails_closed(self, tmp_path: Path) -> None:
        shell = _manifest(
            "shell",
            entry=_SHELL_ENTRY,
            state=StateDescriptor(kind="sqlite", path="../escape.db"),
        )
        with pytest.raises(StateError):
            _boot(
                tmp_path,
                "shell",
                {"shell": shell},
                allowlist=frozenset({_SHELL_ENTRY}),
            )


class TestComponentEntries:
    def test_registry_snapshot_is_sorted_and_canonical(self) -> None:
        snapshot = catalog_snapshot(
            {
                "catalog": [
                    {"name": "zeta", "source_url": "ssh://z", "kind": "python"},
                    {"name": "alpha"},
                ]
            }
        )
        assert snapshot == {
            "catalog": [
                {"name": "alpha", "source_url": "", "kind": "python"},
                {"name": "zeta", "source_url": "ssh://z", "kind": "python"},
            ]
        }

    def test_registry_snapshot_rejects_malformed(self) -> None:
        with pytest.raises(TypeError):
            catalog_snapshot({"catalog": [{"source_url": "x"}]})

    def test_shell_plan_is_deterministic(self) -> None:
        payload = {"mission": "m", "components": ["c", "a", "b"]}
        assert plan_delegation(payload) == plan_delegation(payload)
        assert plan_delegation(payload)["order"] == ["a", "b", "c"]

    def test_shell_plan_rejects_missing_mission(self) -> None:
        with pytest.raises(TypeError):
            plan_delegation({"components": ["a"]})




class TestSubprocessExecution:
    _CODE = "def run(payload):\n    return {'doubled': payload['n'] * 2}\n"

    def test_opted_in_entry_runs_from_bundle(self, tmp_path: Path) -> None:
        shell = _manifest("shell", entry="mymod:run")
        tree = _boot(
            tmp_path,
            "shell",
            {"shell": shell},
            allowlist=frozenset(),
            payloads={"shell": {"mymod.py": self._CODE.encode()}},
            allow_subprocess=True,
        )
        assert tree.run({"n": 21}) == {"doubled": 42}
        assert tree.run({"n": 21}) == {"doubled": 42}

    def test_subprocess_disabled_refuses_bundle_code(self, tmp_path: Path) -> None:
        shell = _manifest("shell", entry="mymod:run")
        with pytest.raises(BootError):
            _boot(
                tmp_path,
                "shell",
                {"shell": shell},
                allowlist=frozenset(),
                payloads={"shell": {"mymod.py": self._CODE.encode()}},
            )

    def test_subprocess_timeout_fails_closed(self, tmp_path: Path) -> None:
        from agent_centric.cbp.component_process import ComponentProcessError

        shell = _manifest("shell", entry="mymod:run")
        sleepy = b"import time\ndef run(payload):\n    time.sleep(5)\n    return 1\n"
        tree = _boot(
            tmp_path,
            "shell",
            {"shell": shell},
            allowlist=frozenset(),
            payloads={"shell": {"mymod.py": sleepy}},
            allow_subprocess=True,
            process_timeout=0.5,
        )
        with pytest.raises(ComponentProcessError):
            tree.run({})

@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestExampleShellBoot:
    def _repo(self, src: Path, dest: Path) -> Path:
        shutil.copytree(src, dest)
        flags = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), *flags, "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(dest), *flags, "commit", "-qm", "x"], check=True)
        return dest

    def test_example_shell_boots_from_lock(self, tmp_path: Path) -> None:
        from agent_centric.cbp.component_source import GitSource, pin_from_git

        shell_repo = self._repo(_COMPONENTS / "shell", tmp_path / "shell-repo")
        registry_repo = self._repo(_COMPONENTS / "registry", tmp_path / "registry-repo")
        shell_pin = pin_from_git(shell_repo)
        registry_pin = pin_from_git(registry_repo)
        entries = (
            LockEntry(
                name="shell",
                repo_url="ssh://example/shell.git",
                commit_sha=shell_pin.commit_sha,
                tree_sha256=shell_pin.tree_sha256,
                implements=("component.v1",),
                signature="good",
            ),
            LockEntry(
                name="registry",
                repo_url="ssh://example/registry.git",
                commit_sha=registry_pin.commit_sha,
                tree_sha256=registry_pin.tree_sha256,
                implements=("component.v1",),
                signature="good",
            ),
        )
        lock = ComponentsLock(
            root="shell", entries=entries, harness_contracts=("component.v1",)
        )
        tree = boot_from_lock(
            lock,
            source=GitSource({"shell": shell_repo, "registry": registry_repo}),
            cache=ContentAddressedCache(tmp_path / "cache"),
            verifier=_Acceptor(),
            entry_allowlist=frozenset({_SHELL_ENTRY, _REGISTRY_ENTRY}),
            state_root=tmp_path / "state",
            expected_lock_hash=lock.lock_hash(),
        )
        assert tree.lock_hash == lock.lock_hash()
        assert [c.name for c in tree.components] == ["registry", "shell"]
        plan = tree.run({"mission": "boot", "components": ["registry"]})
        assert plan == {"mission": "boot", "order": ["registry"], "count": 1}
        assert tree.run({"mission": "boot", "components": ["registry"]}) == plan

    def test_demo_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        import examples.component_boot as demo

        demo.main()
        out = capsys.readouterr().out
        assert "booted order (children first): ['registry', 'shell']" in out
        assert "replay_ok=True" in out
