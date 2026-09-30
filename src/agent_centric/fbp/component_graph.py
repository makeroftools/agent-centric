"""Dependency-ordered component graph resolution (SPEC-0007 §4, Phase 1b).

The flat :class:`agent_centric.fbp.resolver.Resolver` verifies one lock entry in
isolation. A **composite** adds structure: it may *contain* embedded children
(covered by the parent's ``tree_sha256``) or *point to* referenced children
(each its own pinned ``components.lock`` entry). This module resolves that graph
**deterministically and fail-closed**:

1. a referenced child must exist in the lock and resolve exactly like any other
   entry (hash + signature + declared contracts);
2. an embedded child must be present inside the verified parent bundle and must
   itself be a real ``component.v1`` (not a bare data folder);
3. resolution order is deterministic — children before parents
   (post-order), children sorted by target — and a **cycle fails closed**;
4. any absence, mismatch, or unsupported nesting is an explicit
   :class:`GraphError`, never a partial success.

Nested composites (an embedded child that itself declares children, or an
embedded composite) are **not** supported in Phase 1b and fail closed; this is
deliberately conservative (no unproven claim), and real nesting lands with the
shell component in Phase 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..contracts.component import ChildRef, ComponentKind, ComponentManifest
from ..contracts.components_lock import ComponentsLock, LockEntry, LockError
from .cache import ContentAddressedCache
from .component_bundle import (
    BUNDLE_MANIFEST,
    BundleError,
    list_members,
    load_manifest,
    read_member,
)
from .resolver import ResolveError, Resolver


class GraphError(ResolveError):
    """A component graph could not be resolved and verified (fail-closed)."""


@dataclass(frozen=True)
class ResolvedChild:
    """A resolved child of a composite.

    ``mode`` is ``"embed"`` (contained) or ``"ref"`` (referenced). For an
    embedded child, ``manifest`` is its own verified ``component.v1``; for a
    referenced child, ``entry`` is its lock entry (the child also appears once
    in the resolved graph).
    """

    mode: str
    target: str
    manifest: ComponentManifest | None = None
    entry: LockEntry | None = None


@dataclass(frozen=True)
class ResolvedNode:
    """One verified component in the resolved graph."""

    name: str
    entry: LockEntry
    digest: str
    manifest: ComponentManifest
    children: tuple[ResolvedChild, ...]


class ComponentGraphResolver:
    """Resolve a composite component and its children, children first."""

    def __init__(
        self, lock: ComponentsLock, resolver: Resolver, cache: ContentAddressedCache
    ) -> None:
        self._lock = lock
        self._resolver = resolver
        self._cache = cache
        self._nodes: dict[str, ResolvedNode] = {}

    def resolve_graph(self, root: str) -> tuple[ResolvedNode, ...]:
        """Resolve ``root`` and every referenced descendant, leaves first.

        The result is deterministic (same lock + bundles ⇒ same order and same
        content). Raises :class:`GraphError` on any failure.
        """
        self._nodes = {}
        visiting: set[str] = set()
        order: list[str] = []
        self._visit(root, visiting, order)
        return tuple(self._nodes[name] for name in order)

    def _visit(self, name: str, visiting: set[str], order: list[str]) -> None:
        if name in visiting:
            raise GraphError(f"component reference cycle detected at {name!r}")
        if name in self._nodes:
            return
        node = self._resolve_node(name)
        visiting.add(name)
        for child in node.children:
            if child.mode == "ref":
                self._visit(child.target, visiting, order)
        visiting.discard(name)
        self._nodes[name] = node
        order.append(name)

    def _resolve_node(self, name: str) -> ResolvedNode:
        try:
            entry = self._lock.entry(name)
        except LockError as exc:
            raise GraphError(f"referenced component {name!r} is not in the lock") from exc
        resolved = self._resolver.resolve(name)
        bundle = self._cache.get(resolved.digest)
        if bundle is None:
            raise GraphError(f"{name}: verified bundle missing from the cache")
        manifest = load_manifest(bundle)
        children = tuple(self._resolve_children(name, bundle, manifest))
        return ResolvedNode(
            name=name,
            entry=entry,
            digest=resolved.digest,
            manifest=manifest,
            children=children,
        )

    def _resolve_children(
        self, name: str, bundle: bytes, manifest: ComponentManifest
    ) -> list[ResolvedChild]:
        resolved: list[ResolvedChild] = []
        for child in sorted(manifest.children, key=lambda c: (c.mode, c.target)):
            if child.mode == "ref":
                try:
                    entry = self._lock.entry(child.target)
                except LockError as exc:
                    raise GraphError(
                        f"{name}: referenced child {child.target!r} is not in the lock"
                    ) from exc
                resolved.append(ResolvedChild(mode="ref", target=child.target, entry=entry))
            else:
                resolved.append(
                    ResolvedChild(
                        mode="embed",
                        target=child.target,
                        manifest=self._embedded_manifest(name, bundle, child),
                    )
                )
        return resolved

    @staticmethod
    def _embedded_manifest(
        parent: str, bundle: bytes, child: ChildRef
    ) -> ComponentManifest:
        target = child.target.rstrip("/")
        prefix = f"{target}/"
        members = list_members(bundle)
        if not any(member.startswith(prefix) for member in members):
            raise GraphError(
                f"{parent}: embedded child {target!r} is absent from the bundle"
            )
        try:
            raw = read_member(bundle, prefix + BUNDLE_MANIFEST)
        except BundleError as exc:
            raise GraphError(
                f"{parent}: embedded child {target!r} has no {BUNDLE_MANIFEST}"
            ) from exc
        try:
            embedded = ComponentManifest.from_dict(json.loads(raw))
        except (KeyError, TypeError, ValueError) as exc:
            raise GraphError(
                f"{parent}: embedded child {target!r} is not a valid component.v1: {exc}"
            ) from exc
        if embedded.children or embedded.kind == ComponentKind.COMPOSITE:
            raise GraphError(
                f"{parent}: embedded child {target!r} is a nested composite; "
                "not supported in Phase 1b"
            )
        return embedded
