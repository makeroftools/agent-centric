"""Provider contract (versioned) — ``provider.v1`` (SPEC-0020).

A **provider** is the thin, declarative adapter a service hosting layer targets:
it declares its identity, the target it hosts on (local first, cloud explicit),
its capabilities/grants, and a fixed set of named tasks. It is deliberately
**not** a node kind (SPEC-0020 §7): it is a manifest over the deterministic
task/adapter discipline of :mod:`agent_centric.contracts.service`.

This module freezes the **pure** part of the contract, fail-closed:

- :func:`plan_provider` is **pure**: the desired (and observed) state in, a
  canonical, content-addressed :class:`ProviderPlan` out. It performs no I/O and
  is byte-deterministic, so identical desired state yields a byte-identical pin.
- A plan is **verified before apply** (:func:`assert_plan_appliable`): a drifted
  pin, a mutating step with no content digest, or an unapproved destructive/cloud
  step is refused before any provider call.
- :func:`reconcile_action` is the deterministic reconcile decision: already
  converged state performs no mutating call; a failed health gate rolls back to
  the last healthy pinned plan, or **escalates and stops** after a bounded number
  of attempts (never a partial or ambiguous success).
- :class:`NotificationBinding` is the **emit-only** notification boundary: it may
  hold only ``notify.emit`` and can never mutate infrastructure.
- Secrets are **references only** (``service.SecretRef``); `plan`/`ResourceSpec`
  refuse secret material, so no plaintext secret can appear in a plan or record.

Provider choice is a **deployment** axis only (SPEC-0016): it never changes
semantics. Contracts remain **additive-only**; this file performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .capability import Capability
from .service import PinnedRef, SecretRef, TaskBinding

PROVIDER_SCHEMA = "provider.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The capability grant required to target a cloud provider explicitly; local is
# the default (PRINCIPLES Law 5) and needs no such grant.
CLOUD_GRANT = "provider.cloud"

# The only capability an emit-only notification component may hold.
NOTIFICATION_EMIT_CAPABILITY = "notify.emit"

# Secret *references* must never carry material. The same rejection as
# ``service.SecretRef`` is applied to a planned resource's spec so a desired
# state cannot smuggle a plaintext secret into a plan/record/repository.
_FORBIDDEN_SECRET_KEYS = ("value", "secret", "plaintext", "data", "material")

_ALL_NOTIFICATION_EVENTS = ("provisioned", "healed", "rolled-back", "escalated")


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ProviderVersion:
    """The supported provider contract version."""

    V1 = PROVIDER_SCHEMA

    __slots__ = ()


class ProviderTarget:
    """Where a provider hosts. Local is the default; cloud is explicit."""

    LOCAL = "local"
    CLOUD = "cloud"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.LOCAL, cls.CLOUD)

    @classmethod
    def is_cloud(cls, target: str) -> bool:
        return target == cls.CLOUD


class ProviderTask:
    """The fixed set of named tasks a provider declares (SPEC-0020 §1)."""

    PLAN = "plan"
    APPLY = "apply"
    HEALTH = "health"
    TEARDOWN = "teardown"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.PLAN, cls.APPLY, cls.HEALTH, cls.TEARDOWN)

    @classmethod
    def gated(cls) -> tuple[str, ...]:
        """The network-facing tasks gated by verify-then-apply."""
        return (cls.APPLY, cls.HEALTH, cls.TEARDOWN)


class PlanAction:
    """The action a plan step takes against a declared resource."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.CREATE, cls.UPDATE, cls.DELETE, cls.NOOP)

    @classmethod
    def is_mutating(cls, action: str) -> bool:
        return action in (cls.CREATE, cls.UPDATE, cls.DELETE)


class ReconcileAction:
    """The deterministic decision of one reconcile step (SPEC-0020 §3)."""

    CONVERGED = "converged"
    APPLY = "apply"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.CONVERGED, cls.APPLY, cls.ROLLBACK, cls.ESCALATE)


class NotificationEvent:
    """The state transitions an emit-only notification component observes."""

    PROVISIONED = "provisioned"
    HEALED = "healed"
    ROLLED_BACK = "rolled-back"
    ESCALATED = "escalated"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.PROVISIONED, cls.HEALED, cls.ROLLED_BACK, cls.ESCALATED)


