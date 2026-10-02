"""UI contract (versioned) — ``ui.v1`` (SPEC-0022).

A **UI component** is an ordinary CBP component whose typed surface is
*presentation*: it declares the projections it consumes and the directives it may
emit, and it renders a **target-neutral** tree. It is deliberately **not** a new
node kind (SPEC-0022 §1): it is a manifest over the same component ontology, and
the front end is a pinned composition of such components.

This module freezes the **pure** part of the contract, fail-closed:

- A :class:`UIComponent` is canonical and content-hashable
  (:meth:`UIComponent.ui_hash`); identical declarations hash identically
  regardless of collection order.
- It is **target-agnostic** (:class:`UITarget`: ``web``/``cli``/``os``/
  ``embedded``): the same manifest binds to any target.
- It holds **no execution capability and no ambient authority**: only declared
  ``projections`` (reads), ``directives`` (effects), ``capabilities`` (grants),
  ``slots``, design ``tokens``, and secret **references** (never material).
- :func:`validate_ui` is a deterministic policy check (strict UI-ABI negotiation,
  required targets); :func:`assert_projection_granted` and
  :func:`assert_directive_declared` are the **default-deny** boundaries the edge
  enforces.

The UI ABI's typed world lives in the shared contract
(``conformance/contracts/ui-v1.wit`` + ``UI-ABI.md``, content-addressed in
``ui.lock.v1.json``). This module performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .capability import Capability
from .service import SecretRef

UI_SCHEMA = "ui.v1"
UI_ABI_VERSION = "1.0.0"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# A UI manifest declares secret *references* only; the same rejection as
# ``provider.ResourceSpec`` is applied to ``tokens`` so a design token map cannot
# smuggle a plaintext secret into a manifest/record/repository.
_FORBIDDEN_SECRET_KEYS = ("value", "secret", "plaintext", "data", "material")


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


class UITarget:
    """A rendering medium a UI binds to. Target-neutral; a target never changes
    a UI component's meaning (SPEC-0016)."""

    WEB = "web"
    CLI = "cli"
    OS = "os"
    EMBEDDED = "embedded"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.WEB, cls.CLI, cls.OS, cls.EMBEDDED)

    @classmethod
    def is_known(cls, target: str) -> bool:
        return target in cls.all()


class Assurance:
    """The honest status of a projection (SPEC-0015)."""

    VERIFIED = "verified"
    MODEL = "model"
    UNVERIFIED = "unverified"
    REFUSED = "refused"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.VERIFIED, cls.MODEL, cls.UNVERIFIED, cls.REFUSED)


