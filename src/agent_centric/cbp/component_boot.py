"""Boot a CBP tree from a verified components lock (SPEC-0007 §4, Phase 2).

"Boot" is the deterministic, fail-closed bridge from a pinned
``components.lock`` to a runnable tree:

1. the lock's content address is checked (``lock_hash``) — an optional expected
   hash turns lock drift into an explicit failure;
2. the whole component graph is resolved (children first, deterministic) and
   every pin is verified (hash + signature + declared harness contracts);
3. each verified manifest is cross-checked against its lock entry and the
   harness contracts — a manifest may never claim a contract the lock did not
   pin, and a component's identity may not disagree with its lock entry;
4. each component's declared local SQLite state is materialized, **namespaced by
   component name** so two components can never share a state file;
5. every execution entry is resolved strictly from the allowlist.

Nothing here touches the network. Any absence, mismatch, unknown contract,
unsafe path, or un-allowlisted entry is an explicit :class:`BootError` — never a
partial success.

Scope note (honest): Phase 2 executes only entries already installed in the
harness (the Phase 1a allowlist). A component whose code ships only inside its
bundle — e.g. an embedded child — is resolved and verified *structurally*, but
its entry is still refused until the subprocess-isolation backend exists. This
is deliberately conservative: nothing runs unproven.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts.component import ComponentManifest
from ..contracts.components_lock import ComponentsLock
from .cache import ContentAddressedCache
from .component_graph import ComponentGraphResolver, ResolvedNode
from .component_runtime import AllowlistedEntryResolver, EntryNotAllowed
from .component_state import materialize_state
from .resolver import ComponentSource, ResolveError, Resolver
from .signing import SignatureVerifier


class BootError(ResolveError):
    """A tree could not be booted from the lock (fail-closed)."""


@dataclass(frozen=True)
class BootedComponent:
    """One verified, instantiated component of a booted tree."""

    name: str
    digest: str
    manifest: ComponentManifest
    entry: Callable[..., Any]
    state_path: Path | None

    @property
    def kind(self) -> str:
        """The component kind (``atomic`` / ``composite`` / ``model``)."""
        return self.manifest.kind


@dataclass(frozen=True)
class BootedTree:
    """A booted tree: the verified component set plus the executable root."""

    lock_hash: str
    root: str
    components: tuple[BootedComponent, ...]

    @property
    def by_name(self) -> dict[str, BootedComponent]:
        """The booted components keyed by name."""
        return {component.name: component for component in self.components}

    @property
    def root_component(self) -> BootedComponent:
        """The root (shell) component."""
        for component in self.components:
            if component.name == self.root:
                return component
        raise BootError(f"booted tree has no root component {self.root!r}")

    def run(self, work: Any) -> Any:
        """Invoke the root (shell) component's allowlisted entry."""
        return self.root_component.entry(work)


def boot_from_lock(
    lock: ComponentsLock,
    *,
    source: ComponentSource,
    cache: ContentAddressedCache,
    verifier: SignatureVerifier | None,
    entry_allowlist: frozenset[str],
    state_root: str | Path,
    expected_lock_hash: str | None = None,
) -> BootedTree:
    """Resolve, verify, and instantiate the tree pinned by ``lock`` (fail-closed).

    Args:
        lock: The pinned component set (its ``root`` is the shell component).
        source: A local, offline source of component artifacts.
        cache: The content-addressed cache the resolver verifies into.
        verifier: The signature verifier; ``None`` refuses (fail-closed).
        entry_allowlist: The execution entries the harness permits.
        state_root: The root under which per-component SQLite state is created.
        expected_lock_hash: When given, the lock's hash must equal it exactly.

    Returns:
        A :class:`BootedTree` with components ordered children-first and the
        root's entry resolved.

    Raises:
        BootError: On lock drift, a graph/verification failure, an identity or
            contract mismatch, or an un-allowlisted entry.
        StateError: On an unsafe or corrupt component state path.
    """
    lock_hash = lock.lock_hash()
    if expected_lock_hash is not None and lock_hash != expected_lock_hash:
        raise BootError(
            f"lock hash mismatch: expected {expected_lock_hash}, got {lock_hash}"
        )

    resolver = Resolver(lock, cache, source=source, verifier=verifier)
    graph = ComponentGraphResolver(lock, resolver, cache)
    nodes = graph.resolve_graph(lock.root)
    if not nodes:
        raise BootError("resolved component graph is empty")
    if nodes[-1].name != lock.root:
        raise BootError(
            f"resolved graph does not end at the lock root {lock.root!r}"
        )

    supported = frozenset(lock.harness_contracts)
    entry_resolver = AllowlistedEntryResolver(entry_allowlist)
    components: list[BootedComponent] = []
    for node in nodes:
        _check_conformance(node, supported)
        manifest = node.manifest
        state_file = (
            materialize_state(manifest.state, Path(state_root) / node.name)
            if manifest.state is not None
            else None
        )
        components.append(
            BootedComponent(
                name=node.name,
                digest=node.digest,
                manifest=manifest,
                entry=_resolve_entry(entry_resolver, node),
                state_path=state_file,
            )
        )
    return BootedTree(lock_hash=lock_hash, root=lock.root, components=tuple(components))


def _check_conformance(node: ResolvedNode, supported: frozenset[str]) -> None:
    """Cross-check a verified manifest against its lock entry (fail-closed)."""
    manifest = node.manifest
    if manifest.name != node.name:
        raise BootError(
            f"{node.name}: manifest names {manifest.name!r} "
            "(component identity mismatch)"
        )
    declared = set(manifest.implements)
    pinned = set(node.entry.implements)
    if not declared <= pinned:
        raise BootError(
            f"{node.name}: manifest implements {sorted(declared - pinned)} "
            "not pinned by the lock entry"
        )
    unknown = sorted(pinned - supported)
    if unknown:
        raise BootError(
            f"{node.name}: lock entry implements unknown contract(s) {unknown}"
        )


def _resolve_entry(
    resolver: AllowlistedEntryResolver, node: ResolvedNode
) -> Callable[..., Any]:
    """Resolve a component's entry from the allowlist (fail-closed)."""
    try:
        return resolver.resolve(node.manifest.entry)
    except EntryNotAllowed as exc:
        raise BootError(f"{node.name}: entry not executable: {exc}") from exc
