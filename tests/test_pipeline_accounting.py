"""Tests for per-stage resource envelopes and composition accounting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import CASE_TOOL_MANIFEST, REVERSE_MANIFEST


def _pipeline_task(
    task_id: str,
    stages: tuple[StageSpec, ...],
    payload: Any,
    parent: ResourceEnvelope | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V4,
        task_id=task_id,
        payload=payload,
        envelope=parent or ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        pipeline=SequentialComposition(version=PipelineVersion.V2, stages=stages),
    )


def _case_then_reverse(
    *,
    case_env: ResourceEnvelope | None = None,
    reverse_env: ResourceEnvelope | None = None,
) -> tuple[StageSpec, StageSpec]:
    return (
        StageSpec(agent_name="case_tool", granted_tools=("to_upper",), stage_envelope=case_env),
        StageSpec(agent_name="reverse", stage_envelope=reverse_env),
    )


def _accounting(steps) -> dict[str, Any]:
    """Extract the pipeline resource-accounting summary from a trajectory."""
    for s in steps:
        if s.description == "pipeline resource accounting" and isinstance(s.output, dict):
            return s.output
    return {}


class TestStageEnvelopeEnforcement:
    def test_stage_envelope_step_limit_enforced(self) -> None:
        """A stage with a tiny step envelope aborts even if the parent is large."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # case_tool needs several steps (validate + tool request + result + ...);
        # a stage envelope of max_steps=1 must abort it.
        task = _pipeline_task(
            "stage-step-limit",
            _case_then_reverse(case_env=ResourceEnvelope(timeout_seconds=10.0, max_steps=1)),
            {"text": "abc"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_stage_without_envelope_inherits_parent(self) -> None:
        """A stage without its own envelope inherits the parent task envelope."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # No stage envelopes: both inherit parent max_steps=1, so stage 0 aborts.
        task = _pipeline_task(
            "inherit",
            _case_then_reverse(),
            {"text": "abc"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_stage_envelope_timeout_enforced(self) -> None:
        """A stage with a tiny timeout aborts even if the parent is large."""
        from tests.fake_agent import SLEEPY_AGENT_MANIFEST

        m = AgentManager()
        m.register(SLEEPY_AGENT_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "stage-timeout",
            (
                StageSpec(
                    agent_name="sleepy",
                    stage_envelope=ResourceEnvelope(timeout_seconds=0.05, max_steps=1000),
                ),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=2000),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.TIMEOUT

    def test_pipeline_v1_rejects_stage_envelope(self) -> None:
        with pytest.raises(ValueError):
            SequentialComposition(
                version=PipelineVersion.V1,
                stages=(
                    StageSpec(
                        agent_name="reverse",
                        stage_envelope=ResourceEnvelope(timeout_seconds=1.0, max_steps=5),
                    ),
                ),
            )


class TestCompositionAccounting:
    def test_overall_envelope_still_respected(self) -> None:
        """The parent envelope bounds the whole composition even with big stages."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # Each stage has a large envelope, but the parent max_steps=3 caps the
        # whole composition (boundary markers + steps exceed 3).
        task = _pipeline_task(
            "overall-limit",
            _case_then_reverse(
                case_env=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
                reverse_env=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            ),
            {"text": "abc"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=3),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_consumption_recorded_and_attributable(self, tmp_path: Path) -> None:
        """Each stage's consumption is recorded and attributable in the trajectory."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "account",
            _case_then_reverse(),
            {"text": "hello"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        accounting = _accounting(stored.steps)
        assert accounting, "expected a pipeline resource accounting step"
        stages = accounting["stages"]
        assert len(stages) == 2
        assert stages[0]["agent"] == "case_tool"
        assert stages[1]["agent"] == "reverse"
        # Each stage consumed at least one step.
        assert stages[0]["steps"] >= 1
        assert stages[1]["steps"] >= 1
        # The summary is durable and reconstructible.
        assert accounting["total_steps"] == len(stored.steps)

    def test_stage_boundary_records_envelope(self, tmp_path: Path) -> None:
        """Stage-boundary markers record the effective envelope for each stage."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        stage_env = ResourceEnvelope(timeout_seconds=5.0, max_steps=50)
        task = _pipeline_task(
            "boundary-env",
            _case_then_reverse(case_env=stage_env),
            {"text": "abc"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        boundaries = [
            s for s in stored.steps if s.description.startswith("pipeline stage")
        ]
        assert len(boundaries) == 2
        # Stage 0 declares its own envelope; stage 1 inherits the parent.
        env0 = boundaries[0].input["envelope"]
        assert env0["max_steps"] == 50
        env1 = boundaries[1].input["envelope"]
        assert env1["max_steps"] == 200

    def test_failure_abort_records_accounting(self, tmp_path: Path) -> None:
        """A stage failure still records the consumption summary durably."""
        from tests.fake_agent import WRONG_AGENT_MANIFEST

        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(WRONG_AGENT_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "abort-account",
            (
                StageSpec(agent_name="wrong"),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        accounting = _accounting(stored.steps)
        assert accounting, "expected accounting even on abort"
        # Only stage 0 ran.
        assert len(accounting["stages"]) == 1
        assert accounting["stages"][0]["agent"] == "wrong"

    def test_verified_handoff_and_ordering_preserved(self) -> None:
        """Per-stage envelopes do not change ordering or verified hand-off."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "handoff-ok",
            _case_then_reverse(
                case_env=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
                reverse_env=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            ),
            {"text": "abc"},
            parent=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # case_tool('abc')='ABC', reverse('ABC')='CBA'.
        assert outcome.result.output == "CBA"
