"""Tests for the ``provider.v1`` contract (SPEC-0020).

Deterministic and offline: ``plan`` is pure, every plan/step is content-pinned,
apply is verify-then-apply, reconciliation is a deterministic decision, the
notification boundary is emit-only, and secrets are references only.
"""

from __future__ import annotations

import pytest

from agent_centric.contracts.capability import Capability
from agent_centric.contracts.provider import (
    CLOUD_GRANT,
    NOTIFICATION_EMIT_CAPABILITY,
    PROVIDER_SCHEMA,
    NotificationBinding,
    NotificationEvent,
    ObservedResource,
    PlanAction,
    PlanStep,
    Provider,
    ProviderPlan,
    ProviderTarget,
    ProviderTask,
    ProviderVersion,
    ReconcileAction,
    ResourceSpec,
    assert_plan_appliable,
    assert_task_appliable,
    is_plan_pinned,
    plan_provider,
    reconcile_action,
    requires_operator_approval,
    validate_provider,
)
from agent_centric.contracts.service import PinnedRef, SecretRef, TaskBinding

_D = "a" * 64
_L = "b" * 64


def _tasks(*, digest: bool = True) -> tuple[TaskBinding, ...]:
    pinned = _D if digest else ""
    return (
        TaskBinding(name=ProviderTask.PLAN, target="plan"),
        TaskBinding(
            name=ProviderTask.APPLY,
            target="apply-service.sh",
            mutating=True,
            digest=pinned,
        ),
        TaskBinding(name=ProviderTask.HEALTH, target="check-host.sh"),
        TaskBinding(
            name=ProviderTask.TEARDOWN,
            target="teardown.sh",
            mutating=True,
            digest=pinned,
        ),
    )


def _provider(**overrides: object) -> Provider:
    base: dict[str, object] = {
        "id": "local-host",
        "target": ProviderTarget.LOCAL,
        "capabilities": (Capability(name="host.apply"),),
        "tasks": _tasks(),
        "secrets": (SecretRef(name="api", path="secret/provider/api"),),
        "grants": ("host.apply",),
        "metadata": {"region": "local"},
    }
    base.update(overrides)
    return Provider(**base)  # type: ignore[arg-type]


def _resource(kind: str = "db", name: str = "gitea", **spec: object) -> ResourceSpec:
    return ResourceSpec(kind=kind, name=name, spec=dict(spec))


class TestResourceSpec:
    def test_hash_is_deterministic_and_key_order_independent(self) -> None:
        a = ResourceSpec(kind="db", name="gitea", spec={"size": "small", "engine": "sqlite"})
        b = ResourceSpec(kind="db", name="gitea", spec={"engine": "sqlite", "size": "small"})
        assert a.resource_hash() == b.resource_hash()
        assert len(a.resource_hash()) == 64

    def test_distinct_content_has_distinct_address(self) -> None:
        assert _resource(size="small").resource_hash() != _resource(size="large").resource_hash()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"kind": "", "name": "n"},
            {"kind": "k", "name": ""},
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            ResourceSpec(spec={}, **kwargs)

    def test_round_trips(self) -> None:
        resource = _resource(size="small")
        assert ResourceSpec.from_dict(resource.to_dict()) == resource

    def test_secret_material_in_spec_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ResourceSpec.from_dict(
                {"kind": "db", "name": "gitea", "spec": {"value": "hunter2"}}
            )
        with pytest.raises(ValueError):
            ResourceSpec.from_dict(
                {"kind": "db", "name": "gitea", "spec": {"plaintext": "hunter2"}}
            )


class TestObservedResource:
    def test_valid(self) -> None:
        observed = ObservedResource(kind="db", name="gitea", resource_hash=_D)
        assert ObservedResource.from_dict(observed.to_dict()) == observed

    def test_hash_must_be_sha256(self) -> None:
        with pytest.raises(ValueError):
            ObservedResource(kind="db", name="gitea", resource_hash="short")


