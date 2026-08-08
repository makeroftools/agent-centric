"""Control-plane correctness invariant tests.

These tests prove the mandatory invariants listed in Volley 001:
- A task can be submitted and fully governed by the Manager.
- Resource bounds and timeouts are actually enforced.
- Every step is recorded in a reconstructible trajectory.
- No result is accepted without passing verification.
- Failure modes are explicit, contained, and audited.
- The entire flow is testable and replayable.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.contracts.trajectory import Trajectory
from meta_harness.control_plane.manager import AgentManager
from tests.conftest import COUNTER_MANIFEST
from tests.fake_agent import SLEEPY_AGENT_MANIFEST, SLOW_STEP_AGENT_MANIFEST, WRONG_AGENT_MANIFEST


def _make_task(
    task_id: str,
    payload: dict[str, Any],
    envelope: ResourceEnvelope,
    agent_name: str = "counter",
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V2,
        task_id=task_id,
        agent_name=agent_name,
        payload=payload,
        envelope=envelope,
    )


def test_verified_result_is_returned_for_correct_agent(manager: AgentManager) -> None:
    task = _make_task(
        task_id="happy",
        payload={"text": "hello world", "target": "l"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    )
    outcome = manager.run(task)

    assert outcome.result is not None
    assert outcome.failure is None
    # "hello world" contains three 'l' characters.
    assert outcome.result.output == 3


def test_invalid_payload_agent_fails_explicitly(manager: AgentManager) -> None:
    """An agent error must surface as an explicit, audited failure."""
    task = _make_task(
        task_id="bad-payload",
        payload={"text": 123, "target": "l"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=10),
    )
    outcome = manager.run(task)

    assert outcome.result is None
    assert outcome.failure is not None
    assert outcome.failure.reason is FailureReason.AGENT_ERROR
    assert outcome.failure.trajectory is not None


def test_wrong_output_fails_verification(manager: AgentManager) -> None:
    """A result must not be accepted if it does not pass verification."""
    manager.register(WRONG_AGENT_MANIFEST)
    task = TaskSpecification(
        version=TaskSpecVersion.V2,
        task_id="wrong-output",
        agent_name="wrong",
        payload={"text": "hello", "target": "l"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=10),
    )
    outcome = manager.run(task)

    assert outcome.result is None
    assert outcome.failure is not None
    assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED


def test_trajectory_records_all_steps(manager: AgentManager) -> None:
    """Every step must be recorded in an ordered, reconstructible trajectory."""
    task = _make_task(
        task_id="traj",
        payload={"text": "aaaabbbb", "target": "a"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    )
    outcome = manager.run(task)
    assert outcome.result is not None

    traj = outcome.result.trajectory
    assert traj.task_id == "traj"
    assert traj.agent_name == "counter"
    assert len(traj.steps) >= 2
    # Indices must be contiguous and ordered.
    assert [s.step_index for s in traj.steps] == list(range(len(traj.steps)))
    # The final step should describe the computed count.
    assert traj.steps[-1].description == "computed final count"


def test_step_limit_is_enforced(manager: AgentManager) -> None:
    """An agent that exhausts its step budget must be stopped and fail explicitly."""
    task = _make_task(
        task_id="step-limit",
        payload={"text": "x" * 1000, "target": "x"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
    )
    outcome = manager.run(task)

    assert outcome.result is None
    assert outcome.failure is not None
    assert outcome.failure.reason is FailureReason.STEP_LIMIT


def test_overall_timeout_is_enforced() -> None:
    """A slow agent must be stopped at the overall timeout and fail explicitly."""
    from meta_harness.control_plane.manager import AgentManager

    m = AgentManager()
    m.register(SLEEPY_AGENT_MANIFEST)
    task = TaskSpecification(
        version=TaskSpecVersion.V2,
        task_id="timeout",
        agent_name="sleepy",
        payload={},
        envelope=ResourceEnvelope(timeout_seconds=0.05, max_steps=1000),
    )
    start = time.monotonic()
    outcome = m.run(task)
    elapsed = time.monotonic() - start

    assert outcome.result is None
    assert outcome.failure is not None
    assert outcome.failure.reason is FailureReason.TIMEOUT
    # Enforced within a small tolerance of the requested timeout.
    assert elapsed < 1.0


def test_per_step_timeout_is_enforced() -> None:
    """A step that exceeds max_step_seconds must be stopped and fail explicitly."""
    from meta_harness.control_plane.manager import AgentManager

    m = AgentManager()
    m.register(SLOW_STEP_AGENT_MANIFEST)
    task = TaskSpecification(
        version=TaskSpecVersion.V2,
        task_id="per-step-timeout",
        agent_name="slow_step",
        payload=42,
        envelope=ResourceEnvelope(
            timeout_seconds=10.0, max_steps=100, max_step_seconds=0.05
        ),
    )
    outcome = m.run(task)

    assert outcome.result is None
    assert outcome.failure is not None
    assert outcome.failure.reason is FailureReason.TIMEOUT


def test_unknown_agent_fails_explicitly() -> None:
    from meta_harness.control_plane.manager import AgentManager

    m = AgentManager()  # empty registry
    task = _make_task(
        task_id="unknown",
        payload={"text": "hi", "target": "h"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=10),
        agent_name="nonexistent",
    )
    outcome = m.run(task)

    assert outcome.result is None
    assert outcome.failure is not None
    assert outcome.failure.reason is FailureReason.UNKNOWN_AGENT


def test_duplicate_registration_is_rejected(manager: AgentManager) -> None:
    with pytest.raises(ValueError):
        manager.register(COUNTER_MANIFEST)


def test_flow_is_replayable_and_deterministic(manager: AgentManager) -> None:
    """The same inputs must reproduce the same trajectory and outcome."""
    task = _make_task(
        task_id="replay",
        payload={"text": "abracadabra", "target": "a"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    )
    first = manager.run(task)
    second = manager.run(task)

    assert first.result is not None and second.result is not None
    assert first.result.output == second.result.output

    # Determinism applies to the recorded step sequence (descriptions, status,
    # and outputs), not to wall-clock timing, which is inherently nondeterministic.
    def _deterministic_trajectory(traj: Trajectory) -> list[tuple[int, str, str, Any]]:
        return [
            (s.step_index, s.status.value, s.description, s.output)
            for s in traj.steps
        ]

    assert _deterministic_trajectory(first.result.trajectory) == _deterministic_trajectory(
        second.result.trajectory
    )