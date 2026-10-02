"""Tests for the local-first provider adapter (``cbp/provider_local.py``; SPEC-0020).

Deterministic and offline: the task runner is a fake (the single I/O seam) and the
adapter content-verifies every mutating task's bytes before running it. The suite
proves canonical observed-state parsing, verify-then-apply refusal, and full
reconcile convergence/rollback over the real runtime.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_centric.cbp import (
    LocalTaskProviderAdapter,
    LocalTaskResult,
    ProviderCall,
    ProviderCallKind,
    ProviderLedger,
    ProviderRuntime,
)
from agent_centric.contracts.provider import (
    ObservedResource,
    PlanAction,
    Provider,
    ProviderTask,
    ReconcileAction,
    ResourceSpec,
)
from agent_centric.contracts.service import TaskBinding, TaskKind


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_script(tasks_dir: Path, target: str, body: bytes | None = None) -> str:
    data = body if body is not None else b"#!/bin/sh\nexit 0\n"
    (tasks_dir / target).write_bytes(data)
    return _sha256(data)


class _FakeRunner:
    """A deterministic offline task runner modelling provider state."""

    def __init__(
        self,
        *,
        state: dict[tuple[str, str], str] | None = None,
        fail_targets: frozenset[str] = frozenset(),
        poison: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self.state: dict[tuple[str, str], str] = dict(state or {})
        self.fail_targets = set(fail_targets)
        self.poison = set(poison)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def __call__(
        self, target: str, args: Sequence[str], *, tasks_dir: Path
    ) -> LocalTaskResult:
        self.calls.append((target, tuple(args)))
        if target in self.fail_targets:
            return LocalTaskResult(target=target, returncode=1, output="scripted failure")
        if target == "observe.sh":
            resources = [
                {"kind": kind, "name": name, "resource_hash": digest}
                for (kind, name), digest in sorted(self.state.items())
            ]
            payload = {"resources": resources}
            text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            return LocalTaskResult(target=target, returncode=0, output=text)
        if target == "apply.sh":
            kind, name, change, digest = args
            if change == PlanAction.DELETE:
                self.state.pop((kind, name), None)
            else:
                self.state[(kind, name)] = digest
            return LocalTaskResult(target=target, returncode=0, output="")
        if target == "health.sh":
            healthy = not any(key in self.poison for key in self.state)
            return LocalTaskResult(
                target=target,
                returncode=0 if healthy else 1,
                output="" if healthy else "unhealthy",
            )
        if target == "teardown.sh":
            self.state.clear()
            return LocalTaskResult(target=target, returncode=0, output="")
        return LocalTaskResult(target=target, returncode=1, output=f"unknown {target}")

    def apply_calls(self) -> list[tuple[str, tuple[str, ...]]]:
        return [entry for entry in self.calls if entry[0] == "apply.sh"]


def _setup(
    tmp_path: Path,
    *,
    include_observe: bool = True,
    runner: _FakeRunner | None = None,
    require_digests: bool = True,
    apply_digest: str | None = None,
) -> tuple[Provider, LocalTaskProviderAdapter, _FakeRunner, Path]:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    d_apply = _write_script(tasks_dir, "apply.sh")
    d_teardown = _write_script(tasks_dir, "teardown.sh")
    tasks = [
        TaskBinding(name=ProviderTask.PLAN, target="plan.sh"),
        TaskBinding(
            name=ProviderTask.APPLY,
            target="apply.sh",
            mutating=True,
            digest=d_apply if apply_digest is None else apply_digest,
        ),
        TaskBinding(name=ProviderTask.HEALTH, target="health.sh"),
        TaskBinding(
            name=ProviderTask.TEARDOWN,
            target="teardown.sh",
            mutating=True,
            digest=d_teardown,
        ),
    ]
    if include_observe:
        tasks.insert(1, TaskBinding(name=ProviderTask.OBSERVE, target="observe.sh"))
    provider = Provider(id="local-1", target="local", tasks=tuple(tasks))
    fake = runner or _FakeRunner()
    adapter = LocalTaskProviderAdapter(
        provider, fake, tasks_dir=tasks_dir, require_digests=require_digests
    )
    return provider, adapter, fake, tasks_dir


def _resource(name: str = "a") -> ResourceSpec:
    return ResourceSpec(kind="db", name=name, spec={"size": "small"})


class TestObserve:
    def test_parses_canonical_resources(self, tmp_path: Path) -> None:
        _, adapter, fake, _ = _setup(tmp_path)
        fake.state[("db", "a")] = "a" * 64
        result = adapter.call(ProviderCall(ProviderCallKind.OBSERVE, "local-1"))
        assert result.ok is True
        assert result.observed == (ObservedResource("db", "a", "a" * 64),)

    def test_accepts_a_bare_resource_list(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        _write_script(tasks_dir, "observe.sh")
        provider = Provider(
            id="p",
            tasks=(TaskBinding(name=ProviderTask.OBSERVE, target="observe.sh"),),
        )

        def runner(target: str, args: Sequence[str], *, tasks_dir: Path) -> LocalTaskResult:
            text = json.dumps(
                [{"kind": "db", "name": "a", "resource_hash": "b" * 64}],
                sort_keys=True,
                separators=(",", ":"),
            )
            return LocalTaskResult(target, 0, text)

        adapter = LocalTaskProviderAdapter(provider, runner, tasks_dir=tasks_dir)
        result = adapter.call(ProviderCall(ProviderCallKind.OBSERVE, "p"))
        assert result.ok is True and result.observed[0].name == "a"

    def test_noncanonical_output_is_refused(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        _write_script(tasks_dir, "observe.sh")
        provider = Provider(
            id="p",
            tasks=(TaskBinding(name=ProviderTask.OBSERVE, target="observe.sh"),),
        )

        def runner(target: str, args: Sequence[str], *, tasks_dir: Path) -> LocalTaskResult:
            return LocalTaskResult(target, 0, '{"resources": []}')

        adapter = LocalTaskProviderAdapter(provider, runner, tasks_dir=tasks_dir)
        result = adapter.call(ProviderCall(ProviderCallKind.OBSERVE, "p"))
        assert result.ok is False and "canonical" in result.detail

    def test_malformed_resource_is_refused(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        _write_script(tasks_dir, "observe.sh")
        provider = Provider(
            id="p",
            tasks=(TaskBinding(name=ProviderTask.OBSERVE, target="observe.sh"),),
        )

        def runner(target: str, args: Sequence[str], *, tasks_dir: Path) -> LocalTaskResult:
            text = json.dumps(
                {"resources": [{"kind": "db"}]}, sort_keys=True, separators=(",", ":")
            )
            return LocalTaskResult(target, 0, text)

        adapter = LocalTaskProviderAdapter(provider, runner, tasks_dir=tasks_dir)
        result = adapter.call(ProviderCall(ProviderCallKind.OBSERVE, "p"))
        assert result.ok is False and "malformed" in result.detail

    def test_missing_observe_role_is_refused(self, tmp_path: Path) -> None:
        _, adapter, _, _ = _setup(tmp_path, include_observe=False)
        result = adapter.call(ProviderCall(ProviderCallKind.OBSERVE, "local-1"))
        assert result.ok is False and "observe" in result.detail


class TestVerifyThenApply:
    def test_apply_passes_resource_arguments(self, tmp_path: Path) -> None:
        _, adapter, fake, _ = _setup(tmp_path)
        call = ProviderCall(
            op=ProviderCallKind.APPLY,
            provider="local-1",
            kind="db",
            name="a",
            digest="c" * 64,
            change=PlanAction.CREATE,
        )
        assert adapter.call(call).ok is True
        assert fake.apply_calls() == [("apply.sh", ("db", "a", PlanAction.CREATE, "c" * 64))]
        assert fake.state[("db", "a")] == "c" * 64

    def test_mutating_task_without_digest_is_refused_before_running(
        self, tmp_path: Path
    ) -> None:
        _, adapter, fake, _ = _setup(tmp_path, apply_digest="")
        call = ProviderCall(
            op=ProviderCallKind.APPLY,
            provider="local-1",
            kind="db",
            name="a",
            digest="c" * 64,
            change=PlanAction.CREATE,
        )
        result = adapter.call(call)
        assert result.ok is False and "no content digest" in result.detail
        assert fake.calls == []

    def test_digest_drift_is_refused_before_running(self, tmp_path: Path) -> None:
        _, adapter, fake, tasks_dir = _setup(tmp_path)
        (tasks_dir / "apply.sh").write_bytes(b"#!/bin/sh\nexit 0\n# tampered\n")
        call = ProviderCall(
            op=ProviderCallKind.APPLY,
            provider="local-1",
            kind="db",
            name="a",
            digest="c" * 64,
            change=PlanAction.CREATE,
        )
        result = adapter.call(call)
        assert result.ok is False and "digest mismatch" in result.detail
        assert fake.calls == []

    def test_unreadable_pinned_task_is_refused(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        provider = Provider(
            id="p",
            tasks=(
                TaskBinding(
                    name=ProviderTask.APPLY,
                    target="missing.sh",
                    mutating=True,
                    digest="a" * 64,
                ),
            ),
        )
        adapter = LocalTaskProviderAdapter(provider, _FakeRunner(), tasks_dir=tasks_dir)
        call = ProviderCall(
            op=ProviderCallKind.APPLY,
            provider="p",
            kind="db",
            name="a",
            digest="c" * 64,
            change=PlanAction.CREATE,
        )
        result = adapter.call(call)
        assert result.ok is False and "not readable" in result.detail

    def test_mutating_callable_is_refused(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        provider = Provider(
            id="p",
            tasks=(
                TaskBinding(
                    name=ProviderTask.APPLY,
                    target="apply-callable",
                    kind=TaskKind.CALLABLE,
                    mutating=True,
                ),
            ),
        )
        adapter = LocalTaskProviderAdapter(provider, _FakeRunner(), tasks_dir=tasks_dir)
        call = ProviderCall(
            op=ProviderCallKind.APPLY,
            provider="p",
            kind="db",
            name="a",
            digest="c" * 64,
            change=PlanAction.CREATE,
        )
        result = adapter.call(call)
        assert result.ok is False and "callable" in result.detail

    def test_path_traversal_target_is_refused(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        provider = Provider(
            id="p",
            tasks=(
                TaskBinding(name=ProviderTask.HEALTH, target="../escape.sh"),
            ),
        )
        adapter = LocalTaskProviderAdapter(provider, _FakeRunner(), tasks_dir=tasks_dir)
        result = adapter.call(ProviderCall(ProviderCallKind.HEALTH, "p"))
        assert result.ok is False and "single name" in result.detail

    def test_provider_id_mismatch_is_refused(self, tmp_path: Path) -> None:
        _, adapter, _, _ = _setup(tmp_path)
        result = adapter.call(ProviderCall(ProviderCallKind.HEALTH, "someone-else"))
        assert result.ok is False and "mismatch" in result.detail

    def test_nonzero_return_code_is_a_refusal(self, tmp_path: Path) -> None:
        runner = _FakeRunner(fail_targets=frozenset({"health.sh"}))
        _, adapter, _, _ = _setup(tmp_path, runner=runner)
        result = adapter.call(ProviderCall(ProviderCallKind.HEALTH, "local-1"))
        assert result.ok is False and "scripted failure" in result.detail


class TestRuntimeIntegration:
    def _runtime(
        self, tmp_path: Path, provider: Provider, adapter: LocalTaskProviderAdapter, **kw: Any
    ) -> ProviderRuntime:
        return ProviderRuntime(
            provider, adapter, ledger=ProviderLedger(tmp_path / "evidence.jsonl"), **kw
        )

    def test_reconcile_converges(self, tmp_path: Path) -> None:
        provider, adapter, fake, _ = _setup(tmp_path)
        runtime = self._runtime(tmp_path, provider, adapter)
        result = runtime.reconcile((_resource(),))
        assert result.action == ReconcileAction.CONVERGED
        assert fake.state[("db", "a")] == _resource().resource_hash()
        assert runtime.ledger.count() > 0

    def test_already_converged_makes_no_mutating_call(self, tmp_path: Path) -> None:
        provider, adapter, fake, _ = _setup(tmp_path)
        runtime = self._runtime(tmp_path, provider, adapter)
        runtime.reconcile((_resource(),))
        before = len(fake.apply_calls())
        assert runtime.reconcile((_resource(),)).action == ReconcileAction.CONVERGED
        assert len(fake.apply_calls()) == before

    def test_retry_never_duplicates_evidence(self, tmp_path: Path) -> None:
        provider, adapter, _, _ = _setup(tmp_path)
        runtime = self._runtime(tmp_path, provider, adapter)
        assert runtime.observe() == ()
        count = runtime.ledger.count()
        assert runtime.observe() == ()
        assert runtime.ledger.count() == count

    def test_failed_health_rolls_back_then_escalates(self, tmp_path: Path) -> None:
        provider, adapter, fake, _ = _setup(tmp_path)
        runtime = self._runtime(tmp_path, provider, adapter, max_attempts=2)
        assert runtime.reconcile((_resource(name="a"),)).action == ReconcileAction.CONVERGED

        fake.poison = frozenset({("db", "b")})
        result = runtime.reconcile((_resource(name="a"), _resource(name="b")))
        assert result.action == ReconcileAction.ESCALATE
        actions = [entry.action for entry in runtime.ledger.entries()]
        assert "rolled-back" in actions and "escalated" in actions
        # The rollback re-planned toward the last healthy desired state and
        # removed the drift: its pinned plan contains a delete of poisoned b.
        rollbacks = [
            entry for entry in runtime.ledger.entries() if entry.action == "rolled-back"
        ]
        assert rollbacks, "no rolled-back evidence recorded"
        steps = rollbacks[0].plan["steps"]
        assert any(
            step["action"] == "delete" and step["name"] == "b" for step in steps
        )
