"""Tests for the versioned core contracts."""

from __future__ import annotations

import pytest

from meta_harness.contracts.capability import Capability
from meta_harness.contracts.result import (
    Failure,
    FailureReason,
    VerifiedResult,
)
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.contracts.trajectory import (
    StepRecord,
    StepStatus,
    Trajectory,
    TrajectoryVersion,
)


class TestCapability:
    def test_valid_capability(self) -> None:
        cap = Capability(name="count", version="1")
        assert cap.name == "count"
        assert cap.version == "1"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"name": "", "version": "1"},
            {"name": "count", "version": ""},
        ],
    )
    def test_invalid_capability_rejected(self, kwargs: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            Capability(**kwargs)

    def test_capability_is_hashable_and_equal(self) -> None:
        assert Capability(name="count", version="1") == Capability(name="count", version="1")
        assert Capability(name="count", version="1") != Capability(name="count", version="2")
        assert len({Capability(name="count", version="1")}) == 1


class TestResourceEnvelope:
    def test_valid_envelope(self) -> None:
        env = ResourceEnvelope(timeout_seconds=10.0, max_steps=5, max_step_seconds=1.0)
        assert env.timeout_seconds == 10.0
        assert env.max_steps == 5
        assert env.max_step_seconds == 1.0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"timeout_seconds": 0.0, "max_steps": 1},
            {"timeout_seconds": -1.0, "max_steps": 1},
            {"timeout_seconds": 1.0, "max_steps": 0},
            {"timeout_seconds": 1.0, "max_steps": -2},
            {"timeout_seconds": 1.0, "max_steps": 1, "max_step_seconds": 0.0},
            {"timeout_seconds": 1.0, "max_steps": 1, "max_step_seconds": -0.5},
        ],
    )
    def test_invalid_envelope_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            ResourceEnvelope(**kwargs)  # type: ignore[arg-type]


class TestTaskSpecification:
    def test_valid_task_by_name(self) -> None:
        task = TaskSpecification(
            version=TaskSpecVersion.V2,
            task_id="t1",
            agent_name="counter",
            payload={"text": "hello", "target": "l"},
        )
        assert task.envelope.max_steps == 100  # default envelope
        assert task.capability is None

    def test_valid_task_by_capability(self) -> None:
        task = TaskSpecification(
            version=TaskSpecVersion.V2,
            task_id="t2",
            capability=Capability(name="reverse", version="1"),
            payload={"text": "hello"},
        )
        assert task.agent_name is None
        assert task.capability == Capability(name="reverse", version="1")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"task_id": "", "agent_name": "counter"},
            {"task_id": "t1", "agent_name": ""},
        ],
    )
    def test_invalid_task_rejected(self, kwargs: dict[str, str]) -> None:
        base: dict[str, object] = {"version": TaskSpecVersion.V2, "payload": {}}
        base.update(kwargs)
        with pytest.raises(ValueError):
            TaskSpecification(**base)  # type: ignore[arg-type]

    def test_task_requires_exactly_one_selector(self) -> None:
        # Neither selector provided.
        with pytest.raises(ValueError):
            TaskSpecification(
                version=TaskSpecVersion.V2,
                task_id="t1",
                payload={},
            )
        # Both selectors provided.
        with pytest.raises(ValueError):
            TaskSpecification(
                version=TaskSpecVersion.V2,
                task_id="t1",
                agent_name="counter",
                capability=Capability(name="count", version="1"),
                payload={},
            )


class TestTrajectory:
    def test_contiguous_step_indices_enforced(self) -> None:
        steps = [
            StepRecord(step_index=0, status=StepStatus.COMPLETED, description="a"),
            # Deliberately duplicated index to trigger validation.
            StepRecord(step_index=0, status=StepStatus.COMPLETED, description="b"),
        ]
        with pytest.raises(ValueError):
            Trajectory(TrajectoryVersion.V1, "t1", "counter", tuple(steps))

    def test_valid_trajectory(self) -> None:
        steps = [
            StepRecord(step_index=0, status=StepStatus.COMPLETED, description="a"),
            StepRecord(step_index=1, status=StepStatus.COMPLETED, description="b"),
        ]
        traj = Trajectory(TrajectoryVersion.V1, "t1", "counter", tuple(steps))
        assert len(traj.steps) == 2


class TestOutcomeContracts:
    def test_verified_result_requires_valid_version(self) -> None:
        empty = Trajectory(TrajectoryVersion.V1, "t1", "counter")
        with pytest.raises(ValueError):
            VerifiedResult(
                version="result.v2",  # type: ignore[arg-type]
                task_id="t1",
                output=0,
                trajectory=empty,
            )

    def test_failure_holds_trajectory(self) -> None:
        empty = Trajectory(TrajectoryVersion.V1, "t1", "counter")
        failure = Failure(
            task_id="t1",
            reason=FailureReason.AGENT_ERROR,
            message="boom",
            trajectory=empty,
        )
        assert failure.reason is FailureReason.AGENT_ERROR
        assert failure.trajectory is empty
