"""End-to-end demo of booting a tree from a components lock (SPEC-0007 Phase 2).

Offline, no network, no server. It shows the Phase 2 bridge from a pinned lock to
a runnable tree:

1. the example ``shell`` component is the **root** — an ordinary composite that
   **points to** the ``registry`` component (its own ``components.lock`` entry);
2. both are **pinned** from local git repositories;
3. the lock's content address (``lock_hash``) is checked against the expected
   value, so silent lock drift is an explicit failure;
4. the harness **boots** the tree: resolve the graph children-first, verify every
   pin (hash + signature), cross-check manifests, materialize each component's
   namespaced SQLite state, and resolve the root's allowlisted entry;
5. running the root's entry twice is identical (deterministic).

Signature note: this demo uses a clearly-labelled ``_DemoVerifier`` so the slice
runs without a signing key. Production verification uses ``minisign``/``gpg``
via :class:`agent_centric.cbp.signing.SystemVerifier` — never the demo verifier.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import boot_from_lock
from agent_centric.cbp.component_source import GitSource, pin_from_git
from agent_centric.cbp.signing import SignatureError
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry

_HERE = Path(__file__).resolve().parent
_COMPONENTS = _HERE / "components"
_ENTRIES = frozenset(
    {
        "agent_centric.cbp.shell_component:plan_delegation",
        "agent_centric.cbp.registry_component:catalog_snapshot",
    }
)


class _DemoVerifier:
    """DEMO ONLY: accepts the fixed signature ``demo``. Never use in production."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"demo":
            raise SignatureError("demo verifier rejected the signature")


def _init_repo(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    flags = ["-c", "user.email=demo@example.com", "-c", "user.name=demo"]
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), *flags, "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dest), *flags, "commit", "-qm", "component"], check=True)
    return dest


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="component-boot-") as tmp:
        root = Path(tmp)
        shell_repo = _init_repo(_COMPONENTS / "shell", root / "shell-repo")
        registry_repo = _init_repo(_COMPONENTS / "registry", root / "registry-repo")
        shell_pin = pin_from_git(shell_repo)
        registry_pin = pin_from_git(registry_repo)

        entries = (
            LockEntry(
                name="shell",
                repo_url="ssh://git@localhost/shell.git",
                commit_sha=shell_pin.commit_sha,
                tree_sha256=shell_pin.tree_sha256,
                implements=("component.v1",),
                signature="demo",
            ),
            LockEntry(
                name="registry",
                repo_url="ssh://git@localhost/registry.git",
                commit_sha=registry_pin.commit_sha,
                tree_sha256=registry_pin.tree_sha256,
                implements=("component.v1",),
                signature="demo",
            ),
        )
        lock = ComponentsLock(
            root="shell", entries=entries, harness_contracts=("component.v1",)
        )
        print(f"lock_hash={lock.lock_hash()[:12]} root={lock.root}")

        tree = boot_from_lock(
            lock,
            source=GitSource({"shell": shell_repo, "registry": registry_repo}),
            cache=ContentAddressedCache(root / "cache"),
            verifier=_DemoVerifier(),
            entry_allowlist=_ENTRIES,
            state_root=root / "state",
            expected_lock_hash=lock.lock_hash(),
        )
        print(f"booted order (children first): {[c.name for c in tree.components]}")
        for component in tree.components:
            state = (
                component.state_path.relative_to(root)
                if component.state_path is not None
                else None
            )
            print(f"  {component.name:9s} kind={component.kind:10s} state={state}")

        work = {"mission": "collect-utilities", "components": ["registry"]}
        first = tree.run(work)
        second = tree.run(work)
        print(f"root plan={first} replay_ok={first == second}")


if __name__ == "__main__":
    main()
