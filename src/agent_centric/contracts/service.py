"""Service contract (versioned) — ``service.v1`` (SPEC-0018 §3).

A **service** is the signed, content-addressed **host-state manifest**: it binds
a host to a **pinned** definition, declares the secret **references** (never
values) the host consumes, a health descriptor, and the **named task bindings**
the pull/apply agent invokes. It is deliberately *not* a node kind: a service is
a manifest over components/wiring, not a component itself.

Invariants enforced here (all fail-closed):

- every reference is **pinned** — an immutable ``ref`` plus a sha256 ``digest``;
  there are **no floating versions** (SPEC-0018 acceptance criteria);
- secrets are **references only**: a ``service.v1`` never carries secret material
  (a parsing input that tries to smuggle a ``value``/``secret``/``plaintext`` key
  is rejected);
- the document is canonical and content-hashable (``service_hash`` = sha256 of
  its canonical bytes), so the agent can verify hash + signature before applying
  (verify-then-apply, SPEC-0018 §6).

:func:`validate_service` is the deterministic validation entrypoint: it returns a
:class:`ServiceReport` (it never raises on content), so a UI or the agent can
surface every error. Contracts remain **additive-only**.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

SERVICE_SCHEMA = "service.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A secret *reference* must never carry material. These keys are rejected on
# parse so a hand-written manifest cannot smuggle a value past the contract.
_FORBIDDEN_SECRET_KEYS = ("value", "secret", "plaintext", "data", "material")


class ServiceVersion:
    """The supported service contract version."""

    V1 = "service.v1"

    __slots__ = ()


class ServiceTask:
    """The canonical task roles a ``service.v1`` binds (SPEC-0018 §2)."""

    APPLY = "apply"
    HEALTH = "health"
    ROLLBACK = "rollback"
    BACKUP = "backup"
    RESTORE = "restore"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.APPLY, cls.HEALTH, cls.ROLLBACK, cls.BACKUP, cls.RESTORE)


class TaskKind:
    """How a named task is invoked (SPEC-0018 §2)."""

    SCRIPT = "script"
    CALLABLE = "callable"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.SCRIPT, cls.CALLABLE)


@dataclass(frozen=True)
class PinnedRef:
    """An immutable reference to content, pinned by an sha256 digest.

    ``ref`` is origin-agnostic (a commit sha, an OCI/digest ref, or a kebab
    identifier); ``digest`` is the 64-hex sha256 of the referenced content. A
    ``PinnedRef`` can never float: the digest is mandatory.
    """

    ref: str
    digest: str

    def __post_init__(self) -> None:
        if not self.ref:
            raise ValueError("PinnedRef ref must be non-empty.")
        if not _SHA256_RE.match(self.digest):
            raise ValueError(f"PinnedRef digest must be 64-hex sha256: {self.digest!r}")

    def to_dict(self) -> dict[str, str]:
        return {"ref": self.ref, "digest": self.digest}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PinnedRef:
        return cls(ref=str(data["ref"]), digest=str(data["digest"]))


@dataclass(frozen=True)
class ServiceRefs:
    """The pinned references a service binds.

    Attributes:
        host_definition: The pinned host definition (NixOS flake commit + digest).
        composition: An optional ``components.lock`` hash (64-hex) the service
            binds; empty when the first cut binds only the bootstrap host
            definition (SPEC-0018 §3).
    """

    host_definition: PinnedRef
    composition: str = ""

    def __post_init__(self) -> None:
        if self.composition and not _SHA256_RE.match(self.composition):
            raise ValueError(
                f"ServiceRefs composition must be a 64-hex lock hash: {self.composition!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"host_definition": self.host_definition.to_dict()}
        if self.composition:
            data["composition"] = self.composition
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceRefs:
        return cls(
            host_definition=PinnedRef.from_dict(data["host_definition"]),
            composition=str(data.get("composition", "")),
        )


@dataclass(frozen=True)
class TaskBinding:
    """A named, deterministically invocable task (SPEC-0018 §2).

    A task is either an **executable script** or a **callable**. It declares its
    idempotency: a task that is **not** idempotent must be explicitly ``gated``
    (``validate_service`` refuses otherwise), because nothing non-idempotent may
    be applied unattended.

    ``digest`` optionally **content-pins** the task: the 64-hex sha256 of the
    script/callable bytes. The pull/apply agent re-hashes the task on disk and
    refuses to run it on a mismatch (verify-then-apply). It is omitted from the
    canonical bytes when empty, so this field is strictly additive.
    """

    name: str
    target: str
    kind: str = TaskKind.SCRIPT
    mutating: bool = False
    idempotent: bool = True
    gated: bool = False
    args: tuple[str, ...] = ()
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("TaskBinding name must be non-empty.")
        if not self.target:
            raise ValueError("TaskBinding target must be non-empty.")
        if any(ch.isspace() for ch in self.target):
            raise ValueError("TaskBinding target must be a single addressable name.")
        if self.kind not in TaskKind.all():
            raise ValueError(f"Unsupported task kind: {self.kind!r}")
        if self.digest and not _SHA256_RE.match(self.digest):
            raise ValueError(f"TaskBinding digest must be 64-hex sha256: {self.digest!r}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "target": self.target,
            "kind": self.kind,
            "mutating": self.mutating,
            "idempotent": self.idempotent,
            "gated": self.gated,
            "args": list(self.args),
        }
        # Omitted when empty so the canonical bytes (and thus service_hash) of a
        # manifest that does not pin a task are unchanged (additive-only).
        if self.digest:
            data["digest"] = self.digest
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskBinding:
        return cls(
            name=str(data["name"]),
            target=str(data["target"]),
            kind=str(data.get("kind", TaskKind.SCRIPT)),
            mutating=bool(data.get("mutating", False)),
            idempotent=bool(data.get("idempotent", True)),
            gated=bool(data.get("gated", False)),
            args=tuple(str(x) for x in data.get("args", ())),
            digest=str(data.get("digest", "")),
        )


@dataclass(frozen=True)
class SecretRef:
    """A reference to a secret in the secret store — never the secret itself.

    Attributes:
        name: A logical, stable name (e.g. ``gitea-admin``).
        path: The store path (e.g. ``secret/<host>/gitea/admin``).
        key: An optional field within the secret; empty means the whole payload.
    """

    name: str
    path: str
    key: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SecretRef name must be non-empty.")
        if not self.path:
            raise ValueError("SecretRef path must be non-empty.")
        if any(ch.isspace() for ch in self.path):
            raise ValueError("SecretRef path must not contain whitespace.")

    def to_dict(self) -> dict[str, str]:
        data = {"name": self.name, "path": self.path}
        if self.key:
            data["key"] = self.key
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecretRef:
        if not isinstance(data, dict):
            raise ValueError("SecretRef must be an object.")
        smuggled = sorted(k for k in _FORBIDDEN_SECRET_KEYS if k in data)
        if smuggled:
            raise ValueError(
                "a service declares secret *references* only; remove "
                f"{smuggled} (never store secret material in a manifest)."
            )
        return cls(
            name=str(data["name"]),
            path=str(data["path"]),
            key=str(data.get("key", "")),
        )


@dataclass(frozen=True)
class ServiceHealth:
    """The deterministic readiness descriptor (SPEC-0018 §8).

    ``task`` names the health task binding that performs the check; ``path`` is an
    optional ``/healthz`` endpoint for the host runtime.
    """

    task: str = ServiceTask.HEALTH
    timeout_seconds: int = 30
    path: str = ""

    def __post_init__(self) -> None:
        if not self.task:
            raise ValueError("ServiceHealth task must be non-empty.")
        if self.timeout_seconds <= 0:
            raise ValueError("ServiceHealth timeout_seconds must be positive.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "timeout_seconds": self.timeout_seconds,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceHealth:
        return cls(
            task=str(data.get("task", ServiceTask.HEALTH)),
            timeout_seconds=int(data.get("timeout_seconds", 30)),
            path=str(data.get("path", "")),
        )


@dataclass(frozen=True)
class Service:
    """An immutable ``service.v1`` host-state manifest."""

    id: str
    host: str
    refs: ServiceRefs
    tasks: tuple[TaskBinding, ...] = ()
    secrets: tuple[SecretRef, ...] = ()
    health: ServiceHealth | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = SERVICE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SERVICE_SCHEMA:
            raise ValueError(f"Unsupported service schema: {self.schema!r}")
        if not self.id:
            raise ValueError("Service id must be non-empty.")
        if not self.host:
            raise ValueError("Service host must be non-empty.")
        task_names = [task.name for task in self.tasks]
        if len(task_names) != len(set(task_names)):
            raise ValueError("Service task names must be unique.")
        secret_names = [secret.name for secret in self.secrets]
        if len(secret_names) != len(set(secret_names)):
            raise ValueError("Service secret names must be unique.")
        if not isinstance(self.metadata, dict):
            raise ValueError("Service metadata must be a mapping.")
        # Canonical order: the manifest is content, not sequence. Normalizing at
        # construction makes equality and ``service_hash`` independent of the
        # declaration order a caller happened to use.
        object.__setattr__(
            self, "tasks", tuple(sorted(self.tasks, key=lambda task: task.name))
        )
        object.__setattr__(
            self, "secrets", tuple(sorted(self.secrets, key=lambda secret: secret.name))
        )

    def to_dict(self) -> dict[str, Any]:
        """Canonical, deterministic serialization (tasks/secrets are name-sorted)."""
        return {
            "schema": self.schema,
            "id": self.id,
            "host": self.host,
            "refs": self.refs.to_dict(),
            "tasks": [
                task.to_dict() for task in sorted(self.tasks, key=lambda t: t.name)
            ],
            "secrets": [
                secret.to_dict()
                for secret in sorted(self.secrets, key=lambda s: s.name)
            ],
            "health": self.health.to_dict() if self.health else None,
            "metadata": dict(self.metadata),
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for hashing/signing."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def service_hash(self) -> str:
        """The content address of this service (sha256, hex) — signed before apply."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def task(self, name: str) -> TaskBinding:
        """Return the task binding named ``name`` (fail-closed if absent)."""
        for binding in self.tasks:
            if binding.name == name:
                return binding
        raise ValueError(f"No service task named {name!r}.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Service:
        try:
            schema = str(data["schema"])
            service_id = str(data["id"])
            host = str(data["host"])
            refs_raw = data["refs"]
        except KeyError as exc:
            raise ValueError(f"Service missing field: {exc!r}") from exc
        if not isinstance(refs_raw, dict):
            raise ValueError("Service refs must be an object.")
        raw_tasks = data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            raise ValueError("Service tasks must be a list.")
        raw_secrets = data.get("secrets", [])
        if not isinstance(raw_secrets, list):
            raise ValueError("Service secrets must be a list.")
        health_raw = data.get("health")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("Service metadata must be a mapping.")
        return cls(
            id=service_id,
            host=host,
            refs=ServiceRefs.from_dict(refs_raw),
            tasks=tuple(TaskBinding.from_dict(t) for t in raw_tasks),
            secrets=tuple(SecretRef.from_dict(s) for s in raw_secrets),
            health=ServiceHealth.from_dict(health_raw) if health_raw else None,
            metadata=dict(metadata),
            schema=schema,
        )


@dataclass(frozen=True)
class ServiceReport:
    """The deterministic outcome of validating a service manifest."""

    ok: bool
    errors: tuple[str, ...]
    task_names: tuple[str, ...]


def validate_service(
    service: Service, *, required_tasks: tuple[str, ...] = (ServiceTask.APPLY, ServiceTask.HEALTH)
) -> ServiceReport:
    """Validate a service manifest deterministically and fail-closed.

    Checks that the canonical task roles are bound, that every task is idempotent
    or explicitly gated, that no two tasks share a name, that the health
    descriptor references a declared health task, and that every reference is
    pinned (the contract already enforces digests). It never raises on content —
    a caller (the agent) surfaces the report and refuses to apply on ``not ok``.

    Returns:
        A :class:`ServiceReport`; ``ok`` is true only when ``errors`` is empty.
    """
    errors: list[str] = []
    names = [task.name for task in service.tasks]
    task_names = tuple(sorted(names))
    if not service.tasks:
        errors.append("service declares no tasks")
    if len(names) != len(set(names)):
        errors.append("service task names must be unique")

    required = set(required_tasks)
    present = set(names)
    for role in sorted(required - present):
        errors.append(f"service is missing the required task role {role!r}")

    for task in service.tasks:
        if not task.idempotent and not task.gated:
            errors.append(
                f"task {task.name!r} is not idempotent and is not explicitly gated"
            )

    if service.health is not None:
        health_task = service.health.task
        if health_task not in present:
            errors.append(
                f"health references undeclared task {health_task!r}"
            )

    return ServiceReport(ok=not errors, errors=tuple(errors), task_names=task_names)
