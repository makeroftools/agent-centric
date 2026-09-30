"""Component contract (versioned) — ``component.v1``.

A **component** is the single unit of the CBP tree: an FBP node and an ABM
agent, self-contained, with local (SQLite) state, executed by the harness
(see ``specs/SPEC-0007-harness-shell-component-distribution.md``). This module
is the immutable, versioned declaration of a component: its identity, kind,
the harness contracts it implements, its ports, capabilities/grants/envelope, a
state descriptor, an execution entry descriptor, its children (embedded or
referenced), an optional agent-facing directive, and provenance.

Contracts remain **additive-only**. This is the ``component.v1`` shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .capability import Capability

_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ComponentVersion:
    """The supported component contract version."""

    V1 = "component.v1"

    __slots__ = ()


class ComponentKind:
    """The kind of a component."""

    ATOMIC = "atomic"
    COMPOSITE = "composite"
    MODEL = "model"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.ATOMIC, cls.COMPOSITE, cls.MODEL)


class RuntimeKind:
    """How a component's entry is executed."""

    PYTHON = "python"
    PROCESS = "process"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.PYTHON, cls.PROCESS)


@dataclass(frozen=True)
class StateDescriptor:
    """A component's local, self-contained state.

    Attributes:
        kind: The state backend (only ``"sqlite"`` today).
        path: The state file path, relative to the component.
        single_writer: Must be ``True`` (single-writer is an invariant).
    """

    kind: str
    path: str
    single_writer: bool = True

    def __post_init__(self) -> None:
        if self.kind != "sqlite":
            raise ValueError(f"Unsupported state kind: {self.kind!r}")
        if not self.path:
            raise ValueError("State path must be non-empty.")
        if not self.single_writer:
            raise ValueError("Component state must be single-writer.")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "single_writer": self.single_writer}


@dataclass(frozen=True)
class EntryDescriptor:
    """How the harness resolves and invokes a component.

    Attributes:
        runtime: The runtime kind (``python`` or ``process``).
        entrypoint: ``module:qualname`` (python) or an executable path (process).
    """

    runtime: str
    entrypoint: str

    def __post_init__(self) -> None:
        if self.runtime not in RuntimeKind.all():
            raise ValueError(f"Unsupported runtime: {self.runtime!r}")
        if not self.entrypoint:
            raise ValueError("Entry descriptor entrypoint must be non-empty.")

    def to_dict(self) -> dict[str, Any]:
        return {"runtime": self.runtime, "entrypoint": self.entrypoint}


@dataclass(frozen=True)
class ChildRef:
    """A composite's child: **embedded** (contained) or **referenced** (pinned).

    Exactly one of ``embed``/``ref`` must be set. An embedded child is covered by
    the composite's own content hash; a referenced child is pinned in
    ``components.lock``.
    """

    embed: str = ""
    ref: str = ""

    def __post_init__(self) -> None:
        if bool(self.embed) == bool(self.ref):
            raise ValueError("A child must be exactly one of embed/ref.")

    @property
    def mode(self) -> str:
        return "embed" if self.embed else "ref"

    @property
    def target(self) -> str:
        return self.embed or self.ref

    def to_dict(self) -> dict[str, Any]:
        return {self.mode: self.target}


@dataclass(frozen=True)
class Provenance:
    """Where a component came from, and its integrity evidence.

    Attributes:
        repo_url: The repository the component is distributed from.
        commit_sha: The immutable commit (40- or 64-hex).
        tree_sha256: The content hash of the component artifact (64-hex).
        signature: An optional detached signature (verifier-specific encoding).
    """

    repo_url: str
    commit_sha: str
    tree_sha256: str
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.repo_url:
            raise ValueError("Provenance repo_url must be non-empty.")
        if not _COMMIT_RE.match(self.commit_sha):
            raise ValueError(f"Provenance commit_sha must be 40/64-hex: {self.commit_sha!r}")
        if not _SHA256_RE.match(self.tree_sha256):
            raise ValueError(f"Provenance tree_sha256 must be 64-hex: {self.tree_sha256!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_url": self.repo_url,
            "commit_sha": self.commit_sha,
            "tree_sha256": self.tree_sha256,
            "signature": self.signature,
        }


@dataclass(frozen=True)
class ComponentManifest:
    """Immutable declaration of a component (``component.v1``)."""

    version: str
    name: str
    kind: str
    entry: EntryDescriptor
    implements: tuple[str, ...] = ()
    state: StateDescriptor | None = None
    children: tuple[ChildRef, ...] = ()
    directive: str = ""
    capabilities: tuple[Capability, ...] = ()
    provenance: Provenance | None = None
    ports: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version != ComponentVersion.V1:
            raise ValueError(f"Unsupported component version: {self.version!r}")
        if not self.name:
            raise ValueError("Component name must be non-empty.")
        if self.kind not in ComponentKind.all():
            raise ValueError(f"Unsupported component kind: {self.kind!r}")
        if self.children and self.kind != ComponentKind.COMPOSITE:
            raise ValueError("Only a composite component may declare children.")
        if not self.implements:
            raise ValueError("A component must implement at least one harness contract.")
        for port in ("in", "out"):
            if port in self.ports and not isinstance(self.ports[port], tuple):
                raise ValueError("Component ports must be tuples of names.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "kind": self.kind,
            "implements": list(self.implements),
            "state": self.state.to_dict() if self.state else None,
            "entry": self.entry.to_dict(),
            "children": [c.to_dict() for c in self.children],
            "directive": self.directive,
            "capabilities": [
                {"name": c.name, "version": c.version} for c in self.capabilities
            ],
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "ports": {k: list(v) for k, v in self.ports.items()},
        }
