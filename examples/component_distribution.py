"""End-to-end demo of component distribution (SPEC-0007 Phase 1a, offline).

This shows the full vertical slice with **no network and no server**:

1. a component lives in a local git repository (``examples/components/counter``);
2. it is **pinned** to an immutable ``commit_sha`` + ``tree_sha256``;
3. the pin is written into a ``components.lock``;
4. the harness **resolves** it from a local git source (``git archive`` at the
   pinned commit, canonicalized into a deterministic bundle);
5. it **verifies** the content hash and the detached signature;
6. it **loads** the manifest and the allowlisted entry, and **runs** it;
7. resolution is **deterministic**: pinning/resolving twice yields identical
   hashes, and running twice yields an identical result.

Signature note: this demo uses a clearly-labelled ``_DemoVerifier`` so the slice
runs without a signing key. Production verification uses ``minisign``/``gpg``
via :class:`agent_centric.cbp.signing.SystemVerifier` — never the demo verifier.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent_centric.agents.counter import create_counter_agent
from agent_centric.agents.interface import ToolContext
from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_bundle import load_manifest
from agent_centric.cbp.component_runtime import AllowlistedEntryResolver
from agent_centric.cbp.component_source import GitSource, pin_from_git
from agent_centric.cbp.resolver import Resolver
from agent_centric.cbp.signing import SignatureError
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry

_HERE = Path(__file__).resolve().parent
_COMPONENT = _HERE / "components" / "counter"
_ENTRY = "agent_centric.agents.counter:create_counter_agent"


class _DemoVerifier:
    """DEMO ONLY: accepts the fixed signature ``demo``. Never use in production."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"demo":
            raise SignatureError("demo verifier rejected the signature")


def _init_repo(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("component.json", "counter.py", "README.md"):
        shutil.copy(_COMPONENT / name, dest / name)
    run = ["git", "-C", str(dest), "-c", "user.email=demo@example.com", "-c", "user.name=demo"]
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run([*run, "add", "-A"], check=True)
    subprocess.run([*run, "commit", "-qm", "counter"], check=True)
    return dest


def _run_counter(payload: dict[str, str]) -> object:
    generator = create_counter_agent()(payload, 100, ToolContext())
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        return stop.value.output
    raise AssertionError("unreachable")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="component-dist-") as tmp:
        root = Path(tmp)
        repo = _init_repo(root / "counter-repo")

        pinned = pin_from_git(repo)
        print(f"pin: commit={pinned.commit_sha[:12]} tree_sha256={pinned.tree_sha256[:12]}")

        entry = LockEntry(
            name="counter",
            repo_url="ssh://git@localhost/counter.git",
            commit_sha=pinned.commit_sha,
            tree_sha256=pinned.tree_sha256,
            implements=("component.v1",),
            signature="demo",
        )
        lock = ComponentsLock(
            root="counter", entries=(entry,), harness_contracts=("component.v1",)
        )
        print(f"lock_hash={lock.lock_hash()[:12]}")

        cache = ContentAddressedCache(root / "cache")
        resolver = Resolver(
            lock, cache, source=GitSource({"counter": repo}), verifier=_DemoVerifier()
        )
        first = resolver.resolve("counter")
        second = resolver.resolve("counter")
        print(f"resolved digest={first.digest[:12]} deterministic={first.digest == second.digest}")

        manifest = load_manifest(cache.get(first.digest) or b"")
        callable_ = AllowlistedEntryResolver(frozenset({_ENTRY})).resolve(manifest.entry)
        print(f"loaded manifest: {json.dumps({'name': manifest.name, 'kind': manifest.kind})}")

        payload = {"text": "banana", "target": "a"}
        run1 = _run_counter(payload)
        run2 = _run_counter(payload)
        print(f"entry={callable_!r} count('banana','a')={run1} replay_ok={run1 == run2}")


if __name__ == "__main__":
    main()