@dataclass(frozen=True)
class ResourceSpec:
    """One desired resource: a piece of declared, content-addressed state.

    ``spec`` is opaque desired configuration; it is canonicalized (sorted keys)
    so its :meth:`resource_hash` is deterministic. It carries **references**
    only — a spec that tries to smuggle a secret ``value``/``plaintext`` is
    rejected on parse (never store secret material in a plan).
    """

    kind: str
    name: str
    spec: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("ResourceSpec kind must be non-empty.")
        if not self.name:
            raise ValueError("ResourceSpec name must be non-empty.")
        if not isinstance(self.spec, dict):
            raise ValueError("ResourceSpec spec must be a mapping.")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "spec": dict(self.spec)}

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for the content address."""
        return _canonical_bytes(self.to_dict())

    def resource_hash(self) -> str:
        """The content address of this desired resource (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceSpec:
        if not isinstance(data, dict):
            raise ValueError("ResourceSpec must be an object.")
        raw_spec = data.get("spec", {})
        if not isinstance(raw_spec, dict):
            raise ValueError("ResourceSpec spec must be a mapping.")
        smuggled = sorted(key for key in _FORBIDDEN_SECRET_KEYS if key in raw_spec)
        if smuggled:
            raise ValueError(
                "a resource spec declares secret *references* only; remove "
                f"{smuggled} (never store secret material in a plan)."
            )
        return cls(
            kind=str(data["kind"]),
            name=str(data["name"]),
            spec=dict(raw_spec),
        )


@dataclass(frozen=True)
class ObservedResource:
    """One observed resource, reduced to the content address of its state.

    An absent resource is simply omitted from an observed-state tuple; a present
    resource's ``resource_hash`` is the content address of its current spec.
    """

    kind: str
    name: str
    resource_hash: str

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("ObservedResource kind must be non-empty.")
        if not self.name:
            raise ValueError("ObservedResource name must be non-empty.")
        if not _SHA256_RE.match(self.resource_hash):
            raise ValueError(
                f"ObservedResource resource_hash must be 64-hex sha256: "
                f"{self.resource_hash!r}"
            )

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name, "resource_hash": self.resource_hash}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservedResource:
        return cls(
            kind=str(data["kind"]),
            name=str(data["name"]),
            resource_hash=str(data["resource_hash"]),
        )


@dataclass(frozen=True)
class PlanStep:
    """One content-addressed action in a :class:`ProviderPlan`.

    Invariants, all fail-closed: a mutating step (create/update/delete) **must**
    carry a content ``digest``; a non-mutating step must not; and a destructive
    (delete) step must declare that it ``requires_approval``.
    """

    action: str
    kind: str
    name: str
    digest: str = ""
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if self.action not in PlanAction.all():
            raise ValueError(f"Unsupported plan action: {self.action!r}")
        if not self.kind:
            raise ValueError("PlanStep kind must be non-empty.")
        if not self.name:
            raise ValueError("PlanStep name must be non-empty.")
        if self.digest and not _SHA256_RE.match(self.digest):
            raise ValueError(f"PlanStep digest must be 64-hex sha256: {self.digest!r}")
        if PlanAction.is_mutating(self.action):
            if not self.digest:
                raise ValueError(
                    f"mutating plan step {self.action} {self.kind}/{self.name} "
                    "must be content-pinned with a digest"
                )
        elif self.digest:
            raise ValueError("a non-mutating plan step must not carry a digest.")
        if self.action == PlanAction.DELETE and not self.requires_approval:
            raise ValueError("a destructive (delete) plan step must require approval.")

    @property
    def mutating(self) -> bool:
        return PlanAction.is_mutating(self.action)

    @property
    def destructive(self) -> bool:
        return self.action == PlanAction.DELETE

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "kind": self.kind,
            "name": self.name,
            "digest": self.digest,
            "requires_approval": self.requires_approval,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        return cls(
            action=str(data["action"]),
            kind=str(data["kind"]),
            name=str(data["name"]),
            digest=str(data.get("digest", "")),
            requires_approval=bool(data.get("requires_approval", False)),
        )


