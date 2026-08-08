"""Tests for Manager-orchestrated sequential composition (pipelines)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.capability import Capability
from meta_harness.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import (
    CASE_TOOL_MANIFEST,
    COUNTER_CAPABILITY,
    REVERSE_MANIFEST,
)


def _pipeline_task(
    task_id: str,
    stages: tuple[StageSpec, ...],
    payload: Any,
    envelope: ResourceEnvelope | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V4,
        task_id=task_id,
        payload=payload,
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        pipeline=SequentialComposition(version=PipelineVersion.V1, stages=stages),
    )


class TestPipelineContract:
    def test_empty_pipeline_rejected(self) -> None:
        with pytest.raises(ValueError):
            SequentialComposition(version=PipelineVersion.V1, stages=())

    def test_stage_requires_exactly_one_selector(self) -> None:
        with pytest.raises(ValueError):
            StageSpec(agent_name=None, capability=None)
        with pytest.raises(ValueError):
            StageSpec(agent_name="reverse", capability=COUNTER_CAPABILITY)

    def test_pipeline_task_cannot_also_set_single_selector(self) -> None:
        with pytest.raises(ValueError):
            TaskSpecification(
                version=TaskSpecVersion.V4,
                task_id="t",
                agent_name="reverse",
                payload={},
                pipeline=SequentialComposition(
                    version=PipelineVersion.V1,
                    stages=(StageSpec(agent_name="reverse"),),
                ),
            )


class TestSequentialComposition:
    def test_stages_execute_in_declared_order(self) -> None:
        """case_tool then reverse: 'abc' -> 'ABC' -> 'CBA'."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "order",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "CBA"

    def test_output_handed_off_only_after_verification(self, tmp_path: Path) -> None:
        """The second stage receives the verified output of the first."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "handoff",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "aab"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # case_tool('aab')='AAB', reverse('AAB')='BAA'.
        assert outcome.result.output == "BAA"

        # The trajectory records both stages and the hand-off.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any("stage 0 begin" in d for d in descriptions)
        assert any("stage 1 begin" in d for d in descriptions)
        # Both agents' work appears.
        assert any("computed final reversed string" in d for d in descriptions)
        assert any("received to_upper tool result" in d for d in descriptions)

    def test_resource_bounds_enforced_across_composition(self) -> None:
        """A shared envelope bounds the whole composition (step limit)."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # max_steps=1: even the first stage cannot complete.
        task = _pipeline_task(
            "budget",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_intermediate_failure_aborts_cleanly(self, tmp_path: Path) -> None:
        """A verification failure at an intermediate stage aborts the pipeline."""
        from tests.fake_agent import WRONG_AGENT_MANIFEST

        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(WRONG_AGENT_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # wrong agent always outputs -1, which fails its verification.
        task = _pipeline_task(
            "abort",
            (
                StageSpec(agent_name="wrong"),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

        # The trajectory records where it stopped: stage 0 ran, stage 1 never began.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any("stage 0 begin" in d for d in descriptions)
        assert not any("stage 1 begin" in d for d in descriptions)

    def test_unknown_stage_agent_aborts(self) -> None:
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "unknown-stage",
            (
                StageSpec(agent_name="reverse"),
                StageSpec(agent_name="does_not_exist"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.UNKNOWN_AGENT

    def test_final_result_verified_and_durable(self, tmp_path: Path) -> None:
        """The complete trajectory is durable and reconstructible."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "durable",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "hello"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        # Reload from a fresh manager bound to the same store.
        fresh = AgentManager(store=FileTrajectoryStore(tmp_path))
        stored = fresh.load(outcome.trajectory_id)
        assert stored is not None
        assert stored.outcome.kind == "verified"
        assert stored.outcome.output == "OLLEH"  # case_tool('hello')='HELLO', reverse='OLLEH'

    def test_pipeline_by_capability(self) -> None:
        """Stages can select agents by capability."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "by-cap",
            (
                StageSpec(capability=Capability(name="reverse", version="1")),
                StageSpec(capability=Capability(name="reverse", version="1")),
            ),
            {"text": "ab"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # reverse('ab')='ba', reverse('ba')='ab'.
        assert outcome.result.output == "ab"

    def test_pipeline_with_tool_stage(self) -> None:
        """A pipeline stage can use a mediated tool (case_tool)."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "tool-stage",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # case_tool('abc')='ABC', reverse('ABC')='CBA'.
        assert outcome.result.output == "CBA"

    def test_agents_cannot_invoke_each_other(self) -> None:
        """Agents have no mechanism to spawn or directly invoke other agents."""
        from meta_harness.agents.interface import ToolContext

        ctx = ToolContext()
        # No agent-invocation capability is exposed to agents.
        assert not hasattr(ctx, "invoke")
        assert not hasattr(ctx, "spawn")

    def test_pipeline_deterministic_and_replayable(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "det",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abracadabra"},
        )
        first = m.run(task)
        second = m.run(task)
        assert first.result is not None and second.result is not None
        assert first.result.output == second.result.output

        def sig(outcome) -> list[tuple[int, str, str, Any]]:
            result = []
            for s in outcome.result.trajectory.steps:
                output = s.output
                if s.description == "pipeline resource accounting" and isinstance(output, dict):
                    # Wall-clock elapsed_seconds is inherently nondeterministic;
                    # the deterministic property is the step/consumption counts.
                    stages = [
                        {k: v for k, v in a.items() if k != "elapsed_seconds"}
                        for a in output.get("stages", [])
                    ]
                    output = {"stages": stages, "total_steps": output.get("total_steps")}
                result.append((s.step_index, s.status.value, s.description, output))
            return result

        assert sig(first) == sig(second)
