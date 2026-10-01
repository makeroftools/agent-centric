"""Tests for the ``service.v1`` host-state manifest contract (SPEC-0018 §3).

Deterministic and offline: every reference is pinned, secrets are references
only, the document is content-hashable, and :func:`validate_service` reports
errors without raising.
"""

from __future__ import annotations

import pytest

from agent_centric.contracts.service import (
    SERVICE_SCHEMA,
    PinnedRef,
    SecretRef,
    Service,
    ServiceHealth,
    ServiceRefs,
    ServiceTask,
    ServiceVersion,
    TaskBinding,
    TaskKind,
    validate_service,
)

_DIGEST = "a" * 64
_LOCK = "b" * 64


def _tasks() -> tuple[TaskBinding, ...]:
    return (
        TaskBinding(name=ServiceTask.APPLY, target="apply-service.sh", mutating=True),
        TaskBinding(name=ServiceTask.HEALTH, target="check-host.sh"),
        TaskBinding(name=ServiceTask.ROLLBACK, target="apply-service.sh", mutating=True),
        TaskBinding(name=ServiceTask.BACKUP, target="backup.sh", mutating=True),
        TaskBinding(name=ServiceTask.RESTORE, target="restore.sh", mutating=True),
    )


def _service(**overrides: object) -> Service:
    base: dict[str, object] = {
        "id": "host-01-bootstrap",
        "host": "host-01",
        "refs": ServiceRefs(host_definition=PinnedRef(ref="c" * 40, digest=_DIGEST)),
        "tasks": _tasks(),
        "secrets": (SecretRef(name="gitea-admin", path="secret/host-01/gitea/admin"),),
        "health": ServiceHealth(task=ServiceTask.HEALTH, timeout_seconds=30, path="/healthz"),
        "metadata": {"stage": "bootstrap", "phase": 1},
    }
    base.update(overrides)
    return Service(**base)  # type: ignore[arg-type]


class TestPinnedRef:
    def test_valid(self) -> None:
        ref = PinnedRef(ref="deadbeef", digest=_DIGEST)
        assert ref.to_dict() == {"ref": "deadbeef", "digest": _DIGEST}
        assert PinnedRef.from_dict(ref.to_dict()) == ref

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"ref": "", "digest": _DIGEST},
            {"ref": "x", "digest": "short"},
            {"ref": "x", "digest": "A" * 64},
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            PinnedRef(**kwargs)


class TestServiceRefs:
    def test_composition_optional(self) -> None:
        refs = ServiceRefs(host_definition=PinnedRef(ref="x", digest=_DIGEST))
        assert refs.to_dict() == {"host_definition": {"ref": "x", "digest": _DIGEST}}

    def test_composition_must_be_a_lock_hash(self) -> None:
        with pytest.raises(ValueError):
            ServiceRefs(
                host_definition=PinnedRef(ref="x", digest=_DIGEST), composition="float"
            )

    def test_composition_round_trips(self) -> None:
        refs = ServiceRefs(
            host_definition=PinnedRef(ref="x", digest=_DIGEST), composition=_LOCK
        )
        assert ServiceRefs.from_dict(refs.to_dict()) == refs


class TestTaskBinding:
    def test_valid_defaults(self) -> None:
        task = TaskBinding(name="apply", target="apply-service.sh")
        assert task.kind == TaskKind.SCRIPT
        assert task.idempotent is True
        assert task.mutating is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"name": "", "target": "x"},
            {"name": "x", "target": ""},
            {"name": "x", "target": "has whitespace"},
            {"name": "x", "target": "y", "kind": "opaque"},
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            TaskBinding(**kwargs)

    def test_callable_kind_round_trips(self) -> None:
        task = TaskBinding(
            name="apply",
            target="infra.agent:apply",
            kind=TaskKind.CALLABLE,
            mutating=True,
            args=("--dry-run",),
        )
        assert TaskBinding.from_dict(task.to_dict()) == task


