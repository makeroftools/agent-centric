"""Provider runtime (SPEC-0020 §2/§3) — the deterministic hosting engine.

This is the **runtime realization** of ``provider.v1``. The pure contract
(:mod:`agent_centric.contracts.provider`) decides *what* the plan is and whether
it may be applied; this module confines the provider's network I/O behind an
injected, typed :class:`ProviderAdapter`, records every request/response in an
append-only, idempotent :class:`ProviderLedger`, and drives the reconcile loop:

    plan -> verify -> apply -> health

Fail-closed by construction:

- Nothing is applied before its plan is pinned and verified
  (``assert_plan_appliable``); a pinned step whose evidence is already recorded
  is **not re-sent** — a retry never duplicates evidence or a mutating call.
- The provider's own success claim is never trusted: observed state is recorded
  and a separate **health gate** decides whether the change counts.
- A failed health gate triggers **deterministic auto-rollback** to the last
  healthy pinned plan, re-derived from the durable evidence; a repeated failure
  **escalates and stops** (never a partial or ambiguous success).

Notification is **not** core logic: an injected, emit-only
:class:`NotificationSink` receives transition events, validated against the bound
:class:`~agent_centric.contracts.provider.NotificationBinding` (which may hold
only ``notify.emit``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

from ..contracts.provider import (
    NotificationBinding,
    NotificationEvent,
    ObservedResource,
    PlanAction,
    Provider,
    ProviderPlan,
    ReconcileAction,
    ResourceSpec,
    assert_plan_appliable,
    plan_provider,
    reconcile_action,
)

PROVIDER_EVIDENCE_SCHEMA = "provider.evidence/v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProviderRuntimeError(RuntimeError):
    """A provider runtime operation violated the contract (fail-closed)."""


class ProviderCallKind:
    """The kind of a provider API call (the confined I/O boundary)."""

    OBSERVE = "observe"
    APPLY = "apply"
    HEALTH = "health"
    TEARDOWN = "teardown"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.OBSERVE, cls.APPLY, cls.HEALTH, cls.TEARDOWN)


#: Every action an evidence record may carry: the provider calls plus the
#: deterministic runtime transitions.
PROVIDER_EVIDENCE_ACTIONS = (
    *ProviderCallKind.all(),
    "converged",
    "healthy",
    "rolled-back",
    "escalated",
)


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderCall:
    """One typed provider API call: the only place the runtime touches the wire.

    ``op`` is the call kind; ``change`` is the :class:`PlanAction` for an
    ``apply`` call (so an adapter can tell a create/update from a delete), and is
    empty for the other calls. Every ``apply`` call is content-pinned by
    ``digest``; a call with no digest is refused at construction.
    """

    op: str
    provider: str
    kind: str = ""
    name: str = ""
    digest: str = ""
    change: str = ""

    def __post_init__(self) -> None:
        if self.op not in ProviderCallKind.all():
            raise ValueError(f"Unsupported provider call op: {self.op!r}")
        if not self.provider:
            raise ValueError("ProviderCall provider must be non-empty.")
        if self.op == ProviderCallKind.APPLY:
            if not self.kind or not self.name:
                raise ValueError("an apply call must name a resource (kind/name).")
            if not _SHA256_RE.match(self.digest):
                raise ValueError(
                    f"an apply call must carry a 64-hex digest: {self.digest!r}"
                )
            if self.change not in PlanAction.all() or not PlanAction.is_mutating(
                self.change
            ):
                raise ValueError(
                    f"an apply call must declare a mutating change: {self.change!r}"
                )
        elif self.kind or self.name or self.digest or self.change:
            raise ValueError(
                f"a {self.op} call must not carry resource fields (fail-closed)."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "provider": self.provider,
            "kind": self.kind,
            "name": self.name,
            "digest": self.digest,
            "change": self.change,
        }

    def call_hash(self) -> str:
        """The content address of this request (stable across retries)."""
        return hashlib.sha256(_canonical_bytes(self.to_dict())).hexdigest()


@dataclass(frozen=True)
class ProviderResult:
    """The (untrusted) response of one provider call."""

    ok: bool
    observed: tuple[ObservedResource, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise ValueError("ProviderResult ok must be a boolean.")
        if not isinstance(self.detail, str):
            raise ValueError("ProviderResult detail must be a string.")


class ProviderAdapter(Protocol):
    """The injected, typed provider API adapter (the only I/O seam)."""

    def call(self, call: ProviderCall) -> ProviderResult: ...


@dataclass(frozen=True)
class ProviderEvidence:
    """One immutable, content-addressed evidence record (write-once)."""

    action: str
    call_hash: str
    provider: str
    plan_hash: str = ""
    ok: bool = True
    detail: str = ""
    plan: dict[str, Any] | None = None
    desired: list[dict[str, Any]] | None = None
    schema: str = PROVIDER_EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROVIDER_EVIDENCE_SCHEMA:
            raise ValueError(f"Unsupported evidence schema: {self.schema!r}")
        if self.action not in PROVIDER_EVIDENCE_ACTIONS:
            raise ValueError(f"Unsupported evidence action: {self.action!r}")
        if not self.call_hash:
            raise ValueError("ProviderEvidence call_hash must be non-empty.")
        if not self.provider:
            raise ValueError("ProviderEvidence provider must be non-empty.")
        if self.plan is not None and not isinstance(self.plan, dict):
            raise ValueError("ProviderEvidence plan must be a mapping or null.")
        if self.desired is not None and not isinstance(self.desired, list):
            raise ValueError("ProviderEvidence desired must be a list or null.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "action": self.action,
            "call_hash": self.call_hash,
            "provider": self.provider,
            "plan_hash": self.plan_hash,
            "ok": self.ok,
            "detail": self.detail,
            "plan": self.plan,
            "desired": self.desired,
        }

    def fingerprint(self) -> str:
        """The content address of this evidence (idempotency key)."""
        return hashlib.sha256(_canonical_bytes(self.to_dict())).hexdigest()

    def to_line_dict(self) -> dict[str, Any]:
        return {**self.to_dict(), "fingerprint": self.fingerprint()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderEvidence:
        return cls(
            action=str(data["action"]),
            call_hash=str(data["call_hash"]),
            provider=str(data["provider"]),
            plan_hash=str(data.get("plan_hash", "")),
            ok=bool(data.get("ok", True)),
            detail=str(data.get("detail", "")),
            plan=data.get("plan"),
            desired=data.get("desired"),
            schema=str(data.get("schema", PROVIDER_EVIDENCE_SCHEMA)),
        )


def _lock_file(handle: TextIO) -> None:
    """Take an exclusive advisory lock (POSIX); a no-op where unavailable."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: TextIO) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ProviderLedger:
    """An append-only, idempotent, durable evidence log backed by a JSONL file.

    An append with the same fingerprint is a no-op, so a retried provider call
    never duplicates evidence (Law 10). The file is advisory-locked and
    ``fsync``-ed, and materialized ``0600``.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, evidence: ProviderEvidence) -> bool:
        """Append ``evidence`` idempotently; return True iff it was new."""
        fingerprint = evidence.fingerprint()
        line = json.dumps(evidence.to_line_dict(), sort_keys=True, separators=(",", ":"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a+", encoding="utf-8") as handle:
            _lock_file(handle)
            try:
                handle.seek(0)
                for existing in handle.read().splitlines():
                    if not existing:
                        continue
                    if json.loads(existing).get("fingerprint") == fingerprint:
                        return False
                handle.seek(0, os.SEEK_END)
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                _unlock_file(handle)
        os.chmod(self._path, 0o600)
        return True

    def entries(self) -> tuple[ProviderEvidence, ...]:
        if not self._path.exists():
            return ()
        out: list[ProviderEvidence] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(ProviderEvidence.from_dict(json.loads(line)))
        return tuple(out)

    def count(self) -> int:
        return len(self.entries())

    def has_ok(self, call_hash: str, plan_hash: str) -> bool:
        """True iff a successful ``apply`` evidence exists for this call+plan."""
        for entry in self.entries():
            if (
                entry.action == ProviderCallKind.APPLY
                and entry.ok
                and entry.call_hash == call_hash
                and entry.plan_hash == plan_hash
            ):
                return True
        return False

    def last_healthy_desired(self) -> tuple[ResourceSpec, ...] | None:
        """The desired state of the most recently recorded healthy plan, if any.

        Rollback re-plans toward this state from the *current* observed state, so
        drift introduced by the failed change is removed rather than left behind.
        """
        for entry in reversed(self.entries()):
            if entry.action == "healthy" and entry.desired is not None:
                return tuple(ResourceSpec.from_dict(item) for item in entry.desired)
        return None


class NotificationSink(Protocol):
    """The emit-only boundary a notification component implements."""

    def emit(self, event: str, payload: dict[str, Any]) -> None: ...


class NullNotificationSink:
    """An emit-only sink that drops events (the offline default)."""

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        return None


def emit_notification(
    binding: NotificationBinding,
    sink: NotificationSink,
    event: str,
    payload: dict[str, Any],
) -> None:
    """Emit ``event`` through ``sink``, validated against the bound component.

    Fail-closed: an event the binding does not observe is refused rather than
    silently delivered.
    """
    if event not in binding.events:
        raise ProviderRuntimeError(
            f"notification event {event!r} is not bound to this component."
        )
    sink.emit(event, payload)


@dataclass(frozen=True)
class ReconcileResult:
    """The deterministic outcome of one full reconcile run."""

    action: str
    plan: ProviderPlan
    attempts: int

    def __post_init__(self) -> None:
        if self.action not in ReconcileAction.all():
            raise ValueError(f"Unsupported reconcile action: {self.action!r}")
        if self.attempts < 0:
            raise ValueError("ReconcileResult attempts must not be negative.")


class InMemoryProviderAdapter:
    """A deterministic, offline reference adapter (no network).

    It models observed state as a mapping of ``(kind, name) -> resource_hash`` and
    reports health. It is the test double for the runtime and proves the loop
    end to end; a real adapter confines actual provider I/O behind the same
    :class:`ProviderAdapter` seam.
    """

    def __init__(
        self,
        *,
        fail_ops: frozenset[str] = frozenset(),
        healthy: bool = True,
    ) -> None:
        self._state: dict[tuple[str, str], str] = {}
        self._fail_ops = fail_ops
        self._healthy = healthy
        self.calls: list[ProviderCall] = []

    def call(self, call: ProviderCall) -> ProviderResult:
        self.calls.append(call)
        if call.op in self._fail_ops:
            return ProviderResult(ok=False, detail=f"{call.op} failed (scripted)")
        if call.op == ProviderCallKind.OBSERVE:
            observed = tuple(
                ObservedResource(kind=kind, name=name, resource_hash=digest)
                for (kind, name), digest in sorted(self._state.items())
            )
            return ProviderResult(ok=True, observed=observed)
        if call.op == ProviderCallKind.APPLY:
            if call.change == PlanAction.DELETE:
                self._state.pop((call.kind, call.name), None)
            else:
                self._state[(call.kind, call.name)] = call.digest
            return ProviderResult(ok=True)
        if call.op == ProviderCallKind.HEALTH:
            return ProviderResult(ok=self._healthy)
        if call.op == ProviderCallKind.TEARDOWN:
            self._state.clear()
            return ProviderResult(ok=True)
        return ProviderResult(ok=False, detail=f"unknown op {call.op!r}")

    def seed(self, resources: tuple[ObservedResource, ...]) -> None:
        for resource in resources:
            self._state[(resource.kind, resource.name)] = resource.resource_hash

    def observed(self) -> tuple[ObservedResource, ...]:
        return tuple(
            ObservedResource(kind=kind, name=name, resource_hash=digest)
            for (kind, name), digest in sorted(self._state.items())
        )


class ProviderRuntime:
    """The deterministic provider hosting engine (plan -> verify -> apply -> health)."""

    def __init__(
        self,
        provider: Provider,
        adapter: ProviderAdapter,
        *,
        ledger: ProviderLedger,
        notifier: NotificationSink | None = None,
        binding: NotificationBinding | None = None,
        max_attempts: int = 3,
    ) -> None:
        if isinstance(max_attempts, bool) or max_attempts <= 0:
            raise ProviderRuntimeError("max_attempts must be a positive integer.")
        self._provider = provider
        self._adapter = adapter
        self._ledger = ledger
        self._notifier: NotificationSink = notifier or NullNotificationSink()
        self._binding = binding
        self._max_attempts = max_attempts

    @property
    def provider(self) -> Provider:
        return self._provider

    @property
    def ledger(self) -> ProviderLedger:
        return self._ledger

    # -- confined calls ------------------------------------------------------

    def _call(self, call: ProviderCall, *, plan_hash: str = "") -> ProviderResult:
        result = self._adapter.call(call)
        self._record(
            action=call.op,
            call_hash=call.call_hash(),
            plan_hash=plan_hash,
            ok=result.ok,
            detail=result.detail,
        )
        return result

    def _record(
        self,
        *,
        action: str,
        call_hash: str,
        plan_hash: str = "",
        ok: bool = True,
        detail: str = "",
        plan: dict[str, Any] | None = None,
        desired: list[dict[str, Any]] | None = None,
    ) -> ProviderEvidence:
        evidence = ProviderEvidence(
            action=action,
            call_hash=call_hash,
            provider=self._provider.id,
            plan_hash=plan_hash,
            ok=ok,
            detail=detail,
            plan=plan,
            desired=desired,
        )
        self._ledger.append(evidence)
        return evidence

    def observe(self) -> tuple[ObservedResource, ...]:
        """Read the provider's observed state (an untrusted claim, recorded)."""
        result = self._call(ProviderCall(ProviderCallKind.OBSERVE, self._provider.id))
        if not result.ok:
            raise ProviderRuntimeError(f"provider observe failed: {result.detail}")
        return result.observed

    def health(self, *, plan_hash: str = "") -> bool:
        """Run the health gate (a separate, deterministic check of observed state)."""
        result = self._call(
            ProviderCall(ProviderCallKind.HEALTH, self._provider.id), plan_hash=plan_hash
        )
        return result.ok

    def teardown(self) -> bool:
        """Tear the provider's managed state down (gated, idempotent)."""
        result = self._call(ProviderCall(ProviderCallKind.TEARDOWN, self._provider.id))
        return result.ok

    def apply(
        self,
        plan: ProviderPlan,
        *,
        expected_hash: str,
        approved: bool = False,
    ) -> bool:
        """Verify-then-apply a pinned plan; idempotent under retry.

        The plan is pinned to ``expected_hash`` and every mutating step is
        content-pinned. Idempotency under retry comes from re-planning against
        freshly observed state: an already-converged plan is a no-op, and a
        retried ledger append never duplicates evidence.
        """
        assert_plan_appliable(plan, expected_hash=expected_hash, approved=approved)
        if plan.is_converged():
            return True
        for step in plan.mutating_steps:
            call = ProviderCall(
                op=ProviderCallKind.APPLY,
                provider=self._provider.id,
                kind=step.kind,
                name=step.name,
                digest=step.digest,
                change=step.action,
            )
            result = self._call(call, plan_hash=plan.plan_hash())
            if not result.ok:
                return False
        return True

    # -- reconciliation ------------------------------------------------------

    def _plan(self, desired: tuple[ResourceSpec, ...]) -> ProviderPlan:
        observed = self.observe()
        return plan_provider(
            self._provider.id, desired, observed, target=self._provider.target
        )

    def _remember_healthy(
        self, plan: ProviderPlan, desired: tuple[ResourceSpec, ...]
    ) -> None:
        self._record(
            action="healthy",
            call_hash=_digest(f"healthy:{plan.plan_hash()}"),
            plan_hash=plan.plan_hash(),
            plan=plan.to_dict(),
            desired=[resource.to_dict() for resource in desired],
        )

    def _notify(self, event: str, plan: ProviderPlan) -> None:
        payload: dict[str, Any] = {
            "provider": self._provider.id,
            "event": event,
            "plan_hash": plan.plan_hash(),
        }
        if self._binding is not None:
            emit_notification(self._binding, self._notifier, event, payload)
        else:
            self._notifier.emit(event, payload)

    def reconcile(
        self, desired: tuple[ResourceSpec, ...], *, approved: bool = False
    ) -> ReconcileResult:
        """Drive ``plan -> verify -> apply -> health`` to a deterministic outcome.

        Already-converged **healthy** state performs no mutating call. A failed
        health gate triggers deterministic auto-rollback to the last healthy
        pinned plan; bounded repeated failure escalates and stops. This method
        never returns an ambiguous or partial state.
        """
        had_history = self._ledger.count() > 0
        plan = self._plan(desired)
        attempts = 0
        while True:
            if plan.is_converged():
                if self.health(plan_hash=plan.plan_hash()):
                    self._finish_converged(desired, plan, had_history)
                    return ReconcileResult(
                        action=ReconcileAction.CONVERGED, plan=plan, attempts=attempts
                    )
                # A converged plan with a failing health gate is a failure, not
                # convergence (SPEC-0020 §3): decide rollback/escalate below.
            elif self.apply(
                plan, expected_hash=plan.plan_hash(), approved=approved
            ) and self.health(plan_hash=plan.plan_hash()):
                self._finish_converged(desired, plan, had_history)
                return ReconcileResult(
                    action=ReconcileAction.CONVERGED, plan=plan, attempts=attempts
                )

            attempts += 1
            previous = self._ledger.last_healthy_desired()
            action = reconcile_action(
                converged=False,
                health_ok=False,
                rollback_available=previous is not None,
                attempts=attempts,
                max_attempts=self._max_attempts,
            )
            if action == ReconcileAction.ROLLBACK and previous is not None:
                target = plan_provider(
                    self._provider.id,
                    previous,
                    self.observe(),
                    target=self._provider.target,
                )
                rolled_back = self.apply(
                    target, expected_hash=target.plan_hash(), approved=True
                ) and self.health(plan_hash=target.plan_hash())
                if rolled_back:
                    self._record(
                        action="rolled-back",
                        call_hash=_digest(f"rolled-back:{target.plan_hash()}"),
                        plan_hash=target.plan_hash(),
                        plan=target.to_dict(),
                    )
                    self._notify(NotificationEvent.ROLLED_BACK, target)
                    plan = self._plan(desired)
                    continue
            self._record(
                action="escalated",
                call_hash=_digest(f"escalated:{plan.plan_hash()}"),
                plan_hash=plan.plan_hash(),
                ok=False,
                plan=plan.to_dict(),
            )
            self._notify(NotificationEvent.ESCALATED, plan)
            return ReconcileResult(
                action=ReconcileAction.ESCALATE, plan=plan, attempts=attempts
            )

    def _finish_converged(
        self,
        desired: tuple[ResourceSpec, ...],
        plan: ProviderPlan,
        had_history: bool,
    ) -> None:
        """Record a healthy convergence and emit the transition notification."""
        self._remember_healthy(plan, desired)
        self._record(
            action="converged",
            call_hash=_digest(f"converged:{plan.plan_hash()}"),
            plan_hash=plan.plan_hash(),
            plan=plan.to_dict(),
        )
        self._notify(
            NotificationEvent.HEALED if had_history else NotificationEvent.PROVISIONED,
            plan,
        )
