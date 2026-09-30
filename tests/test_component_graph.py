"""Tests for dependency-ordered component graph resolution (SPEC-0007 Phase 1b).

Most cases use in-memory bundles (no git) so the graph semantics are exercised
directly; one end-to-end case pins the real example components from local git.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_centric.contracts.component import (
    ChildRef,
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
    StateDescriptor,
)
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry
from agent_centric.fbp.cache import ContentAddressedCache
from agent_centric.fbp.component_bundle import build_bundle, bundle_sha256, component_json
from agent_centric.fbp.component_graph import ComponentGraphResolver, GraphError
from agent_centric.fbp.component_state import materialize_state
from agent_centric.fbp.resolver import ResolveError, Resolver
from agent_centric.fbp.signing import SignatureError

_COMPONENTS = Path(__file__).resolve().parents[1] / "examples" / "components"


def _manifest(
    name: str,
    *,
    kind: str = ComponentKind.ATOMIC,
    entry: str = "pkg.mod:make",
    children: tuple[ChildRef, ...] = (),
    state: StateDescriptor | None = None,
) -> ComponentManifest:
    return ComponentManifest(
        version=ComponentVersion.V1,
        name=name,
        kind=kind,
        entry=EntryDescriptor(runtime="python", entrypoint=entry),
        implements=("component.v1",),
        children=children,
        state=state,
    )


class _Acceptor:
    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != b"good":
            raise SignatureError("bad signature")


class _MemorySource:
    def __init__(self, bundles: dict[str, bytes]) -> None:
        self._bundles = dict(bundles)

    def fetch(self, entry: LockEntry) -> bytes:
        return self._bundles[entry.name]


def _graph(
    tmp_path: Path,
    root: str,
    manifests: dict[str, ComponentManifest],
    payloads: dict[str, dict[str, bytes]] | None = None,
) -> ComponentGraphResolver:
    payloads = payloads or {}
    bundles = {
        name: build_bundle(manifest, payloads.get(name)) for name, manifest in manifests.items()
    }
    entries = tuple(
        LockEntry(
            name=name,
            repo_url=f"ssh://example/{name}.git",
            commit_sha="a" * 40,
            tree_sha256=bundle_sha256(bundle),
            implements=("component.v1",),
            signature="good",
        )
        for name, bundle in sorted(bundles.items())
    )
    lock = ComponentsLock(
        root=root, entries=entries, harness_contracts=("component.v1",)
    )
    cache = ContentAddressedCache(tmp_path / "cache")
    resolver = Resolver(lock, cache, source=_MemorySource(bundles), verifier=_Acceptor())
    return ComponentGraphResolver(lock, resolver, cache)


def _embed_payload(child: ComponentManifest, *, path: str = "children/rules") -> dict[str, bytes]:
    return {f"{path}/component.json": component_json(child)}


class TestEmbedAndRef:
    def test_resolves_children_before_parent(self, tmp_path: Path) -> None:
        rules = _manifest("rules")
        parent = _manifest(
            "suite",
            kind=ComponentKind.COMPOSITE,
            children=(ChildRef(embed="children/rules"), ChildRef(ref="agenda")),
        )
        agenda = _manifest("agenda")
        graph = _graph(
            tmp_path,
            "suite",
            {"suite": parent, "agenda": agenda},
            {"suite": _embed_payload(rules)},
        )
        nodes = graph.resolve_graph("suite")
        assert [n.name for n in nodes] == ["agenda", "suite"]
        root = nodes[-1]
        modes = [(c.mode, c.target) for c in root.children]
        assert modes == [("embed", "children/rules"), ("ref", "agenda")]
        embedded = next(c for c in root.children if c.mode == "embed")
        assert embedded.manifest is not None and embedded.manifest.name == "rules"
        referenced = next(c for c in root.children if c.mode == "ref")
        assert referenced.entry is not None and referenced.entry.name == "agenda"

    def test_embedded_child_is_content_hashed(self) -> None:
        rules = _manifest("rules")
        parent = _manifest("suite", kind=ComponentKind.COMPOSITE)
        first = build_bundle(parent, _embed_payload(rules))
        tampered = build_bundle(
            parent, {"children/rules/component.json": component_json(rules) + b"\n"}
        )
        assert bundle_sha256(first) != bundle_sha256(tampered)

    def test_absent_embed_fails_closed(self, tmp_path: Path) -> None:
        parent = _manifest(
            "suite",
            kind=ComponentKind.COMPOSITE,
            children=(ChildRef(embed="children/missing"),),
        )
        graph = _graph(tmp_path, "suite", {"suite": parent})
        with pytest.raises(GraphError):
            graph.resolve_graph("suite")

    def test_embed_without_manifest_fails_closed(self, tmp_path: Path) -> None:
        parent = _manifest(
            "suite",
            kind=ComponentKind.COMPOSITE,
            children=(ChildRef(embed="children/rules"),),
        )
        graph = _graph(
            tmp_path,
            "suite",
            {"suite": parent},
            {"suite": {"children/rules/data.txt": b"just data"}},
        )
        with pytest.raises(GraphError):
            graph.resolve_graph("suite")

    def test_nested_composite_embed_fails_closed(self, tmp_path: Path) -> None:
        nested = _manifest(
            "nested",
            kind=ComponentKind.COMPOSITE,
            children=(ChildRef(ref="other"),),
        )
        parent = _manifest(
            "suite",
            kind=ComponentKind.COMPOSITE,
            children=(ChildRef(embed="children/nested"),),
        )
        graph = _graph(
            tmp_path,
            "suite",
            {"suite": parent},
            {"suite": _embed_payload(nested, path="children/nested")},
        )
        with pytest.raises(GraphError):
            graph.resolve_graph("suite")

    def test_ref_not_in_lock_fails_closed(self, tmp_path: Path) -> None:
        parent = _manifest(
            "suite", kind=ComponentKind.COMPOSITE, children=(ChildRef(ref="ghost"),)
        )
        graph = _graph(tmp_path, "suite", {"suite": parent})
        with pytest.raises(GraphError):
            graph.resolve_graph("suite")

    def test_ref_hash_mismatch_fails_closed(self, tmp_path: Path) -> None:
        parent = _manifest(
            "suite", kind=ComponentKind.COMPOSITE, children=(ChildRef(ref="agenda"),)
        )
        agenda = _manifest("agenda")
        bundles = {"suite": build_bundle(parent), "agenda": build_bundle(agenda)}
        entries = (
            LockEntry(
                name="suite",
                repo_url="ssh://example/suite.git",
                commit_sha="a" * 40,
                tree_sha256=bundle_sha256(bundles["suite"]),
                implements=("component.v1",),
                signature="good",
            ),
            LockEntry(
                name="agenda",
                repo_url="ssh://example/agenda.git",
                commit_sha="a" * 40,
                tree_sha256="0" * 64,
                implements=("component.v1",),
                signature="good",
            ),
        )
        lock = ComponentsLock(root="suite", entries=entries, harness_contracts=("component.v1",))
        cache = ContentAddressedCache(tmp_path / "cache")
        resolver = Resolver(
            lock, cache, source=_MemorySource(bundles), verifier=_Acceptor()
        )
        graph = ComponentGraphResolver(lock, resolver, cache)
        with pytest.raises(ResolveError):
            graph.resolve_graph("suite")

    def test_cycle_fails_closed(self, tmp_path: Path) -> None:
        a = _manifest("a", kind=ComponentKind.COMPOSITE, children=(ChildRef(ref="b"),))
        b = _manifest("b", kind=ComponentKind.COMPOSITE, children=(ChildRef(ref="a"),))
        graph = _graph(tmp_path, "a", {"a": a, "b": b})
        with pytest.raises(GraphError):
            graph.resolve_graph("a")

    def test_shallow_and_deterministic(self, tmp_path: Path) -> None:
        rules = _manifest("rules")
        parent = _manifest(
            "suite",
            kind=ComponentKind.COMPOSITE,
            children=(ChildRef(embed="children/rules"), ChildRef(ref="agenda")),
        )
        graph = _graph(
            tmp_path,
            "suite",
            {"suite": parent, "agenda": _manifest("agenda")},
            {"suite": _embed_payload(rules)},
        )
        first = [n.name for n in graph.resolve_graph("suite")]
        second = [n.name for n in graph.resolve_graph("suite")]
        assert first == second == ["agenda", "suite"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestExampleComponents:
    def _repo(self, src: Path, dest: Path) -> Path:
        shutil.copytree(src, dest)
        flags = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), *flags, "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(dest), *flags, "commit", "-qm", "x"], check=True)
        return dest

    def test_example_graph_end_to_end(self, tmp_path: Path) -> None:
        from agent_centric.fbp.bills_component import registry_snapshot
        from agent_centric.fbp.component_runtime import AllowlistedEntryResolver
        from agent_centric.fbp.component_source import GitSource, pin_from_git

        br_repo = self._repo(_COMPONENTS / "bills_registry", tmp_path / "br")
        ag_repo = self._repo(_COMPONENTS / "agenda", tmp_path / "ag")
        br = pin_from_git(br_repo)
        ag = pin_from_git(ag_repo)
        entries = (
            LockEntry(
                name="bills_registry",
                repo_url="ssh://example/bills_registry.git",
                commit_sha=br.commit_sha,
                tree_sha256=br.tree_sha256,
                implements=("component.v1",),
                signature="good",
            ),
            LockEntry(
                name="agenda",
                repo_url="ssh://example/agenda.git",
                commit_sha=ag.commit_sha,
                tree_sha256=ag.tree_sha256,
                implements=("component.v1",),
                signature="good",
            ),
        )
        lock = ComponentsLock(
            root="bills_registry", entries=entries, harness_contracts=("component.v1",)
        )
        cache = ContentAddressedCache(tmp_path / "cache")
        resolver = Resolver(
            lock,
            cache,
            source=GitSource({"bills_registry": br_repo, "agenda": ag_repo}),
            verifier=_Acceptor(),
        )
        graph = ComponentGraphResolver(lock, resolver, cache)
        nodes = graph.resolve_graph("bills_registry")
        assert [n.name for n in nodes] == ["agenda", "bills_registry"]

        root = nodes[-1]
        assert root.manifest.kind == ComponentKind.COMPOSITE
        assert root.manifest.state == StateDescriptor(
            kind="sqlite", path="state/bills.db", single_writer=True
        )
        embedded = next(c for c in root.children if c.mode == "embed")
        assert embedded.manifest is not None and embedded.manifest.name == "bills_rules"
        assert next(c for c in root.children if c.mode == "ref").target == "agenda"

        state = materialize_state(root.manifest.state, tmp_path / "state-root")
        assert state.is_file()

        entry = AllowlistedEntryResolver(
            frozenset({"agent_centric.fbp.bills_component:registry_snapshot"})
        ).resolve(root.manifest.entry)
        assert entry is registry_snapshot
        payload = {
            "registry": {
                "bills": [
                    {
                        "id": "b1",
                        "vendor": "ACME",
                        "amount_cents": 1200,
                        "due_date": "2026-10-01",
                    }
                ]
            }
        }
        assert entry(payload) == entry(json.loads(json.dumps(payload)))
