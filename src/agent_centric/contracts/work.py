"""Work contract (versioned) — ``work.v1`` (SPEC-0021).

A **``work.v1`` recipe** is the declarative intent for a unit of work: where it
comes from (**obtain** a pinned source, *or* **create/generate** from a pinned
generator), how it is **built**, the **verifier** it must pass, its
**provenance + grant**, and the hard **envelope** it runs under. It is an intent
layer, not a rival ontology: :func:`compile_work` compiles it, **purely**, onto a
``component.v1`` — the compiled artifact *is* a component.

Invariants, all fail-closed:

- The compile seam is a **pure function**: an identical recipe yields a
  byte-identical artifact hash. The only non-deterministic step is generation,
  which is confined to a ``model`` component whose output is a **suspect** —
  canonicalized, pinned, and verified before it is used.
- Generated work is **untrusted until it passes its declared verifier** (and the
  conformance suite, when the verifier requires it); it runs at most at **A0**
  until it earns an Assurance Label. Automatic labeling is operator-gated
  (SPEC-0019) and is not representable here.
- The **dynamic task-load protocol** is explicit and fail-closed
  (SPEC-0002 open item #2): all channels and tasks are announced over the
  **initial hard-coded** channels (``control``); a task's identity is its
  **signed content hash** (never its ports); and an unpinned or undeclared task
  is refused **before** any bind, with no partial side effect.

``component-abi.v1`` is consumed and unchanged; the legacy ``task.v1`` is
untouched. Contracts remain **additive-only**; this file performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .capability import Capability
from .component import (
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
    Provenance,
)

WORK_SCHEMA = "work.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

# The ABI's initial hard-coded channels (ABI.md §3). Every channel and task is
# announced over `control`; the data plane travels on `data`. A component at rest
# has no edges; ports are never identity.
CONTROL_CHANNEL = "control"
DATA_CHANNEL = "data"
INITIAL_CHANNELS = (CONTROL_CHANNEL, DATA_CHANNEL)

# The assurance a compiled work item starts at: untrusted until it earns a label.
UNTRUSTED_ASSURANCE = "A0"

# A component at rest declares no edges; a manifest that smuggles one is refused.
_FORBIDDEN_EDGE_KEYS = ("edges", "wires", "connections")


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


class WorkVersion:
    """The supported work contract version."""

    V1 = WORK_SCHEMA

    __slots__ = ()


class WorkOrigin:
    """How a work item comes to exist (SPEC-0021 §2)."""

    OBTAIN = "obtain"
    GENERATE = "generate"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.OBTAIN, cls.GENERATE)


class WorkSourceKind:
    """Where a pinned source is obtained from (SPEC-0012 registry sources)."""

    GIT = "git"
    DIRECTORY = "directory"
    BINARY = "binary"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.GIT, cls.DIRECTORY, cls.BINARY)


@dataclass(frozen=True)
class SourceRef:
    """A pinned **obtain** source: an immutable commit plus a content address."""

    repo_url: str
    commit: str
    digest: str
    kind: str = WorkSourceKind.DIRECTORY

    def __post_init__(self) -> None:
        if not self.repo_url:
            raise ValueError("SourceRef repo_url must be non-empty.")
        if not _COMMIT_RE.match(self.commit):
            raise ValueError(f"SourceRef commit must be 40/64-hex: {self.commit!r}")
        if not _SHA256_RE.match(self.digest):
            raise ValueError(f"SourceRef digest must be 64-hex sha256: {self.digest!r}")
        if self.kind not in WorkSourceKind.all():
            raise ValueError(f"Unsupported source kind: {self.kind!r}")

    def to_dict(self) -> dict[str, str]:
        return {
            "repo_url": self.repo_url,
            "commit": self.commit,
            "digest": self.digest,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRef:
        return cls(
            repo_url=str(data["repo_url"]),
            commit=str(data["commit"]),
            digest=str(data["digest"]),
            kind=str(data.get("kind", WorkSourceKind.DIRECTORY)),
        )


@dataclass(frozen=True)
class GeneratorRef:
    """A pinned **create/generate** reference (a ``model`` component + prompt + seed).

    The generator is the *only* non-deterministic step; its output is a suspect
    that must be canonicalized, pinned, and verified before use. ``ref`` names the
    generator component, ``digest`` pins it, and ``seed``/``prompt`` make the
    recorded request reproducible for audit.
    """

    ref: str
    digest: str
    prompt: str
    seed: int
    model: str = ""

    def __post_init__(self) -> None:
        if not self.ref:
            raise ValueError("GeneratorRef ref must be non-empty.")
        if not _SHA256_RE.match(self.digest):
            raise ValueError(
                f"GeneratorRef digest must be 64-hex sha256: {self.digest!r}"
            )
        if not self.prompt:
            raise ValueError("GeneratorRef prompt must be non-empty.")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("GeneratorRef seed must be a non-negative integer.")
        if not isinstance(self.model, str):
            raise ValueError("GeneratorRef model must be a string.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "digest": self.digest,
            "prompt": self.prompt,
            "seed": self.seed,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeneratorRef:
        return cls(
            ref=str(data["ref"]),
            digest=str(data["digest"]),
            prompt=str(data["prompt"]),
            seed=data["seed"],
            model=str(data.get("model", "")),
        )


@dataclass(frozen=True)
class BuildRecipe:
    """How a work item is built: language, ABI runtime, and toolchain pins."""

    language: str
    runtime: str
    toolchain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.language:
            raise ValueError("BuildRecipe language must be non-empty.")
        if self.runtime not in ("python", "process"):
            raise ValueError(
                f"BuildRecipe runtime must be 'python' or 'process': {self.runtime!r}"
            )
        for entry in self.toolchain:
            if not isinstance(entry, str) or not entry:
                raise ValueError("BuildRecipe toolchain entries must be non-empty strings.")
        if len(set(self.toolchain)) != len(self.toolchain):
            raise ValueError("BuildRecipe toolchain entries must be unique.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "runtime": self.runtime,
            "toolchain": list(self.toolchain),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildRecipe:
        toolchain = data.get("toolchain", ())
        if not isinstance(toolchain, (list, tuple)):
            raise ValueError("BuildRecipe toolchain must be a list.")
        return cls(
            language=str(data["language"]),
            runtime=str(data["runtime"]),
            toolchain=tuple(str(entry) for entry in toolchain),
        )


@dataclass(frozen=True)
class VerifierSpec:
    """The acceptance a work item must pass before it may execute.

    ``ref`` names the verifier, ``digest`` pins it, and ``conformance`` requires
    the shared conformance suite (SPEC-0013) as well.
    """

    ref: str
    digest: str
    conformance: bool = False

    def __post_init__(self) -> None:
        if not self.ref:
            raise ValueError("VerifierSpec ref must be non-empty.")
        if not _SHA256_RE.match(self.digest):
            raise ValueError(f"VerifierSpec digest must be 64-hex sha256: {self.digest!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "digest": self.digest, "conformance": self.conformance}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerifierSpec:
        return cls(
            ref=str(data["ref"]),
            digest=str(data["digest"]),
            conformance=bool(data.get("conformance", False)),
        )


@dataclass(frozen=True)
class WorkEnvelope:
    """The hard bounds a compiled work item runs under (least privilege)."""

    timeout_seconds: float
    max_steps: int
    max_step_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("WorkEnvelope timeout_seconds must be positive.")
        if isinstance(self.max_steps, bool) or self.max_steps <= 0:
            raise ValueError("WorkEnvelope max_steps must be a positive integer.")
        if self.max_step_seconds < 0:
            raise ValueError("WorkEnvelope max_step_seconds must not be negative.")
        if self.max_step_seconds and self.max_step_seconds > self.timeout_seconds:
            raise ValueError(
                "WorkEnvelope max_step_seconds must not exceed timeout_seconds."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_steps": self.max_steps,
            "max_step_seconds": self.max_step_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkEnvelope:
        return cls(
            timeout_seconds=float(data.get("timeout_seconds", 0)),
            max_steps=int(data.get("max_steps", 0)),
            max_step_seconds=float(data.get("max_step_seconds", 0.0)),
        )


@dataclass(frozen=True)
class WorkRecipe:
    """An immutable ``work.v1`` recipe (intent that compiles to a component)."""

    id: str
    build: BuildRecipe
    verifier: VerifierSpec
    envelope: WorkEnvelope
    source: SourceRef | None = None
    generator: GeneratorRef | None = None
    grants: tuple[Capability, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = WORK_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORK_SCHEMA:
            raise ValueError(f"Unsupported work schema: {self.schema!r}")
        if not self.id:
            raise ValueError("WorkRecipe id must be non-empty.")
        if (self.source is None) == (self.generator is None):
            raise ValueError(
                "a work recipe must declare exactly one of source (obtain) or "
                "generator (create/generate)."
            )
        grant_names = [grant.name for grant in self.grants]
        if len(grant_names) != len(set(grant_names)):
            raise ValueError("WorkRecipe grant names must be unique.")
        if not isinstance(self.metadata, dict):
            raise ValueError("WorkRecipe metadata must be a mapping.")
        object.__setattr__(
            self,
            "grants",
            tuple(sorted(self.grants, key=lambda grant: grant.name)),
        )

    @property
    def origin(self) -> str:
        """``obtain`` or ``generate`` — a pure function of the declared source."""
        return (
            WorkOrigin.GENERATE
            if self.generator is not None
            else WorkOrigin.OBTAIN
        )

    @property
    def is_generated(self) -> bool:
        """True iff this work item is generated (and thus untrusted until verified)."""
        return self.generator is not None

    def to_dict(self) -> dict[str, Any]:
        """Canonical, deterministic serialization (grants are name-sorted)."""
        return {
            "schema": self.schema,
            "id": self.id,
            "source": self.source.to_dict() if self.source else None,
            "generator": self.generator.to_dict() if self.generator else None,
            "build": self.build.to_dict(),
            "verifier": self.verifier.to_dict(),
            "envelope": self.envelope.to_dict(),
            "grants": [
                {"name": grant.name, "version": grant.version}
                for grant in sorted(self.grants, key=lambda g: g.name)
            ],
            "metadata": dict(self.metadata),
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for hashing/signing."""
        return _canonical_bytes(self.to_dict())

    def recipe_hash(self) -> str:
        """The content address of this recipe (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkRecipe:
        raw_source = data.get("source")
        raw_generator = data.get("generator")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("WorkRecipe metadata must be a mapping.")
        raw_grants = data.get("grants", ())
        if not isinstance(raw_grants, (list, tuple)):
            raise ValueError("WorkRecipe grants must be a list.")
        return cls(
            id=str(data["id"]),
            build=BuildRecipe.from_dict(data["build"]),
            verifier=VerifierSpec.from_dict(data["verifier"]),
            envelope=WorkEnvelope.from_dict(data["envelope"]),
            source=SourceRef.from_dict(raw_source) if raw_source else None,
            generator=GeneratorRef.from_dict(raw_generator) if raw_generator else None,
            grants=tuple(
                Capability(name=str(item["name"]), version=str(item.get("version", "1")))
                for item in raw_grants
            ),
            metadata=dict(metadata),
            schema=str(data.get("schema", WORK_SCHEMA)),
        )


@dataclass(frozen=True)
class WorkCompilation:
    """The pure result of compiling a recipe onto a ``component.v1``.

    The artifact hash covers both the recipe content address and the compiled
    component, so two distinct recipes never collide even if their component
    projections happen to match.
    """

    recipe_hash: str
    component: ComponentManifest
    origin: str
    assurance: str = UNTRUSTED_ASSURANCE
    channel: str = CONTROL_CHANNEL
    schema: str = WORK_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORK_SCHEMA:
            raise ValueError(f"Unsupported work schema: {self.schema!r}")
        if not _SHA256_RE.match(self.recipe_hash):
            raise ValueError(
                f"WorkCompilation recipe_hash must be 64-hex sha256: {self.recipe_hash!r}"
            )
        if self.origin not in WorkOrigin.all():
            raise ValueError(f"Unsupported work origin: {self.origin!r}")
        if self.assurance != UNTRUSTED_ASSURANCE:
            raise ValueError(
                "compiled work runs at most at A0 until it earns an Assurance "
                "Label; automatic labeling is operator-gated (SPEC-0019)."
            )
        if self.channel not in INITIAL_CHANNELS:
            raise ValueError(
                "a compilation is announced over an initial hard-coded channel."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "recipe_hash": self.recipe_hash,
            "origin": self.origin,
            "assurance": self.assurance,
            "channel": self.channel,
            "component": self.component.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for the artifact address."""
        return _canonical_bytes(self.to_dict())

    def artifact_hash(self) -> str:
        """The content address of the compiled artifact (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkCompilation:
        component = data.get("component")
        if not isinstance(component, dict):
            raise ValueError("WorkCompilation component must be an object.")
        return cls(
            recipe_hash=str(data["recipe_hash"]),
            component=ComponentManifest.from_dict(component),
            origin=str(data["origin"]),
            assurance=str(data.get("assurance", UNTRUSTED_ASSURANCE)),
            channel=str(data.get("channel", CONTROL_CHANNEL)),
            schema=str(data.get("schema", WORK_SCHEMA)),
        )


def _source_provenance(recipe: WorkRecipe) -> Provenance | None:
    """The declared provenance of an **obtain** recipe (a generated one is not
    yet a trusted, distributed artifact, so it carries none)."""
    source = recipe.source
    if source is None:
        return None
    return Provenance(
        repo_url=source.repo_url,
        commit_sha=source.commit,
        tree_sha256=source.digest,
    )


def compile_work(recipe: WorkRecipe) -> WorkCompilation:
    """Compile a ``work.v1`` recipe onto a ``component.v1`` — **purely**.

    No I/O, no generation: the same recipe always yields a byte-identical
    :class:`WorkCompilation` (and thus artifact hash). The compiled component is
    an ordinary ``component.v1``; a component at rest carries no edges.
    """
    component = ComponentManifest(
        version=ComponentVersion.V1,
        name=recipe.id,
        kind=ComponentKind.ATOMIC,
        entry=EntryDescriptor(
            runtime=recipe.build.runtime,
            entrypoint=f"work:{recipe.id}",
        ),
        implements=("component.v1",),
        capabilities=recipe.grants,
        provenance=_source_provenance(recipe),
    )
    return WorkCompilation(
        recipe_hash=recipe.recipe_hash(),
        component=component,
        origin=recipe.origin,
    )


def assert_work_executable(
    recipe: WorkRecipe,
    *,
    verifier_ok: bool,
    conformance_ok: bool = False,
) -> None:
    """Refuse to execute a work item that has not passed its declared verifier.

    Fail-closed: an unverified work item (in particular **generated** work, which
    is only a suspect) cannot execute; and a recipe whose verifier requires the
    conformance suite cannot execute without it.

    Raises:
        ValueError: If the work item may not execute yet.
    """
    if not verifier_ok:
        raise ValueError(
            "work is untrusted until it passes its declared verifier; refusing "
            "to execute."
        )
    if recipe.verifier.conformance and not conformance_ok:
        raise ValueError(
            "work declares a conformance verifier but has no conformance pass "
            "(SPEC-0013); refusing to execute."
        )


@dataclass(frozen=True)
class TaskAnnouncement:
    """A task announced over the initial hard-coded control channel.

    A task's **identity is its signed content hash**; there are no ports here,
    because ports are dynamic runtime endpoints and are **never** identity.
    """

    task: str
    content_hash: str
    channel: str = CONTROL_CHANNEL
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.task:
            raise ValueError("TaskAnnouncement task must be non-empty.")
        if not _SHA256_RE.match(self.content_hash):
            raise ValueError(
                f"TaskAnnouncement content_hash must be 64-hex sha256: "
                f"{self.content_hash!r}"
            )
        if self.channel != CONTROL_CHANNEL:
            raise ValueError(
                "tasks are announced only over the initial hard-coded control "
                f"channel: {self.channel!r}"
            )

    @property
    def identity(self) -> str:
        """The task identity: its signed content hash, never its ports."""
        return self.content_hash

    def to_dict(self) -> dict[str, str]:
        return {
            "task": self.task,
            "content_hash": self.content_hash,
            "channel": self.channel,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskAnnouncement:
        return cls(
            task=str(data["task"]),
            content_hash=str(data["content_hash"]),
            channel=str(data.get("channel", CONTROL_CHANNEL)),
            signature=str(data.get("signature", "")),
        )


@dataclass(frozen=True)
class TaskLoad:
    """A dynamically loaded task, content-addressed before it may bind/run."""

    task: str
    content_hash: str
    artifact: str
    channel: str = CONTROL_CHANNEL
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.task:
            raise ValueError("TaskLoad task must be non-empty.")
        if not _SHA256_RE.match(self.content_hash):
            raise ValueError(
                f"TaskLoad content_hash must be 64-hex sha256: {self.content_hash!r}"
            )
        if not self.artifact:
            raise ValueError("TaskLoad artifact must be non-empty.")
        if self.channel not in INITIAL_CHANNELS:
            raise ValueError(f"Unsupported load channel: {self.channel!r}")

    @property
    def identity(self) -> str:
        """The task identity: its signed content hash, never its ports."""
        return self.content_hash

    def to_dict(self) -> dict[str, str]:
        return {
            "task": self.task,
            "content_hash": self.content_hash,
            "artifact": self.artifact,
            "channel": self.channel,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskLoad:
        smuggled = sorted(key for key in _FORBIDDEN_EDGE_KEYS if key in data)
        if smuggled:
            raise ValueError(
                "a loaded task carries no edges (a component at rest has none); "
                f"remove {smuggled}."
            )
        return cls(
            task=str(data["task"]),
            content_hash=str(data["content_hash"]),
            artifact=str(data["artifact"]),
            channel=str(data.get("channel", CONTROL_CHANNEL)),
            signature=str(data.get("signature", "")),
        )


def admit_task_load(
    load: TaskLoad,
    *,
    declared_tasks: tuple[str, ...],
    expected_hash: str,
) -> TaskAnnouncement:
    """Admit a dynamic task load, fail-closed, **before any bind**.

    A dynamically loaded task must be **declared** (announced) and **pinned** to
    the verified signed content hash. An undeclared or unpinned task is an
    explicit refusal with no partial side effect.

    Raises:
        ValueError: If the task is undeclared or not pinned to ``expected_hash``.
    """
    if load.task not in declared_tasks:
        raise ValueError(
            f"task {load.task!r} was not announced/declared; refusing before any bind."
        )
    if not _SHA256_RE.match(expected_hash) or load.content_hash != expected_hash:
        raise ValueError(
            f"task {load.task!r} is not pinned to the verified content hash; "
            "refusing before any bind."
        )
    return TaskAnnouncement(
        task=load.task,
        content_hash=load.content_hash,
        channel=load.channel,
        signature=load.signature,
    )
