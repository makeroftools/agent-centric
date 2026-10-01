"""Design → pin → ``components.lock`` producer (SPEC-0008 Phase 3, SPEC-0007 §4).

A ``design.v1`` is untrusted, declarative input: a ``network.v1`` graph plus a
list of **requested** component references (``name`` + a ref that may be a tag,
branch, or commit). This module turns that design into a pinned, deterministic
``components.lock/v1``: every requested ref is resolved to an immutable commit
and a content-addressed bundle **at pin time** — the SPEC-0007 tag rule, so a
design never floats at run time.

It composes the existing primitives:

- :func:`~agent_centric.cbp.design.validate_design` (fail-closed design gate);
- :func:`~agent_centric.cbp.component_source.pin_from_git` (offline resolve →
  bundle → ``tree_sha256``);
- :func:`~agent_centric.cbp.component_bundle.load_manifest` (the pinned
  ``component.v1`` ground truth for ``implements`` / ``capabilities`` /
  provenance).

Invariants (fail-closed, deterministic, offline):

- an invalid design is refused before any ref is resolved;
- every requested component must have a configured source and pin to a manifest
  whose **name matches** the request (identity is not the ref);
- a component that ``implements`` an unpinned contract is refused;
- a component without a signature is refused (a design cannot introduce unsigned
  code, SPEC-0008 §4); the signature is verified later, at resolve time;
- a component's validated per-component envelope (SPEC-0008 §4) is pinned into
  its lock entry, so the declared hard resource bounds are immutable and signed;
- entries are emitted in deterministic (name-sorted) order, so pinning the same
  design twice yields an identical ``lock_hash``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..contracts.components_lock import ComponentsLock, LockEntry
from ..contracts.design import Design
from .component_bundle import load_manifest
from .component_source import GitError, pin_from_git
from .design import validate_design

DEFAULT_HARNESS_CONTRACTS = ("component.v1",)


class PinError(Exception):
    """A design could not be pinned into a lock (fail-closed)."""


@dataclass(frozen=True)
class ComponentPin:
    """An offline, signed source for one requested component.

    Attributes:
        repo: The local git repository the component is pinned from (offline).
        repo_url: The canonical repository URL recorded in the lock entry.
        signature: A detached signature over the artifact (recorded; verified at
            resolve time). Empty is refused — a design cannot introduce unsigned
            code.
    """

    repo: Path
    repo_url: str
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.repo_url:
            raise PinError("ComponentPin repo_url must be non-empty.")


def pin_design(
    design: Design,
    sources: Mapping[str, ComponentPin],
    *,
    root: str,
    harness_contracts: tuple[str, ...] = DEFAULT_HARNESS_CONTRACTS,
) -> ComponentsLock:
    """Pin a design's requested components into a ``components.lock/v1``.

    Args:
        design: The untrusted ``design.v1`` to pin.
        sources: A mapping of component name to its offline, signed
            :class:`ComponentPin`.
        root: The shell component name; it must be one of the requested
            components and is the lock's root.
        harness_contracts: The contract versions the harness supports; a
            component that implements anything else is refused.

    Returns:
        A deterministic, content-addressed :class:`ComponentsLock`.

    Raises:
        PinError: If the design is invalid, a ref cannot be resolved, a
            component is unnamed/unsigned/unknown, or the root is absent.
    """
    report = validate_design(design)
    if not report.ok:
        raise PinError("invalid design: " + "; ".join(report.errors))
    if not design.components:
        raise PinError("design requests no components")

    requested = tuple(sorted(design.components, key=lambda c: c.name))
    # Envelope limits were validated above; pin them alongside the component so
    # the design's declared hard bounds become part of the signed lock.
    envelopes_by_component = {
        grant.component: dict(grant.bounds) for grant in design.envelopes
    }
    names = [component.name for component in requested]
    if root not in set(names):
        raise PinError(f"lock root {root!r} is not among the requested components")
    supported = frozenset(harness_contracts)

    entries: list[LockEntry] = []
    for component in requested:
        name = component.name
        source = sources.get(name)
        if source is None:
            raise PinError(f"no source configured for requested component {name!r}")
        repo = Path(source.repo)
        if not repo.is_dir():
            raise PinError(f"{name}: source repository {repo} does not exist")
        try:
            pinned = pin_from_git(repo, component.requested)
        except GitError as exc:
            raise PinError(
                f"{name}: cannot resolve {component.requested!r}: {exc}"
            ) from exc

        manifest = load_manifest(pinned.bundle)
        if manifest.name != name:
            raise PinError(
                f"{name}: pinned manifest is named {manifest.name!r} (identity mismatch)"
            )
        for contract in manifest.implements:
            if contract not in supported:
                raise PinError(f"{name}: implements unpinned contract {contract!r}")

        provenance = manifest.provenance
        if provenance is not None and (
            provenance.commit_sha != pinned.commit_sha
            or provenance.tree_sha256 != pinned.tree_sha256
        ):
            raise PinError(
                f"{name}: manifest provenance does not match the pinned commit/tree"
            )
        signature = source.signature or (provenance.signature if provenance else "")
        if not signature:
            raise PinError(
                f"{name}: unsigned component; a design cannot introduce unsigned code"
            )

        entries.append(
            LockEntry(
                name=name,
                repo_url=source.repo_url,
                commit_sha=pinned.commit_sha,
                tree_sha256=pinned.tree_sha256,
                implements=manifest.implements,
                capabilities=tuple(capability.name for capability in manifest.capabilities),
                signature=signature,
                envelope=envelopes_by_component.get(name, {}),
            )
        )

    return ComponentsLock(
        root=root,
        entries=tuple(entries),
        harness_contracts=tuple(harness_contracts),
    )