@dataclass(frozen=True)
class DirectiveBinding:
    """A directive a UI may emit, and whether it requires explicit approval.

    A directive is a content-addressed intent executed by the verified spine; the
    UI never mutates state directly. A directive that is not idempotent or is
    destructive must declare ``requires_approval``.
    """

    name: str
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("DirectiveBinding name must be non-empty.")
        if not _SCHEMA_RE.match(self.name):
            raise ValueError(f"DirectiveBinding name must be a lowercase id: {self.name!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "requires_approval": self.requires_approval}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DirectiveBinding:
        return cls(
            name=str(data["name"]),
            requires_approval=bool(data.get("requires_approval", False)),
        )


@dataclass(frozen=True)
class UIComponent:
    """An immutable, target-agnostic ``ui.v1`` presentation manifest."""

    id: str
    abi_version: str = UI_ABI_VERSION
    targets: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    directives: tuple[DirectiveBinding, ...] = ()
    capabilities: tuple[Capability, ...] = ()
    slots: tuple[str, ...] = ()
    tokens: dict[str, Any] = field(default_factory=dict)
    secrets: tuple[SecretRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = UI_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != UI_SCHEMA:
            raise ValueError(f"Unsupported ui schema: {self.schema!r}")
        if not self.id:
            raise ValueError("UIComponent id must be non-empty.")
        if not self.abi_version:
            raise ValueError("UIComponent abi_version must be non-empty.")
        if not self.targets:
            raise ValueError("a UI component must target at least one UI target.")
        for target in self.targets:
            if not UITarget.is_known(target):
                raise ValueError(f"Unsupported ui target: {target!r}")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("UIComponent targets must be unique.")
        if not self.projections:
            raise ValueError("a UI component must declare at least one projection.")
        for schema_id in self.projections:
            if not _SCHEMA_RE.match(schema_id):
                raise ValueError(f"Unsupported projection schema id: {schema_id!r}")
        if len(set(self.projections)) != len(self.projections):
            raise ValueError("UIComponent projections must be unique.")
        directive_names = [binding.name for binding in self.directives]
        if len(directive_names) != len(set(directive_names)):
            raise ValueError("UIComponent directive names must be unique.")
        capability_names = [capability.name for capability in self.capabilities]
        if len(capability_names) != len(set(capability_names)):
            raise ValueError("UIComponent capability names must be unique.")
        if len(set(self.slots)) != len(self.slots):
            raise ValueError("UIComponent slots must be unique.")
        if any(not slot for slot in self.slots):
            raise ValueError("UIComponent slots must be non-empty.")
        secret_names = [secret.name for secret in self.secrets]
        if len(secret_names) != len(set(secret_names)):
            raise ValueError("UIComponent secret names must be unique.")
        if not isinstance(self.tokens, dict):
            raise ValueError("UIComponent tokens must be a mapping.")
        smuggled = sorted(key for key in _FORBIDDEN_SECRET_KEYS if key in self.tokens)
        if smuggled:
            raise ValueError(
                "tokens declare references only; remove "
                f"{smuggled} (never store secret material in a manifest)."
            )
        if not isinstance(self.metadata, dict):
            raise ValueError("UIComponent metadata must be a mapping.")
        # Canonical order: the manifest is content, not sequence.
        object.__setattr__(self, "targets", tuple(sorted(self.targets)))
        object.__setattr__(self, "projections", tuple(sorted(self.projections)))
        object.__setattr__(
            self,
            "directives",
            tuple(sorted(self.directives, key=lambda binding: binding.name)),
        )
        object.__setattr__(
            self,
            "capabilities",
            tuple(sorted(self.capabilities, key=lambda capability: capability.name)),
        )
        object.__setattr__(self, "slots", tuple(sorted(self.slots)))
        object.__setattr__(
            self, "secrets", tuple(sorted(self.secrets, key=lambda secret: secret.name))
        )

    # -- default-deny boundaries --------------------------------------------

    def may_read(self, schema: str) -> bool:
        """True iff the component declared this projection schema."""
        return schema in self.projections

    def directive(self, name: str) -> DirectiveBinding | None:
        """The binding for ``name``, or ``None`` (default deny)."""
        for binding in self.directives:
            if binding.name == name:
                return binding
        return None

    def may_emit(self, name: str) -> bool:
        """True iff the component declared this directive name."""
        return self.directive(name) is not None

    # -- canonical form ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Canonical, deterministic serialization (name-sorted collections)."""
        return {
            "schema": self.schema,
            "id": self.id,
            "abi_version": self.abi_version,
            "targets": list(self.targets),
            "projections": list(self.projections),
            "directives": [binding.to_dict() for binding in self.directives],
            "capabilities": [
                {"name": capability.name, "version": capability.version}
                for capability in self.capabilities
            ],
            "slots": list(self.slots),
            "tokens": dict(self.tokens),
            "secrets": [secret.to_dict() for secret in self.secrets],
            "metadata": dict(self.metadata),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def ui_hash(self) -> str:
        """The content address of this UI component (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UIComponent:
        raw_targets = data.get("targets", [])
        raw_projections = data.get("projections", [])
        raw_directives = data.get("directives", [])
        raw_caps = data.get("capabilities", [])
        raw_slots = data.get("slots", [])
        raw_secrets = data.get("secrets", [])
        for name, value in (
            ("targets", raw_targets),
            ("projections", raw_projections),
            ("directives", raw_directives),
            ("capabilities", raw_caps),
            ("slots", raw_slots),
            ("secrets", raw_secrets),
        ):
            if not isinstance(value, list):
                raise ValueError(f"UIComponent {name} must be a list.")
        return cls(
            id=str(data["id"]),
            abi_version=str(data.get("abi_version", UI_ABI_VERSION)),
            targets=tuple(str(target) for target in raw_targets),
            projections=tuple(str(schema_id) for schema_id in raw_projections),
            directives=tuple(DirectiveBinding.from_dict(item) for item in raw_directives),
            capabilities=tuple(
                Capability(name=str(item["name"]), version=str(item.get("version", "1")))
                for item in raw_caps
            ),
            slots=tuple(str(slot) for slot in raw_slots),
            tokens=dict(data.get("tokens") or {}),
            secrets=tuple(SecretRef.from_dict(item) for item in raw_secrets),
            metadata=dict(data.get("metadata") or {}),
            schema=str(data.get("schema", UI_SCHEMA)),
        )


@dataclass(frozen=True)
class UIReport:
    """The deterministic outcome of validating a UI component manifest."""

    ok: bool
    errors: tuple[str, ...]
    targets: tuple[str, ...]


def validate_ui(
    component: UIComponent,
    *,
    required_targets: tuple[str, ...] = (),
    allowed_abi_versions: tuple[str, ...] = (UI_ABI_VERSION,),
    require_projections: bool = True,
) -> UIReport:
    """Validate a UI component deterministically and fail-closed.

    Enforces **strict UI-ABI negotiation** (an unknown ``abi_version`` refuses),
    the presence of at least one target and (by default) one projection, and the
    required targets. It never raises on content; the caller surfaces the report
    and refuses on ``not ok``.
    """
    errors: list[str] = []
    if component.abi_version not in allowed_abi_versions:
        errors.append(
            f"unsupported ui abi version {component.abi_version!r}; "
            f"supported: {sorted(allowed_abi_versions)}"
        )
    if not component.targets:
        errors.append("a UI component must target at least one UI target")
    for target in required_targets:
        if target not in component.targets:
            errors.append(f"UI component does not target required target {target!r}")
    if require_projections and not component.projections:
        errors.append("a UI component must declare at least one projection")
    return UIReport(
        ok=not errors, errors=tuple(errors), targets=tuple(component.targets)
    )


def assert_projection_granted(component: UIComponent, schema: str) -> None:
    """Default-deny: refuse a projection the component did not declare."""
    if not component.may_read(schema):
        raise ValueError(
            f"projection {schema!r} is not declared by UI component {component.id!r} "
            "(default deny)."
        )


def assert_directive_declared(component: UIComponent, name: str) -> DirectiveBinding:
    """Default-deny: refuse a directive the component did not declare."""
    binding = component.directive(name)
    if binding is None:
        raise ValueError(
            f"directive {name!r} is not declared by UI component {component.id!r} "
            "(default deny)."
        )
    return binding