class TestPlanStep:
    def test_mutating_step_requires_a_digest(self) -> None:
        with pytest.raises(ValueError):
            PlanStep(action=PlanAction.CREATE, kind="db", name="gitea")

    def test_non_mutating_step_must_not_carry_a_digest(self) -> None:
        with pytest.raises(ValueError):
            PlanStep(action=PlanAction.NOOP, kind="db", name="gitea", digest=_D)

    def test_delete_requires_approval(self) -> None:
        with pytest.raises(ValueError):
            PlanStep(action=PlanAction.DELETE, kind="db", name="gitea", digest=_D)
        step = PlanStep(
            action=PlanAction.DELETE, kind="db", name="gitea", digest=_D, requires_approval=True
        )
        assert step.destructive is True
        assert step.mutating is True
        assert step.to_dict()["action"] == "delete"

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ValueError):
            PlanStep(action="destroy", kind="db", name="gitea", digest=_D)


class TestPlanProvider:
    def test_identical_desired_state_is_byte_identical(self) -> None:
        desired = (_resource(size="small"),)
        first = plan_provider("local-host", desired)
        second = plan_provider("local-host", desired)
        assert first.canonical_bytes() == second.canonical_bytes()
        assert first.plan_hash() == second.plan_hash()

    def test_declaration_order_does_not_matter(self) -> None:
        a = _resource(name="a")
        b = _resource(name="b")
        forward = plan_provider("p", (a, b))
        reverse = plan_provider("p", (b, a))
        assert forward.canonical_bytes() == reverse.canonical_bytes()

    def test_missing_resource_is_created(self) -> None:
        plan = plan_provider("p", (_resource(size="small"),))
        assert [step.action for step in plan.steps] == [PlanAction.CREATE]
        assert plan.steps[0].digest == _resource(size="small").resource_hash()
        assert plan.is_converged() is False

    def test_matching_observed_resource_converges(self) -> None:
        resource = _resource(size="small")
        observed = (
            ObservedResource(
                kind=resource.kind, name=resource.name, resource_hash=resource.resource_hash()
            ),
        )
        plan = plan_provider("p", (resource,), observed)
        assert plan.is_converged() is True
        assert plan.mutating_steps == ()
        assert plan.steps[0].action == PlanAction.NOOP

    def test_changed_resource_is_updated(self) -> None:
        observed = (ObservedResource(kind="db", name="gitea", resource_hash=_D),)
        plan = plan_provider("p", (_resource(size="small"),), observed)
        assert plan.steps[0].action == PlanAction.UPDATE
        assert plan.steps[0].digest == _resource(size="small").resource_hash()

    def test_extra_observed_resource_is_destructively_removed(self) -> None:
        observed = (ObservedResource(kind="db", name="ghost", resource_hash=_D),)
        plan = plan_provider("p", (), observed)
        assert plan.steps[0].action == PlanAction.DELETE
        assert plan.steps[0].requires_approval is True
        assert plan.steps[0].destructive is True

    def test_local_mutating_steps_need_no_approval(self) -> None:
        plan = plan_provider("p", (_resource(),), target=ProviderTarget.LOCAL)
        assert plan.steps[0].requires_approval is False

    def test_cloud_mutating_steps_require_approval(self) -> None:
        plan = plan_provider("p", (_resource(),), target=ProviderTarget.CLOUD)
        assert plan.steps[0].requires_approval is True

    def test_target_changes_the_plan_hash(self) -> None:
        desired = (_resource(),)
        local = plan_provider("p", desired, target=ProviderTarget.LOCAL)
        cloud = plan_provider("p", desired, target=ProviderTarget.CLOUD)
        assert local.plan_hash() != cloud.plan_hash()

    def test_plan_hash_is_64_hex(self) -> None:
        plan = plan_provider("p", (_resource(),))
        assert len(plan.plan_hash()) == 64 and plan.plan_hash() == plan.plan_hash().lower()

    def test_duplicate_desired_is_refused(self) -> None:
        with pytest.raises(ValueError):
            plan_provider("p", (_resource(), _resource()))

    def test_unknown_target_is_refused(self) -> None:
        with pytest.raises(ValueError):
            plan_provider("p", (_resource(),), target="datacenter")

    def test_round_trips_through_dict(self) -> None:
        plan = plan_provider("p", (_resource(),), (
            ObservedResource(kind="db", name="ghost", resource_hash=_D),
        ))
        restored = ProviderPlan.from_dict(plan.to_dict())
        assert restored == plan
        assert restored.plan_hash() == plan.plan_hash()


