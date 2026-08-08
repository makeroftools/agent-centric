"""Tests for schema-constrained stage hand-off (Volley 007).

These tests prove that the verified output of a pipeline stage is validated
against declared output/input schemas before it is accepted as the next stage's
input, that schema mismatches abort the composition cleanly and are fully
audited, and that stages without declared schemas still behave correctly under
the documented conservative default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.handoff import is_valid_schema, validate_handoff
from meta_harness.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import CASE_TOOL_MANIFEST, REVERSE_MANIFEST


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
        pipeline=SequentialComposition(version=PipelineVersion.V3, stages=stages),
    )


def _handoff_steps(steps) -> list:
    return [s for s in steps if s.description.endswith("hand-off validated")]


class TestHandoffContract:
    def test_valid_schemas_accepted(self) -> None:
        assert is_valid_schema("str")
        assert is_valid_schema({"text": "str"})
        assert is_valid_schema({"text": "any"})
        assert not is_valid_schema("nope")
        assert not is_valid_schema({"text": "nope"})
        assert not is_valid_schema(123)

    def test_validate_handoff_scalar(self) -> None:
        passed, _ = validate_handoff("abc", "str")
        assert passed
        passed, _ = validate_handoff(3, "str")
        assert not passed

    def test_validate_handoff_object(self) -> None:
        passed, _ = validate_handoff({"text": "abc"}, {"text": "str"})
        assert passed
        passed, _ = validate_handoff({"text": 3}, {"text": "str"})
        assert not passed
        passed, _ = validate_handoff({"other": "abc"}, {"text": "str"})
        assert not passed

    def test_v3_requires_schema_support(self) -> None:
        # v1/v2 reject hand-off schemas; v3 accepts them.
        with pytest.raises(ValueError):
            SequentialComposition(
                version=PipelineVersion.V1,
                stages=(StageSpec(agent_name="reverse", output_schema="str"),),
            )
        with pytest.raises(ValueError):
            SequentialComposition(
                version=PipelineVersion.V2,
                stages=(StageSpec(agent_name="reverse", input_schema="str"),),
            )
        # v3 accepts them.
        SequentialComposition(
            version=PipelineVersion.V3,
            stages=(StageSpec(agent_name="reverse", output_schema="str"),),
        )

    def test_invalid_schema_rejected(self) -> None:
        with pytest.raises(ValueError):
            StageSpec(agent_name="reverse", output_schema="not_a_type")
        with pytest.raises(ValueError):
            StageSpec(agent_name="reverse", input_schema={"text": "not_a_type"})


class TestValidHandoff:
    def test_valid_handoff_succeeds_and_appears_in_trajectory(self, tmp_path: Path) -> None:
        """A schema-valid hand-off succeeds and is recorded durably."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "valid-handoff",
            (
                StageSpec(
                    agent_name="case_tool",
                    granted_tools=("to_upper",),
                    output_schema={"text": "str"},
                ),
                StageSpec(agent_name="reverse", input_schema={"text": "str"}),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # case_tool('abc')='ABC', reverse('ABC')='CBA'.
        assert outcome.result.output == "CBA"

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        handoffs = _handoff_steps(stored.steps)
        assert len(handoffs) == 1
        assert handoffs[0].input["from_stage"] == 0
        assert handoffs[0].input["to_stage"] == 1
        # The shape of the handed-off data is visible.
        assert handoffs[0].input["shape"]["kind"] == "object"
        assert handoffs[0].input["shape"]["fields"] == {"text": "str"}

    def test_output_schema_only(self) -> None:
        """A producing stage declaring only an output_schema is enforced."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "output-only",
            (
                StageSpec(
                    agent_name="case_tool",
                    granted_tools=("to_upper",),
                    output_schema={"text": "str"},
                ),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "CBA"

    def test_input_schema_only(self) -> None:
        """A consuming stage declaring only an input_schema is enforced."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "input-only",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse", input_schema={"text": "str"}),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "CBA"


class TestSchemaMismatch:
    def test_output_schema_mismatch_aborts(self, tmp_path: Path) -> None:
        """A stage output violating its declared output_schema aborts cleanly."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # case_tool produces {"text": "ABC"}; declare output as an int -> mismatch.
        task = _pipeline_task(
            "output-mismatch",
            (
                StageSpec(
                    agent_name="case_tool",
                    granted_tools=("to_upper",),
                    output_schema={"text": "int"},
                ),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.HANDOFF_FAILED
        assert "output_schema" in outcome.failure.message

        # The abort is durable and stage 1 never began.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any("stage 0 begin" in d for d in descriptions)
        assert not any("stage 1 begin" in d for d in descriptions)
        assert not any("hand-off validated" in d for d in descriptions)

    def test_input_schema_mismatch_aborts(self) -> None:
        """A stage input violating its declared input_schema aborts cleanly."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        # reverse expects {"text": "str"}; declare input as int -> mismatch.
        task = _pipeline_task(
            "input-mismatch",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse", input_schema={"text": "int"}),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.HANDOFF_FAILED
        assert "input_schema" in outcome.failure.message

    def test_no_invalid_data_reaches_next_stage(self) -> None:
        """A schema-invalid payload is never passed to a subsequent stage."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "no-leak",
            (
                StageSpec(
                    agent_name="case_tool",
                    granted_tools=("to_upper",),
                    output_schema={"text": "int"},  # will fail
                ),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        # The reverse stage never ran, so its verified output cannot exist.
        assert outcome.failure.reason is FailureReason.HANDOFF_FAILED
        descriptions = [s.description for s in outcome.failure.trajectory.steps]
        assert not any("computed final reversed string" in d for d in descriptions)


class TestDefaultBehaviour:
    def test_no_declared_schemas_uses_default(self, tmp_path: Path) -> None:
        """Stages without schemas behave correctly under the documented default.

        The default requires the handed-off payload to be a mapping (the shape
        the harness agents expect), so a scalar output is wrapped into
        ``{"text": ...}`` and validated against the default object shape.
        """
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "default",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            {"text": "abc"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "CBA"

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        handoffs = _handoff_steps(stored.steps)
        assert len(handoffs) == 1
        # The default validation still records the handed-off shape.
        assert handoffs[0].input["shape"]["kind"] == "object"


class TestInvariantsIntact:
    def test_ordering_and_verification_preserved(self) -> None:
        """Schema constraints do not change ordering or verified hand-off."""
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "invariant",
            (
                StageSpec(
                    agent_name="case_tool",
                    granted_tools=("to_upper",),
                    output_schema={"text": "str"},
                ),
                StageSpec(agent_name="reverse", input_schema={"text": "str"}),
            ),
            {"text": "aab"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # case_tool('aab')='AAB', reverse('AAB')='BAA'.
        assert outcome.result.output == "BAA"

    def test_resource_accounting_intact(self, tmp_path: Path) -> None:
        """Resource accounting still records each stage's consumption."""
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "accounting",
            (
                StageSpec(
                    agent_name="case_tool",
                    granted_tools=("to_upper",),
                    output_schema={"text": "str"},
                ),
                StageSpec(agent_name="reverse", input_schema={"text": "str"}),
            ),
            {"text": "hello"},
        )
        outcome = m.run(task)
        assert outcome.result is not None
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        accounting = next(
            (
                s.output
                for s in stored.steps
                if s.description == "pipeline resource accounting" and isinstance(s.output, dict)
            ),
            None,
        )
        assert accounting is not None
        assert len(accounting["stages"]) == 2
        assert accounting["total_steps"] == len(stored.steps)
