"""End-to-end demo of design → pin → ``components.lock`` (SPEC-0008 Phase 3).

Offline, no network, no server. It shows the Phase 3 wiring from a human's
design to a booted tree:

1. a ``design.v1`` requests the example ``shell`` (root) and ``registry``
   components (a network plus requested refs);
2. ``pin_design`` resolves each ref to an immutable commit + content-addressed
   bundle and builds the ``components.lock`` — refs never float at run time;
3. the lock's ``lock_hash`` is signed and recorded in the append-only
   transparency log (SPEC-0007 §6);
4. the harness boots the tree from the pinned lock (verify lock signature, the
   log head, and every component pin), and running it twice is identical.

Signature note: this demo uses clearly-labelled ``_Demo`` signer/verifiers so the
slice runs without key material. Production uses a system signer/verifier
(``minisign``/``gpg``) — never the demo double.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import boot_from_lock
from agent_centric.cbp.component_source import GitSource
from agent_centric.cbp.lockfile import ComponentPin, pin_design
from agent_centric.cbp.signing import SignatureError
from agent_centric.cbp.signing_service import record_release
from agent_centric.cbp.transparency import TransparencyLog
from agent_centric.contracts.design import Design, RequestedComponent

_HERE = Path(__file__).resolve().parent
_COMPONENTS = _HERE / "components"
_ENTRIES = frozenset(
    {
        "agent_centric.cbp.shell_component:plan_delegation",
        "agent_centric.cbp.registry_component:catalog_snapshot",
    }
)
_NETWORK = {
    "components": [
        {"id": "shell", "task": "plan", "args": {}},
        {"id": "registry", "task": "catalog", "args": {}},
    ],
    "edges": [],
}


class _DemoVerifier:
    """DEMO ONLY: accepts the fixed component signature ``demo``."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"demo":
            raise SignatureError("demo verifier rejected the component signature")


class _DemoSigner:
    """DEMO ONLY: deterministic lock signer (never use in production)."""

    @property
    def key_id(self) -> str:
        return "demo-key"

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(b"demo:" + payload).hexdigest().encode()


class _DemoLockVerifier:
    """DEMO ONLY: verifier matching :class:`_DemoSigner`."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        expected = hashlib.sha256(b"demo:" + payload).hexdigest().encode()
        if signature != expected:
            raise SignatureError("demo verifier rejected the lock signature")


def _init_repo(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    flags = ["-c", "user.email=demo@example.com", "-c", "user.name=demo"]
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), *flags, "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dest), *flags, "commit", "-qm", "component"], check=True)
    return dest


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="design-pin-") as tmp:
        root = Path(tmp)
        sources = {
            "shell": ComponentPin(
                repo=_init_repo(_COMPONENTS / "shell", root / "shell-repo"),
                repo_url="ssh://git@localhost/shell.git",
                signature="demo",
            ),
            "registry": ComponentPin(
                repo=_init_repo(_COMPONENTS / "registry", root / "registry-repo"),
                repo_url="ssh://git@localhost/registry.git",
                signature="demo",
            ),
        }
        design = Design(
            id="demo",
            title="shell -> registry",
            network=_NETWORK,
            components=(
                RequestedComponent("shell", "HEAD"),
                RequestedComponent("registry", "HEAD"),
            ),
        )
        lock = pin_design(design, sources, root="shell")
        print(
            f"design_hash={design.design_hash()[:12]} "
            f"lock_hash={lock.lock_hash()[:12]} root={lock.root}"
        )
        print(f"entries={[entry.name for entry in lock.entries]}")

        log = TransparencyLog(root / "log.jsonl")
        signed, _entry = record_release(lock, _DemoSigner(), log)
        print(f"recorded key={signed.key_id} head={log.head()[:12]}")

        tree = boot_from_lock(
            lock,
            source=GitSource({name: pin.repo for name, pin in sources.items()}),
            cache=ContentAddressedCache(root / "cache"),
            verifier=_DemoVerifier(),
            entry_allowlist=_ENTRIES,
            state_root=root / "state",
            expected_lock_hash=lock.lock_hash(),
            lock_signature=signed.signature,
            lock_verifier=_DemoLockVerifier(),
            transparency=log,
            expected_transparency_head=log.head(),
        )
        print(f"booted order (children first): {[c.name for c in tree.components]}")
        work = {"mission": "collect", "components": ["registry"]}
        first = tree.run(work)
        second = tree.run(work)
        print(f"root plan={first} replay_ok={first == second}")


if __name__ == "__main__":
    main()