@dataclass(frozen=True)
class ProviderPlan:
    """An immutable, canonical, content-addressed provider plan.

    The plan is **content, not sequence**: steps are sorted at construction so
    equality and :meth:`plan_hash` are independent of the order the planner
    happened to emit them.
    """

    provider: str
    target: str
    desired_hash: str
    steps: tuple[PlanStep, ...] = ()
    schema: str = PROVIDER_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROVIDER_SCHEMA:
            raise ValueError(f"Unsupported provider schema: {self.schema!r}")
        if not self.provider:
            raise ValueError("ProviderPlan provider must be non-empty.")
        if self.target not in ProviderTarget.all():
            raise ValueError(f"Unsupported provider target: {self.target!r}")
        if not _SHA256_RE.match(self.desired_hash):
            raise ValueError(
                f"ProviderPlan desired_hash must be 64-hex sha256: {self.desired_hash!r}"
            )
        object.__setattr__(
            self,
            "steps",
            tuple(sorted(self.steps, key=lambda step: (step.kind, step.name, step.action))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "provider": self.provider,
            "target": self.target,
            "desired_hash": self.desired_hash,
            "steps": [step.to_dict() for step in self.steps],
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for the pin/signature."""
        return _canonical_bytes(self.to_dict())

    def plan_hash(self) -> str:
        """The content address of this plan (sha256, hex) — pinned before apply."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def mutating_steps(self) -> tuple[PlanStep, ...]:
        return tuple(step for step in self.steps if step.mutating)

    def is_converged(self) -> bool:
        """True iff applying this plan would make no mutating provider call."""
        return not self.mutating_steps

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderPlan:
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list):
            raise ValueError("ProviderPlan steps must be a list.")
        return cls(
            provider=str(data["provider"]),
            target=str(data["target"]),
            desired_hash=str(data["desired_hash"]),
            steps=tuple(PlanStep.from_dict(step) for step in raw_steps),
            schema=str(data.get("schema", PROVIDER_SCHEMA)),
        )


def plan_provider(
    provider: str,
    desired: tuple[ResourceSpec, ...] = (),
    observed: tuple[ObservedResource, ...] = (),
    *,
    target: str = ProviderTarget.LOCAL,
) -> ProviderPlan:
    """Compute a provider plan **purely** from desired (and observed) state.

    This function performs **no I/O**: it canonicalizes its inputs, diffs them
    deterministically, and returns a content-addressed plan. Identical desired
    state therefore yields a byte-identical plan; the declaration order of the
    resources does not matter.

    Raises:
        ValueError: On an empty provider id, an unknown target, or a duplicate
            resource in either state (fail-closed, before any side effect).
    """
    if not provider:
        raise ValueError("provider id must be non-empty.")
    if target not in ProviderTarget.all():
        raise ValueError(f"Unsupported provider target: {target!r}")

    desired_by_key: dict[tuple[str, str], ResourceSpec] = {}
    for resource in desired:
        key = (resource.kind, resource.name)
        if key in desired_by_key:
            raise ValueError(f"duplicate desired resource: {key!r}")
        desired_by_key[key] = resource

    observed_by_key: dict[tuple[str, str], ObservedResource] = {}
    for observed_resource in observed:
        key = (observed_resource.kind, observed_resource.name)
        if key in observed_by_key:
            raise ValueError(f"duplicate observed resource: {key!r}")
        observed_by_key[key] = observed_resource

    cloud = ProviderTarget.is_cloud(target)
    steps: list[PlanStep] = []
    for key in sorted(desired_by_key):
        resource = desired_by_key[key]
        digest = resource.resource_hash()
        current = observed_by_key.get(key)
        if current is None:
            steps.append(
                PlanStep(
                    action=PlanAction.CREATE,
                    kind=resource.kind,
                    name=resource.name,
                    digest=digest,
                    requires_approval=cloud,
                )
            )
        elif current.resource_hash != digest:
            steps.append(
                PlanStep(
                    action=PlanAction.UPDATE,
                    kind=resource.kind,
                    name=resource.name,
                    digest=digest,
                    requires_approval=cloud,
                )
            )
        else:
            steps.append(
                PlanStep(action=PlanAction.NOOP, kind=resource.kind, name=resource.name)
            )
    for key in sorted(set(observed_by_key) - set(desired_by_key)):
        observed_resource = observed_by_key[key]
        steps.append(
            PlanStep(
                action=PlanAction.DELETE,
                kind=observed_resource.kind,
                name=observed_resource.name,
                digest=observed_resource.resource_hash,
                requires_approval=True,
            )
        )

    desired_document = {
        "resources": [desired_by_key[key].to_dict() for key in sorted(desired_by_key)]
    }
    desired_hash = hashlib.sha256(_canonical_bytes(desired_document)).hexdigest()
    return ProviderPlan(
        provider=provider,
        target=target,
        desired_hash=desired_hash,
        steps=tuple(steps),
    )


def is_plan_pinned(plan: ProviderPlan, expected_hash: str) -> bool:
    """True iff ``plan`` matches the pinned content address ``expected_hash``."""
    return bool(_SHA256_RE.match(expected_hash)) and plan.plan_hash() == expected_hash


def assert_plan_appliable(
    plan: ProviderPlan,
    *,
    expected_hash: str,
    approved: bool = False,
) -> None:
    """Verify-then-apply guard: refuse a plan that is not safe to apply.

    Fail-closed: a drifted (unpinned) plan, a mutating step with no content
    digest, or a step that requires (and lacks) explicit approval is refused
    **before** any provider call.

    Raises:
        ValueError: If the plan is not exactly the pinned plan, or a step is
            unapproved.
    """
    if not _SHA256_RE.match(expected_hash):
        raise ValueError("apply requires a pinned 64-hex plan hash.")
    if plan.plan_hash() != expected_hash:
        raise ValueError(
            "provider plan drifted: the pinned hash does not match the plan "
            "(refused before apply)."
        )
    for step in plan.steps:
        if step.mutating and not step.digest:
            raise ValueError(
                f"mutating plan step {step.action} {step.kind}/{step.name} "
                "declares no content digest (refused)."
            )
        if step.requires_approval and not approved:
            raise ValueError(
                f"plan step {step.action} {step.kind}/{step.name} requires "
                "operator approval (refused)."
            )


def assert_task_appliable(task: TaskBinding, *, require_digest: bool = True) -> None:
    """Refuse a provider task that may not be applied unattended.

    A task that is **not** idempotent must be explicitly gated; when
    ``require_digest`` is set (the default), a **mutating** task must carry a
    content digest so it can be verified before it runs.

    Raises:
        ValueError: If the task is unsafe to apply.
    """
    if not task.idempotent and not task.gated:
        raise ValueError(
            f"provider task {task.name!r} is not idempotent and is not explicitly gated."
        )
    if require_digest and task.mutating and not task.digest:
        raise ValueError(
            f"mutating provider task {task.name!r} declares no content digest."
        )


@dataclass(frozen=True)
class Provider:
    """An immutable ``provider.v1`` adapter manifest."""

    id: str
    target: str = ProviderTarget.LOCAL
    capabilities: tuple[Capability, ...] = ()
    tasks: tuple[TaskBinding, ...] = ()
    secrets: tuple[SecretRef, ...] = ()
    grants: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = PROVIDER_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROVIDER_SCHEMA:
            raise ValueError(f"Unsupported provider schema: {self.schema!r}")
        if not self.id:
            raise ValueError("Provider id must be non-empty.")
        if self.target not in ProviderTarget.all():
            raise ValueError(f"Unsupported provider target: {self.target!r}")
        capability_names = [capability.name for capability in self.capabilities]
        if len(capability_names) != len(set(capability_names)):
            raise ValueError("Provider capability names must be unique.")
        task_names = [task.name for task in self.tasks]
        if len(task_names) != len(set(task_names)):
            raise ValueError("Provider task names must be unique.")
        secret_names = [secret.name for secret in self.secrets]
        if len(secret_names) != len(set(secret_names)):
            raise ValueError("Provider secret names must be unique.")
        for grant in self.grants:
            if not isinstance(grant, str) or not grant:
                raise ValueError("Provider grants must be non-empty strings.")
        if len(set(self.grants)) != len(self.grants):
            raise ValueError("Provider grants must be unique.")
        if not isinstance(self.metadata, dict):
            raise ValueError("Provider metadata must be a mapping.")
        # Canonical order: the manifest is content, not sequence.
        object.__setattr__(
            self,
            "capabilities",
            tuple(sorted(self.capabilities, key=lambda capability: capability.name)),
        )
        object.__setattr__(
            self, "tasks", tuple(sorted(self.tasks, key=lambda task: task.name))
        )
        object.__setattr__(
            self,
            "secrets",
            tuple(sorted(self.secrets, key=lambda secret: secret.name)),
        )
        object.__setattr__(self, "grants", tuple(sorted(self.grants)))

    def is_cloud(self) -> bool:
        """True iff this provider targets an explicit cloud deployment."""
        return ProviderTarget.is_cloud(self.target)

    def to_dict(self) -> dict[str, Any]:
        """Canonical, deterministic serialization (name-sorted collections)."""
        return {
            "schema": self.schema,
            "id": self.id,
            "target": self.target,
            "capabilities": [
                {"name": capability.name, "version": capability.version}
                for capability in sorted(self.capabilities, key=lambda c: c.name)
            ],
            "tasks": [task.to_dict() for task in sorted(self.tasks, key=lambda t: t.name)],
            "secrets": [
                secret.to_dict() for secret in sorted(self.secrets, key=lambda s: s.name)
            ],
            "grants": sorted(self.grants),
            "metadata": dict(self.metadata),
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for hashing/signing."""
        return _canonical_bytes(self.to_dict())

    def provider_hash(self) -> str:
        """The content address of this provider (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def task(self, name: str) -> TaskBinding:
        """Return the task binding named ``name`` (fail-closed if absent)."""
        for binding in self.tasks:
            if binding.name == name:
                return binding
        raise ValueError(f"No provider task named {name!r}.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provider:
        raw_tasks = data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            raise ValueError("Provider tasks must be a list.")
        raw_secrets = data.get("secrets", [])
        if not isinstance(raw_secrets, list):
            raise ValueError("Provider secrets must be a list.")
        raw_caps = data.get("capabilities", [])
        if not isinstance(raw_caps, list):
            raise ValueError("Provider capabilities must be a list.")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("Provider metadata must be a mapping.")
        return cls(
            id=str(data["id"]),
            target=str(data.get("target", ProviderTarget.LOCAL)),
            capabilities=tuple(
                Capability(name=str(item["name"]), version=str(item.get("version", "1")))
                for item in raw_caps
            ),
            tasks=tuple(TaskBinding.from_dict(task) for task in raw_tasks),
            secrets=tuple(SecretRef.from_dict(secret) for secret in raw_secrets),
            grants=tuple(str(grant) for grant in data.get("grants", ())),
            metadata=dict(metadata),
            schema=str(data.get("schema", PROVIDER_SCHEMA)),
        )


@dataclass(frozen=True)
class ProviderReport:
    """The deterministic outcome of validating a provider manifest."""

    ok: bool
    errors: tuple[str, ...]
    task_names: tuple[str, ...]


def validate_provider(
    provider: Provider,
    *,
    required_tasks: tuple[str, ...] = (ProviderTask.APPLY, ProviderTask.HEALTH),
    require_task_digests: bool = True,
) -> ProviderReport:
    """Validate a provider manifest deterministically and fail-closed.

    Checks the canonical task roles are bound, that every task is idempotent or
    explicitly gated, that no two tasks share a name, and — when
    ``require_task_digests`` is set (the default) — that every **mutating** task
    carries a content digest. A **cloud** target additionally requires the
    explicit :data:`CLOUD_GRANT` (cloud is never implicit). It never raises on
    content; the caller surfaces the report and refuses on ``not ok``.

    Returns:
        A :class:`ProviderReport`; ``ok`` is true only when ``errors`` is empty.
    """
    errors: list[str] = []
    names = [task.name for task in provider.tasks]
    task_names = tuple(sorted(names))
    if not provider.tasks:
        errors.append("provider declares no tasks")
    if len(names) != len(set(names)):
        errors.append("provider task names must be unique")

    required = set(required_tasks)
    present = set(names)
    for role in sorted(required - present):
        errors.append(f"provider is missing the required task role {role!r}")

    for task in provider.tasks:
        if not task.idempotent and not task.gated:
            errors.append(
                f"task {task.name!r} is not idempotent and is not explicitly gated"
            )
        if require_task_digests and task.mutating and not task.digest:
            errors.append(f"mutating task {task.name!r} declares no content digest")

    if provider.is_cloud() and CLOUD_GRANT not in provider.grants:
        errors.append(
            f"a cloud provider requires the explicit {CLOUD_GRANT!r} grant "
            "(cloud is never the default; PRINCIPLES Law 5)"
        )

    return ProviderReport(ok=not errors, errors=tuple(errors), task_names=task_names)


def reconcile_action(
    *,
    converged: bool,
    health_ok: bool = True,
    rollback_available: bool = False,
    attempts: int = 0,
    max_attempts: int = 3,
) -> str:
    """The deterministic reconcile decision (SPEC-0020 §3), pure.

    - Already-converged state performs **no mutating call** (``converged``).
    - A healthy but non-converged state applies the pinned plan (``apply``).
    - A failed health gate rolls back to the last healthy pinned plan
      (``rollback``) while attempts remain and a healthy plan is available.
    - A repeated failure **escalates and stops** (``escalate``) — fail-closed,
      never a partial or ambiguous success.

    Raises:
        ValueError: If ``attempts`` is negative or ``max_attempts`` is not
            positive.
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError(f"attempts must be a non-negative integer: {attempts!r}")
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts <= 0
    ):
        raise ValueError(f"max_attempts must be a positive integer: {max_attempts!r}")
    if converged:
        return ReconcileAction.CONVERGED
    if health_ok:
        return ReconcileAction.APPLY
    if rollback_available and attempts < max_attempts:
        return ReconcileAction.ROLLBACK
    return ReconcileAction.ESCALATE


@dataclass(frozen=True)
class NotificationBinding:
    """The **emit-only** notification component boundary (SPEC-0020 §4).

    A notification is a component, not core logic: this binding pins the
    component and the transitions it observes. It is impossible for it to mutate
    infrastructure — the only capability it may hold is
    :data:`NOTIFICATION_EMIT_CAPABILITY` — and it carries no secret of its own
    (configuration is by reference).
    """

    component: PinnedRef
    events: tuple[str, ...] = _ALL_NOTIFICATION_EVENTS
    capabilities: tuple[str, ...] = (NOTIFICATION_EMIT_CAPABILITY,)
    schema: str = PROVIDER_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROVIDER_SCHEMA:
            raise ValueError(f"Unsupported provider schema: {self.schema!r}")
        if not self.events:
            raise ValueError("NotificationBinding declares no events.")
        if len(set(self.events)) != len(self.events):
            raise ValueError("NotificationBinding events must be unique.")
        for event in self.events:
            if event not in NotificationEvent.all():
                raise ValueError(f"Unsupported notification event: {event!r}")
        if not self.capabilities:
            raise ValueError("NotificationBinding declares no capabilities.")
        for capability in self.capabilities:
            if capability != NOTIFICATION_EMIT_CAPABILITY:
                raise ValueError(
                    "an emit-only notification component cannot hold capability "
                    f"{capability!r} (it may only emit)."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "component": self.component.to_dict(),
            "events": list(self.events),
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationBinding:
        raw_events = data.get("events", [])
        if not isinstance(raw_events, list):
            raise ValueError("NotificationBinding events must be a list.")
        raw_caps = data.get("capabilities", [])
        if not isinstance(raw_caps, list):
            raise ValueError("NotificationBinding capabilities must be a list.")
        return cls(
            component=PinnedRef.from_dict(data["component"]),
            events=tuple(str(event) for event in raw_events),
            capabilities=tuple(str(capability) for capability in raw_caps),
            schema=str(data.get("schema", PROVIDER_SCHEMA)),
        )


def requires_operator_approval(provider: Provider) -> bool:
    """True iff provisioning this provider requires explicit operator approval.

    Local is the default and self-serve (Law 5); an explicit cloud deployment is
    always an operator decision.
    """
    return provider.is_cloud()