class TestSecretRef:
    def test_reference_only(self) -> None:
        ref = SecretRef(name="gitea-admin", path="secret/host-01/gitea/admin", key="token")
        assert SecretRef.from_dict(ref.to_dict()) == ref

    def test_value_fields_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            SecretRef.from_dict(
                {"name": "x", "path": "secret/x", "value": "hunter2"}
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"name": "", "path": "secret/x"},
            {"name": "x", "path": ""},
            {"name": "x", "path": "has space"},
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            SecretRef(**kwargs)


class TestServiceHealth:
    def test_defaults(self) -> None:
        health = ServiceHealth()
        assert health.task == ServiceTask.HEALTH
        assert health.timeout_seconds == 30

    def test_non_positive_timeout_rejected(self) -> None:
        with pytest.raises(ValueError):
            ServiceHealth(timeout_seconds=0)


class TestService:
    def test_schema_and_identity_required(self) -> None:
        with pytest.raises(ValueError):
            _service(schema="service.v2")
        with pytest.raises(ValueError):
            _service(id="")
        with pytest.raises(ValueError):
            _service(host="")

    def test_task_and_secret_names_unique(self) -> None:
        dup = (TaskBinding(name="apply", target="a"), TaskBinding(name="apply", target="b"))
        with pytest.raises(ValueError):
            _service(tasks=dup)
        with pytest.raises(ValueError):
            _service(
                secrets=(
                    SecretRef(name="s", path="secret/s"),
                    SecretRef(name="s", path="secret/s2"),
                )
            )

    def test_hash_is_order_independent(self) -> None:
        forward = _service(tasks=_tasks())
        reverse = _service(tasks=tuple(reversed(_tasks())))
        assert forward.service_hash() == reverse.service_hash()

    def test_hash_is_deterministic_and_64_hex(self) -> None:
        first = _service().service_hash()
        second = _service().service_hash()
        assert first == second
        assert len(first) == 64 and first == first.lower()

    def test_hash_changes_with_content(self) -> None:
        assert _service().service_hash() != _service(host="host-02").service_hash()

    def test_round_trips(self) -> None:
        service = _service()
        assert Service.from_dict(service.to_dict()) == service
        assert Service.from_dict(service.to_dict()).service_hash() == service.service_hash()

    def test_task_lookup_fails_closed(self) -> None:
        service = _service()
        assert service.task(ServiceTask.APPLY).target == "apply-service.sh"
        with pytest.raises(ValueError):
            service.task("nope")

    def test_schema_constant(self) -> None:
        assert SERVICE_SCHEMA == "service.v1"
        assert ServiceVersion.V1 == "service.v1"


class TestValidateService:
    def test_canonical_service_is_valid(self) -> None:
        report = validate_service(_service())
        assert report.ok is True
        assert report.errors == ()
        assert ServiceTask.APPLY in report.task_names

    def test_missing_required_task_is_reported(self) -> None:
        service = _service(tasks=(TaskBinding(name=ServiceTask.HEALTH, target="check-host.sh"),))
        report = validate_service(service)
        assert report.ok is False
        assert any("apply" in error for error in report.errors)

    def test_non_idempotent_ungated_task_is_refused(self) -> None:
        tasks = _tasks()[:-1] + (
            TaskBinding(name=ServiceTask.RESTORE, target="restore.sh", idempotent=False),
        )
        report = validate_service(_service(tasks=tasks))
        assert report.ok is False
        assert any("not idempotent" in error for error in report.errors)

    def test_non_idempotent_gated_task_is_allowed(self) -> None:
        tasks = _tasks()[:-1] + (
            TaskBinding(
                name=ServiceTask.RESTORE,
                target="restore.sh",
                idempotent=False,
                gated=True,
            ),
        )
        assert validate_service(_service(tasks=tasks)).ok is True

    def test_health_task_must_be_declared(self) -> None:
        service = _service(health=ServiceHealth(task="missing", timeout_seconds=5))
        report = validate_service(service)
        assert report.ok is False
        assert any("undeclared task" in error for error in report.errors)
