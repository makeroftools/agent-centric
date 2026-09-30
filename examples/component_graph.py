"""End-to-end demo of composite component resolution (SPEC-0007 Phase 1b).

Offline, no network, no server. It shows what a **composite** adds over the
Phase 1a atomic slice:

1. a composite (``bills_registry``) declares local **SQLite state** and two
   children — one **contained** (embedded ``bills_rules``, covered by the
   parent's ``tree_sha256``) and one **pointed-to** (referenced ``agenda``, its
   own ``components.lock`` entry);
2. both components are **pinned** from local git repositories;
3. the harness **resolves the graph deterministically, children first**, verifies
   each pin (hash + signature), and confirms the embedded child is a real
   ``component.v1`` inside the parent bundle;
4. the composite's SQLite state is **materialized** (WAL, integrity-checked,
   atomic);
5. the allowlisted entry runs, and running twice is identical.

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
from agent_centric.cbp.component_graph import ComponentGraphResolver
from agent_centric.cbp.component_runtime import AllowlistedEntryResolver
from agent_centric.cbp.component_source import GitSource, pin_from_git
from agent_centric.cbp.component_state import materialize_state
from agent_centric.cbp.resolver import Resolver
from agent_centric.cbp.signing import SignatureError
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry

_HERE = Path(__file__).resolve().parent
_COMPONENTS = _HERE / "components"
_ENTRY = "agent_centric.cbp.bills_component:registry_snapshot"

_REGISTRY = {
    "bills": [
        {"id": "b1", "vendor": "ACME Power", "amount_cents": 4200, "due_date": "2026-10-05"},
        {"id": "b2", "vendor": "City Water", "amount_cents": 1875, "due_date": "2026-10-12"},
    ]
}


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
    with tempfile.TemporaryDirectory(prefix="component-graph-") as tmp:
        root = Path(tmp)
        br_repo = _init_repo(_COMPONENTS / "bills_registry", root / "bills_registry-repo")
        ag_repo = _init_repo(_COMPONENTS / "agenda", root / "agenda-repo")
        br = pin_from_git(br_repo)
        ag = pin_from_git(ag_repo)
        print(
            f"pin bills_registry: commit={br.commit_sha[:12]} tree={br.tree_sha256[:12]}\n"
            f"pin agenda:         commit={ag.commit_sha[:12]} tree={ag.tree_sha256[:12]}"
        )

        entries = (
            LockEntry(
                name="bills_registry",
                repo_url="ssh://git@localhost/bills_registry.git",
                commit_sha=br.commit_sha,
                tree_sha256=br.tree_sha256,
                implements=("component.v1",),
                signature="demo",
            ),
            LockEntry(
                name="agenda",
                repo_url="ssh://git@localhost/agenda.git",
                commit_sha=ag.commit_sha,
                tree_sha256=ag.tree_sha256,
                implements=("component.v1",),
                signature="demo",
            ),
        )
        lock = ComponentsLock(
            root="bills_registry", entries=entries, harness_contracts=("component.v1",)
        )
        print(f"lock_hash={lock.lock_hash()[:12]}")

        cache = ContentAddressedCache(root / "cache")
        resolver = Resolver(
            lock,
            cache,
            source=GitSource({"bills_registry": br_repo, "agenda": ag_repo}),
            verifier=_DemoVerifier(),
        )
        graph = ComponentGraphResolver(lock, resolver, cache)
        nodes = graph.resolve_graph("bills_registry")
        print(f"resolved order (children first): {[n.name for n in nodes]}")

        composite = nodes[-1]
        for child in composite.children:
            if child.mode == "embed":
                assert child.manifest is not None
                print(f"contains  (embed): {child.target} -> component {child.manifest.name!r}")
            else:
                print(f"points-to (ref):   {child.target} -> lock entry {child.entry.name!r}")

        state = materialize_state(composite.manifest.state, root / "state-root")
        print(f"state materialized: {state.relative_to(root)}")

        entry = AllowlistedEntryResolver(frozenset({_ENTRY})).resolve(composite.manifest.entry)
        payload = {"registry": _REGISTRY}
        first = entry(payload)
        second = entry(payload)
        print(f"snapshot bills={len(first['bills'])} replay_ok={first == second}")


if __name__ == "__main__":
    main()