class TestApplyGates:
    def test_pinned_plan_matches(self) -> None:
        plan = plan_provider("p", (_resource(),))
        assert is_plan_pinned(plan, plan.plan_hash()) is True
        assert is_plan_pinned(plan, "0" * 64) is False

    def test_drifted_plan_is_refused(self) -> None:
        plan = plan_provider("p", (_resource(),))
        with pytest.raises(ValueError):
            assert_plan_appliable(plan, expected_hash="0" * 64)

    def test_unpinned_hash_is_refused(self) -> None:
        plan = plan_provider("p", (_resource(),))
        with pytest.raises(ValueError):
            assert_plan_appliable(plan, expected_hash="nope")

    def test_approved_destructive_step_is_allowed(self) -> None:
        observed = (ObservedResource(kind="db", name="ghost", resource_hash=_D),)
        plan = plan_provider("p", (), observed)
        with pytest.raises(ValueError):
            assert_plan_appliable(plan, expected_hash=plan.plan_hash())
        assert_plan_appliable(plan, expected_hash=plan.plan_hash(), approved=True)

    def test_unapproved_cloud_step_is_refused(self) -> None:
        plan = plan_provider("p", (_resource(),), target=ProviderTarget.CLOUD)
        with pytest.raises(ValueError):
            assert_plan_appliable(plan, expected_hash=plan.plan_hash())

    def test_task_gate_refuses_non_idempotent_ungated(self) -> None:
        with pytest.raises(ValueError):
            assert_task_appliable(TaskBinding(name="t", target="t.sh", idempotent=False))

    def test_task_gate_allows_explicitly_gated(self) -> None:
        assert_task_appliable(
            TaskBinding(name="t", target="t.sh", idempotent=False, gated=True)
        )

    def test_task_gate_refuses_unpinned_mutating_task(self) -> None:
        with pytest.raises(ValueError):
            assert_task_appliable(TaskBinding(name="t", target="t.sh", mutating=True))


class TestValidateProvider:
    def test_canonical_provider_is_valid(self) -> None:
        report = validate_provider(_provider())
        assert report.ok is True
        assert report.errors == ()
        assert ProviderTask.APPLY in report.task_names

    def test_missing_required_task_is_reported(self) -> None:
        report = validate_provider(
            _provider(tasks=(TaskBinding(name=ProviderTask.HEALTH, target="check-host.sh"),))
        )
        assert report.ok is False
        assert any("apply" in error for error in report.errors)

    def test_non_idempotent_ungated_task_is_refused(self) -> None:
        tasks = _tasks()[:-1] + (
            TaskBinding(name=ProviderTask.TEARDOWN, target="teardown.sh", idempotent=False),
        )
        report = validate_provider(_provider(tasks=tasks))
        assert report.ok is False
        assert any("not idempotent" in error for error in report.errors)

    def test_unpinned_mutating_task_is_refused(self) -> None:
        report = validate_provider(_provider(tasks=_tasks(digest=False)))
        assert report.ok is False
        assert any("content digest" in error for error in report.errors)

    def test_digests_are_optional_when_not_required(self) -> None:
        assert validate_provider(
            _provider(tasks=_tasks(digest=False)), require_task_digests=False
        ).ok is True

    def test_cloud_without_grant_is_refused(self) -> None:
        report = validate_provider(_provider(target=ProviderTarget.CLOUD))
        assert report.ok is False
        assert any(CLOUD_GRANT in error for error in report.errors)

    def test_cloud_with_grant_is_valid(self) -> None:
        report = validate_provider(
            _provider(target=ProviderTarget.CLOUD, grants=(CLOUD_GRANT,))
        )
        assert report.ok is True


class TestReconcileAction:
    def test_converged_performs_no_mutating_call(self) -> None:
        assert reconcile_action(converged=True) == ReconcileAction.CONVERGED

    def test_healthy_non_converged_applies(self) -> None:
        assert reconcile_action(converged=False, health_ok=True) == ReconcileAction.APPLY

    def test_failed_health_rolls_back_when_possible(self) -> None:
        action = reconcile_action(
            converged=False, health_ok=False, rollback_available=True, attempts=0, max_attempts=3
        )
        assert action == ReconcileAction.ROLLBACK

    def test_failed_health_without_a_healthy_plan_escalates(self) -> None:
        action = reconcile_action(converged=False, health_ok=False, rollback_available=False)
        assert action == ReconcileAction.ESCALATE

    def test_repeated_failure_escalates_and_stops(self) -> None:
        action = reconcile_action(
            converged=False,
            health_ok=False,
            rollback_available=True,
            attempts=3,
            max_attempts=3,
        )
        assert action == ReconcileAction.ESCALATE

    def test_bad_attempt_counters_are_refused(self) -> None:
        with pytest.raises(ValueError):
            reconcile_action(converged=False, attempts=-1)
        with pytest.raises(ValueError):
            reconcile_action(converged=False, max_attempts=0)
        with pytest.raises(ValueError):
            reconcile_action(converged=False, attempts=True)  # type: ignore[arg-type]


