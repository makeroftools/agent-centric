"""Tests for the provider hosting runtime (``cbp/provider_runtime.py``; SPEC-0020).

Deterministic and offline: the provider I/O is behind an injected adapter, every
call is recorded once (idempotent evidence), apply is verify-then-apply, and the
reconcile loop rolls back or escalates fail-closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_centric.cbp import (
    InMemoryProviderAdapter,
    ProviderCall,
    ProviderCallKind,
    ProviderEvidence,
    ProviderLedger,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeError,
    emit_notification,
)
from agent_centric.contracts.capability import Capability
from agent_centric.contracts.provider import (
    CLOUD_GRANT,
    PROVIDER_SCHEMA,
    NotificationBinding,
    NotificationEvent,
    ObservedResource,
    PlanAction,
    Provider,
    ProviderTarget,
    ProviderTask,
    ReconcileAction,
    ResourceSpec,
    plan_provider,
)
from agent_centric.contracts.service import PinnedRef, SecretRef, TaskBinding

_D = "a" * 64
_L = "b" * 64


def _tasks() -> tuple[TaskBinding, ...]:
    return (
        TaskBinding(name=ProviderTask.PLAN, target="plan"),
        TaskBinding(name=ProviderTask.APPLY, target="apply.sh", mutating=True, digest=_D),
        TaskBinding(name=ProviderTask.HEALTH, target="health.sh"),
        TaskBinding(
            name=ProviderTask.TEARDOWN, target="teardown.sh", mutating=True, digest=_D
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
    }
    base.update(overrides)
    return Provider(**base)  # type: ignore[arg-type]


def _resource(name: str = "gitea", **spec: object) -> ResourceSpec:
    return ResourceSpec(kind="db", name=name, spec=dict(spec))


class _ScriptedAdapter:
    """A controllable offline adapter (models observed state; poisonable health)."""

    def __init__(self, *, poison: frozenset[tuple[str, str]] = frozenset()) -> None:
        self.state: dict[tuple[str, str], str] = {}
        self.poison = poison
        self.calls: list[ProviderCall] = []

    def call(self, call: ProviderCall) -> ProviderResult:
        self.calls.append(call)
        if call.op == ProviderCallKind.OBSERVE:
            observed = tuple(
                ObservedResource(kind=kind, name=name, resource_hash=digest)
                for (kind, name), digest in sorted(self.state.items())
            )
            return ProviderResult(ok=True, observed=observed)
        if call.op == ProviderCallKind.APPLY:
            if call.change == PlanAction.DELETE:
                self.state.pop((call.kind, call.name), None)
            else:
                self.state[(call.kind, call.name)] = call.digest
            return ProviderResult(ok=True)
        if call.op == ProviderCallKind.HEALTH:
            healthy = not any(key in self.poison for key in self.state)
            return ProviderResult(ok=healthy)
        if call.op == ProviderCallKind.TEARDOWN:
            self.state.clear()
            return ProviderResult(ok=True)
        return ProviderResult(ok=False, detail=f"unknown op {call.op!r}")

    def observed(self) -> tuple[ObservedResource, ...]:
        return tuple(
            ObservedResource(kind=kind, name=name, resource_hash=digest)
            for (kind, name), digest in sorted(self.state.items())
        )

    def mutate_calls(self) -> list[ProviderCall]:
        return [c for c in self.calls if c.op == ProviderCallKind.APPLY]


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))


class TestProviderLedger:
    def test_append_is_idempotent_by_fingerprint(self, tmp_path: Path) -> None:
        ledger = ProviderLedger(tmp_path / "evidence.jsonl")
        evidence = ProviderEvidence(action="observe", call_hash=_D, provider="p")
        assert ledger.append(evidence) is True
        assert ledger.append(evidence) is False
        assert ledger.count() == 1

    def test_entries_round_trip(self, tmp_path: Path) -> None:
        ledger = ProviderLedger(tmp_path / "evidence.jsonl")
        evidence = ProviderEvidence(
            action="apply",
            call_hash=_D,
            provider="p",
            plan_hash=_L,
            ok=True,
            detail="created",
            plan={"schema": PROVIDER_SCHEMA},
            desired=[_resource().to_dict()],
        )
        ledger.append(evidence)
        assert ledger.entries() == (evidence,)

    def test_persists_across_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.jsonl"
        ProviderLedger(path).append(
            ProviderEvidence(action="health", call_hash=_D, provider="p")
        )
        assert ProviderLedger(path).count() == 1

    def test_has_ok_and_last_healthy_desired(self, tmp_path: Path) -> None:
        ledger = ProviderLedger(tmp_path / "evidence.jsonl")
        ledger.append(
            ProviderEvidence(
                action="apply", call_hash=_D, provider="p", plan_hash=_L, ok=True
            )
        )
        assert ledger.has_ok(_D, _L) is True
        assert ledger.has_ok(_D, "0" * 64) is False
        assert ledger.last_healthy_desired() is None
        ledger.append(
            ProviderEvidence(
                action="healthy",
                call_hash=_digestish("h"),
                provider="p",
                plan_hash=_L,
                desired=[_resource(name="a").to_dict()],
            )
        )
        desired = ledger.last_healthy_desired()
        assert desired is not None and desired[0].name == "a"

    def test_evidence_rejects_unknown_action(self) -> None:
        with pytest.raises(ValueError):
            ProviderEvidence(action="teleport", call_hash=_D, provider="p")


def _digestish(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


class TestProviderCall:
    def test_apply_requires_a_pin_and_mutating_change(self) -> None:
        with pytest.raises(ValueError):
            ProviderCall(
                op=ProviderCallKind.APPLY, provider="p", kind="db", name="a"
            )
        with pytest.raises(ValueError):
            ProviderCall(
                op=ProviderCallKind.APPLY,
                provider="p",
                kind="db",
                name="a",
                digest=_D,
                change=PlanAction.NOOP,
            )
        call = ProviderCall(
            op=ProviderCallKind.APPLY,
            provider="p",
            kind="db",
            name="a",
            digest=_D,
            change=PlanAction.CREATE,
        )
        assert call.call_hash() == call.call_hash()

    def test_non_apply_calls_reject_resource_fields(self) -> None:
        with pytest.raises(ValueError):
            ProviderCall(op=ProviderCallKind.OBSERVE, provider="p", kind="db")

    def test_call_hash_is_field_sensitive(self) -> None:
        a = ProviderCall(op=ProviderCallKind.HEALTH, provider="p")
        b = ProviderCall(op=ProviderCallKind.HEALTH, provider="q")
        assert a.call_hash() != b.call_hash()


class TestApplyGates:
    def _runtime(self, tmp_path: Path, adapter: _ScriptedAdapter) -> ProviderRuntime:
        return ProviderRuntime(_provider(), adapter, ledger=ProviderLedger(tmp_path / "e"))

    def test_apply_verifies_the_pin_before_any_call(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        runtime = self._runtime(tmp_path, adapter)
        plan = plan_provider("local-host", (_resource(),))
        with pytest.raises(ValueError):
            runtime.apply(plan, expected_hash="0" * 64)
        assert adapter.calls == []

    def test_observe_and_health_are_recorded(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        runtime = self._runtime(tmp_path, adapter)
        assert runtime.observe() == ()
        assert runtime.health() is True
        actions = [entry.action for entry in runtime.ledger.entries()]
        assert actions == ["observe", "health"]

    def test_teardown_clears_state(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        adapter.state[("db", "a")] = _D
        runtime = self._runtime(tmp_path, adapter)
        assert runtime.teardown() is True
        assert adapter.observed() == ()


class TestReconcile:
    def _runtime(
        self, tmp_path: Path, adapter: _ScriptedAdapter, *, max_attempts: int = 3
    ) -> ProviderRuntime:
        return ProviderRuntime(
            _provider(),
            adapter,
            ledger=ProviderLedger(tmp_path / "evidence.jsonl"),
            max_attempts=max_attempts,
        )

    def test_fresh_provision_converges(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        runtime = self._runtime(tmp_path, adapter)
        result = runtime.reconcile((_resource(size="small"),))
        assert result.action == ReconcileAction.CONVERGED
        assert adapter.observed()[0].resource_hash == _resource(size="small").resource_hash()
        assert runtime.ledger.count() > 0

    def test_already_converged_makes_no_mutating_call(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        runtime = self._runtime(tmp_path, adapter)
        runtime.reconcile((_resource(),))
        before = len(adapter.mutate_calls())
        result = runtime.reconcile((_resource(),))
        assert result.action == ReconcileAction.CONVERGED
        assert len(adapter.mutate_calls()) == before

    def test_drift_is_repaired(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        runtime = self._runtime(tmp_path, adapter)
        runtime.reconcile((_resource(size="small"),))
        result = runtime.reconcile((_resource(size="large"),))
        assert result.action == ReconcileAction.CONVERGED
        assert adapter.observed()[0].resource_hash == _resource(size="large").resource_hash()

    def test_cloud_requires_operator_approval(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        provider = _provider(target=ProviderTarget.CLOUD, grants=(CLOUD_GRANT,))
        runtime = ProviderRuntime(
            provider, adapter, ledger=ProviderLedger(tmp_path / "evidence.jsonl")
        )
        with pytest.raises(ValueError):
            runtime.reconcile((_resource(),))
        result = runtime.reconcile((_resource(),), approved=True)
        assert result.action == ReconcileAction.CONVERGED

    def test_failed_health_rolls_back_then_escalates(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        runtime = self._runtime(tmp_path, adapter, max_attempts=2)
        healthy = _resource(name="a")
        assert runtime.reconcile((healthy,)).action == ReconcileAction.CONVERGED

        # A new desired resource poisons the health gate: applying it fails health,
        # so the runtime rolls back to the last healthy desired state (removing the
        # drift) and, when the change fails again, escalates.
        adapter.poison = frozenset({("db", "b")})
        poisoned = (_resource(name="a"), _resource(name="b"))
        result = runtime.reconcile(poisoned)
        assert result.action == ReconcileAction.ESCALATE
        actions = [entry.action for entry in runtime.ledger.entries()]
        assert "rolled-back" in actions
        assert "escalated" in actions
        # The rollback removed the poisoned resource.
        assert all(name != "b" for name, _ in adapter.state)

    def test_notifications_fire_on_transitions(self, tmp_path: Path) -> None:
        adapter = _ScriptedAdapter()
        sink = _RecordingSink()
        runtime = ProviderRuntime(
            _provider(),
            adapter,
            ledger=ProviderLedger(tmp_path / "evidence.jsonl"),
            notifier=sink,
        )
        runtime.reconcile((_resource(),))
        assert [event for event, _ in sink.events] == [NotificationEvent.PROVISIONED]
        runtime.reconcile((_resource(),))
        assert sink.events[-1][0] == NotificationEvent.HEALED


class TestNotificationBoundary:
    def test_emit_is_validated_against_the_binding(self) -> None:
        sink = _RecordingSink()
        binding = NotificationBinding(
            component=PinnedRef(ref="notify", digest=_D),
            events=(NotificationEvent.PROVISIONED,),
        )
        emit_notification(binding, sink, NotificationEvent.PROVISIONED, {"x": 1})
        assert sink.events[0][0] == NotificationEvent.PROVISIONED
        with pytest.raises(ProviderRuntimeError):
            emit_notification(binding, sink, NotificationEvent.ESCALATED, {})


class TestInMemoryAdapter:
    def test_round_trips_observed_state(self) -> None:
        adapter = InMemoryProviderAdapter()
        resource = _resource()
        adapter.seed(
            (
                ObservedResource(
                    kind=resource.kind,
                    name=resource.name,
                    resource_hash=resource.resource_hash(),
                ),
            )
        )
        assert adapter.observed()[0].resource_hash == resource.resource_hash()
