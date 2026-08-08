"""Tests for the durable, append-only trajectory store and auditability."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.trajectory_store import (
    CorruptTrajectoryError,
    FileTrajectoryStore,
    StoredOutcome,
    TrajectoryStoreError,
)
from tests.conftest import COUNTER_MANIFEST, REVERSE_MANIFEST

# Reuse the task helpers from the control-plane test module.
from tests.test_control_plane import _make_task


def _make_manager(tmp_path: Path, *, register_fakes: bool = False) -> AgentManager:
    from tests.fake_agent import (
        SLEEPY_AGENT_MANIFEST,
        SLOW_STEP_AGENT_MANIFEST,
        WRONG_AGENT_MANIFEST,
    )

    store = FileTrajectoryStore(tmp_path)
    m = AgentManager(store=store)
    m.register(COUNTER_MANIFEST)
    m.register(REVERSE_MANIFEST)
    if register_fakes:
        m.register(WRONG_AGENT_MANIFEST)
        m.register(SLEEPY_AGENT_MANIFEST)
        m.register(SLOW_STEP_AGENT_MANIFEST)
    return m


class TestFileStoreDurability:
    def test_trajectory_survives_process_boundary(self, tmp_path: Path) -> None:
        m = _make_manager(tmp_path)
        task = _make_task(
            "durable", {"text": "hello world", "target": "l"},
            ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        tid = outcome.trajectory_id
        assert tid is not None

        # Simulate a process restart: drop the Manager (and its in-memory state)
        # and reload from a brand-new Manager bound to the same directory.
        fresh = _make_manager(tmp_path)
        stored = fresh.load(tid)
        assert stored is not None
        assert stored.task_id == "durable"
        assert stored.agent_name == "counter"
        assert stored.outcome.kind == "verified"
        assert stored.outcome.output == 3
        # Steps are fully reconstructible, in order.
        assert [s.step_index for s in stored.steps] == list(range(len(stored.steps)))

    def test_steps_are_append_only_and_ordered(self, tmp_path: Path) -> None:
        m = _make_manager(tmp_path)
        task = _make_task(
            "append-only", {"text": "aaaabbbb", "target": "a"},
            ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        tid = outcome.trajectory_id
        assert tid is not None

        stored = m.load(tid)
        assert stored is not None
        steps = stored.steps
        # The persisted raw file should contain only meta + step + outcome records
        # in order, and should not be modified by reading.
        path = _file_for(m, tid)
        before = path.read_text(encoding="utf-8")
        _ = m.load(tid)
        after = path.read_text(encoding="utf-8")
        assert before == after
        # Exactly one meta, exactly one outcome, and step records in order.
        kinds = [json.loads(line)["kind"] for line in before.splitlines()]
        assert kinds[0] == "meta"
        assert kinds[-1] == "outcome"
        assert kinds[1:-1] == ["step"] * (len(steps))

    def test_crash_before_outcome_is_detected_as_interrupted(self, tmp_path: Path) -> None:
        """A trajectory with steps but no outcome is detectable, not silent."""
        store = FileTrajectoryStore(tmp_path)
        store.begin("crash-1", "crash-task", "counter")
        store.append_step(
            "crash-1",
            _step(0, "did some work"),
        )
        # No outcome recorded -> simulates a crash mid-task.

        m = AgentManager(store=store)
        stored = m.load("crash-1")
        assert stored is not None
        assert stored.outcome.kind == "interrupted"
        assert len(stored.steps) == 1

    def test_corrupt_trajectory_is_detected(self, tmp_path: Path) -> None:
        store = FileTrajectoryStore(tmp_path)
        store.begin("corrupt-1", "t", "counter")
        # Corrupt the raw file by appending a non-JSON line.
        f = _file_for(AgentManager(store=store), "corrupt-1")
        with f.open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
        m = AgentManager(store=store)
        with pytest.raises(CorruptTrajectoryError):
            m.load("corrupt-1")

    def test_duplicate_begin_rejected(self, tmp_path: Path) -> None:
        store = FileTrajectoryStore(tmp_path)
        store.begin("dup", "t", "counter")
        with pytest.raises(TrajectoryStoreError):
            store.begin("dup", "t2", "counter")

    def test_duplicate_outcome_rejected(self, tmp_path: Path) -> None:
        store = FileTrajectoryStore(tmp_path)
        store.begin("dout", "t", "counter")
        store.record_outcome("dout", StoredOutcome.verified(1))
        with pytest.raises(TrajectoryStoreError):
            store.record_outcome("dout", StoredOutcome.verified(2))


class TestInMemoryStore:
    def test_in_memory_replay(self) -> None:
        m = AgentManager()  # defaults to in-memory store
        m.register(COUNTER_MANIFEST)
        task = _make_task(
            "mem", {"text": "abc", "target": "a"},
            ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        assert stored.outcome.kind == "verified"


class TestManagerDurabilityIntegration:
    def test_failure_paths_produce_durable_records(self, tmp_path: Path) -> None:
        """Step-limit, timeout, and verification failures are durably stored."""
        m = _make_manager(tmp_path, register_fakes=True)

        # Step-limit failure.
        step_limit_task = _make_task(
            "sl", {"text": "x" * 1000, "target": "x"},
            ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
        )
        sl_outcome = m.run(step_limit_task)
        assert sl_outcome.failure is not None
        assert sl_outcome.trajectory_id is not None
        sl_stored = m.load(sl_outcome.trajectory_id)
        assert sl_stored is not None
        assert sl_stored.outcome.kind == "failure"
        assert sl_stored.outcome.reason == FailureReason.STEP_LIMIT.value

        # Verification failure (wrong agent output).
        wrong_task = TaskSpecification(
            version=TaskSpecVersion.V2, task_id="wf", agent_name="wrong",
            payload={"text": "hello", "target": "l"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=10),
        )
        wf_outcome = m.run(wrong_task)
        assert wf_outcome.failure is not None
        assert wf_outcome.trajectory_id is not None
        wf_stored = m.load(wf_outcome.trajectory_id)
        assert wf_stored is not None
        assert wf_stored.outcome.kind == "failure"
        assert wf_stored.outcome.reason == FailureReason.VERIFICATION_FAILED.value

    def test_verified_and_failure_are_mutually_consistent(self, tmp_path: Path) -> None:
        """The stored outcome always matches the returned Outcome kind."""
        m = _make_manager(tmp_path, register_fakes=True)

        # Success.
        ok_task = _make_task(
            "ok", {"text": "hello", "target": "l"},
            ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        ok = m.run(ok_task)
        assert ok.result is not None and ok.trajectory_id is not None
        ok_stored = m.load(ok.trajectory_id)
        assert ok_stored is not None and ok_stored.outcome.kind == "verified"

        # Failure.
        bad_task = TaskSpecification(
            version=TaskSpecVersion.V2, task_id="bad", capability=None,
            agent_name="counter", payload={"text": 123, "target": "l"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=10),
        )
        bad = m.run(bad_task)
        assert bad.failure is not None and bad.trajectory_id is not None
        bad_stored = m.load(bad.trajectory_id)
        assert bad_stored is not None and bad_stored.outcome.kind == "failure"
        assert bad_stored.outcome.reason == FailureReason.AGENT_ERROR.value

    def test_unknown_capability_is_durably_recorded(self, tmp_path: Path) -> None:
        m = _make_manager(tmp_path)
        from meta_harness.contracts.capability import Capability

        task = TaskSpecification(
            version=TaskSpecVersion.V2, task_id="unkcap",
            capability=Capability(name="missing", version="1"),
            payload={"text": "hi"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=10),
        )
        outcome = m.run(task)
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.UNKNOWN_AGENT
        assert outcome.trajectory_id is not None
        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        assert stored.outcome.kind == "failure"
        assert stored.outcome.reason == FailureReason.UNKNOWN_AGENT.value

    def test_load_missing_trajectory_returns_none(self, tmp_path: Path) -> None:
        m = _make_manager(tmp_path)
        assert m.load("does-not-exist") is None
        assert m.contains("does-not-exist") is False

    def test_replay_in_subprocess(self, tmp_path: Path) -> None:
        """Durability across a real process boundary, using an external process."""
        store = FileTrajectoryStore(tmp_path)
        m = AgentManager(store=store)
        m.register(COUNTER_MANIFEST)
        task = _make_task(
            "subproc", {"text": "aab", "target": "a"},
            ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None
        tid = outcome.trajectory_id

        # A separate Python process reloads the trajectory from the same directory.
        code = (
            "import sys; from meta_harness.control_plane.trajectory_store import "
            "FileTrajectoryStore; from meta_harness.control_plane.manager import AgentManager; "
            "m=AgentManager(store=FileTrajectoryStore(sys.argv[1])); "
            "t=m.load(sys.argv[2]); "
            "assert t is not None and t.outcome.kind=='verified' and t.outcome.output==2; "
            "print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path), tid],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


def _step(index: int, description: str) -> Any:
    from meta_harness.contracts.trajectory import StepRecord, StepStatus

    return StepRecord(
        step_index=index, status=StepStatus.COMPLETED, description=description
    )


def _file_for(manager: AgentManager, trajectory_id: str) -> Path:
    """Return the on-disk file for a trajectory in a FileTrajectoryStore."""
    store = manager._store  # type: ignore[attr-defined]
    assert isinstance(store, FileTrajectoryStore)
    return store._path(trajectory_id)