class TestNotificationBinding:
    def _binding(self, **overrides: object) -> NotificationBinding:
        base: dict[str, object] = {"component": PinnedRef(ref="notify", digest=_D)}
        base.update(overrides)
        return NotificationBinding(**base)  # type: ignore[arg-type]

    def test_defaults_are_emit_only_and_complete(self) -> None:
        binding = self._binding()
        assert binding.events == NotificationEvent.all()
        assert binding.capabilities == (NOTIFICATION_EMIT_CAPABILITY,)

    def test_cannot_hold_a_mutating_capability(self) -> None:
        with pytest.raises(ValueError):
            self._binding(capabilities=("infra.mutate",))

    def test_unknown_event_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._binding(events=("exploded",))

    def test_empty_events_rejected(self) -> None:
        with pytest.raises(ValueError):
            self._binding(events=())

    def test_round_trips(self) -> None:
        binding = self._binding()
        assert NotificationBinding.from_dict(binding.to_dict()) == binding

    def test_holds_no_secret_of_its_own(self) -> None:
        assert not hasattr(self._binding(), "secrets")
        assert not hasattr(self._binding(), "grants")


class TestProvider:
    def test_schema_and_identity_required(self) -> None:
        with pytest.raises(ValueError):
            _provider(schema="provider.v2")
        with pytest.raises(ValueError):
            _provider(id="")
        with pytest.raises(ValueError):
            _provider(target="datacenter")

    def test_local_is_the_default(self) -> None:
        provider = Provider(id="p")
        assert provider.target == ProviderTarget.LOCAL
        assert requires_operator_approval(provider) is False

    def test_cloud_requires_operator_approval(self) -> None:
        provider = _provider(target=ProviderTarget.CLOUD, grants=(CLOUD_GRANT,))
        assert requires_operator_approval(provider) is True

    def test_names_must_be_unique(self) -> None:
        with pytest.raises(ValueError):
            _provider(
                tasks=(
                    TaskBinding(name="apply", target="a"),
                    TaskBinding(name="apply", target="b"),
                )
            )
        with pytest.raises(ValueError):
            _provider(
                secrets=(
                    SecretRef(name="s", path="secret/s"),
                    SecretRef(name="s", path="secret/s2"),
                )
            )

    def test_hash_is_order_independent_and_deterministic(self) -> None:
        forward = _provider(tasks=_tasks())
        reverse = _provider(tasks=tuple(reversed(_tasks())))
        assert forward.provider_hash() == reverse.provider_hash()
        assert forward.provider_hash() == _provider().provider_hash()

    def test_round_trips(self) -> None:
        provider = _provider()
        assert Provider.from_dict(provider.to_dict()) == provider
        assert Provider.from_dict(provider.to_dict()).provider_hash() == provider.provider_hash()

    def test_task_lookup_fails_closed(self) -> None:
        assert _provider().task(ProviderTask.APPLY).target == "apply-service.sh"
        with pytest.raises(ValueError):
            _provider().task("nope")

    def test_secret_values_are_rejected_on_parse(self) -> None:
        with pytest.raises(ValueError):
            Provider.from_dict(
                {
                    "id": "p",
                    "secrets": [{"name": "s", "path": "secret/s", "value": "hunter2"}],
                }
            )

    def test_constants(self) -> None:
        assert PROVIDER_SCHEMA == "provider.v1"
        assert ProviderVersion.V1 == "provider.v1"
        assert ProviderTarget.all() == ("local", "cloud")
        assert set(ProviderTask.all()) == {"plan", "apply", "health", "teardown"}
        assert set(ReconcileAction.all()) == {"converged", "apply", "rollback", "escalate"}
