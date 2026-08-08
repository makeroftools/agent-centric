"""Tests for the thin Policy component (Volley 008).

These tests prove that a task or composition can carry a thin, deterministic
policy that the Manager enforces before any agent is instantiated or any stage
begins: allowed work proceeds, denied agents/capabilities/tools are rejected
before execution, violations are fail-closed and fully audited, and the absence
of a policy preserves current behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.capability import Capability
from meta_harness.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from meta_harness.contracts.policy import Policy, PolicyVersion
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import (
    CASE_TOOL_MANIFEST,
    COUNTER_CAPABILITY,
    COUNTER_MANIFEST,
    REVERSE_CAPABILITY,
    REVERSE_MANIFEST,
)


def _policy(**kwargs: Any) -> Policy:
    return Policy(version=PolicyVersion.V1, **kwargs)


def _single_task(
    task_id: str,
    *,
    agent_name: str | None = None,
    capability: Capability | None = None,
    granted_tools: tuple[str, ...] = (),
    policy: Policy | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name=agent_name,
        capability=capability,
        payload={"text": "abc"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        granted_tools=granted_tools,
        policy=policy,
    )


def _pipeline_task(
    task_id: str,
    stages: tuple[StageSpec, ...],
    *,
    policy: Policy | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        payload={"text": "abc"},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
        pipeline=SequentialComposition(version=PipelineVersion.V3, stages=stages),
        policy=policy,
    )


class TestPolicyContract:
    def test_deny_overrides_allow(self) -> None:
        p = _policy(allow_agents=frozenset({"reverse"}), deny_agents=frozenset({"reverse"}))
        assert not p.check_agent("reverse").allowed

    def test_allow_set_restricts(self) -> None:
        p = _policy(allow_agents=frozenset({"reverse"}))
        assert p.check_agent("reverse").allowed
        assert not p.check_agent("counter").allowed

    def test_empty_allow_is_open(self) -> None:
        p = _policy()
        assert p.check_agent("anything").allowed
        assert p.check_tool("to_upper").allowed

    def test_capability_check(self) -> None:
        p = _policy(deny_capabilities=frozenset({COUNTER_CAPABILITY}))
        assert not p.check_capability(COUNTER_CAPABILITY).allowed
        assert p.check_capability(REVERSE_CAPABILITY).allowed

    def test_tool_check(self) -> None:
        p = _policy(deny_tools=frozenset({"to_upper"}))
        assert not p.check_tool("to_upper").allowed
        assert p.check_tool("add").allowed

    def test_invalid_version_rejected(self) -> None:
        with pytest.raises(ValueError):
            Policy(version="policy.nope")  # type: ignore[arg-type]


class TestAllowedWork:
    def test_allowed_agent_proceeds(self) -> None:
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _single_task(
            "allow-agent",
            agent_name="reverse",
            policy=_policy(allow_agents=frozenset({"reverse"})),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "cba"

    def test_allowed_capability_proceeds(self) -> None:
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _single_task(
            "allow-cap",
            capability=REVERSE_CAPABILITY,
            policy=_policy(allow_capabilities=frozenset({REVERSE_CAPABILITY})),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "cba"

    def test_allowed_tool_proceeds(self) -> None:
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        task = _single_task(
            "allow-tool",
            agent_name="case_tool",
            granted_tools=("to_upper",),
            policy=_policy(allow_tools=frozenset({"to_upper"})),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "ABC"

    def test_policy_accepted_recorded_in_trajectory(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _single_task(
            "accept-record",
            agent_name="reverse",
            policy=_policy(allow_agents=frozenset({"reverse"})),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "policy accepted" for d in descriptions)


class TestDeniedAgentCapability:
    def test_denied_agent_rejected_before_execution(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _single_task(
            "deny-agent",
            agent_name="reverse",
            policy=_policy(deny_agents=frozenset({"reverse"})),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION
        assert "reverse" in outcome.failure.message

        # No agent work occurred: no agent steps, only the policy rejection.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "policy rejected" for d in descriptions)
        assert not any("reversed chunk" in d for d in descriptions)

    def test_denied_capability_rejected(self) -> None:
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _single_task(
            "deny-cap",
            capability=REVERSE_CAPABILITY,
            policy=_policy(deny_capabilities=frozenset({REVERSE_CAPABILITY})),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION

    def test_not_in_allow_set_rejected(self) -> None:
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        m.register(COUNTER_MANIFEST)
        task = _single_task(
            "not-allowed",
            agent_name="counter",
            policy=_policy(allow_agents=frozenset({"reverse"})),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestDeniedTool:
    def test_denied_tool_rejected_even_if_granted(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        # to_upper is in granted_tools but denied by policy -> rejected.
        task = _single_task(
            "deny-tool",
            agent_name="case_tool",
            granted_tools=("to_upper",),
            policy=_policy(deny_tools=frozenset({"to_upper"})),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION
        assert "to_upper" in outcome.failure.message

        # No agent work occurred.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "policy rejected" for d in descriptions)
        assert not any("tool" in d for d in descriptions)

    def test_denied_tool_not_in_allow_set_rejected(self) -> None:
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        task = _single_task(
            "tool-not-allowed",
            agent_name="case_tool",
            granted_tools=("to_upper",),
            policy=_policy(allow_tools=frozenset({"add"})),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestCompositionPolicy:
    def test_stage_agent_denied_rejected_before_any_stage(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "stage-deny",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            policy=_policy(deny_agents=frozenset({"reverse"})),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION

        # No stage began.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert not any("stage 0 begin" in d for d in descriptions)
        assert any(d == "policy rejected" for d in descriptions)

    def test_stage_tool_denied_rejected(self) -> None:
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "stage-tool-deny",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            policy=_policy(deny_tools=frozenset({"to_upper"})),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION

    def test_composition_allowed_proceeds(self) -> None:
        m = AgentManager()
        m.register(CASE_TOOL_MANIFEST)
        m.register(REVERSE_MANIFEST)
        task = _pipeline_task(
            "stage-allow",
            (
                StageSpec(agent_name="case_tool", granted_tools=("to_upper",)),
                StageSpec(agent_name="reverse"),
            ),
            policy=_policy(
                allow_agents=frozenset({"case_tool", "reverse"}),
                allow_tools=frozenset({"to_upper"}),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        # case_tool('abc')='ABC', reverse('ABC')='CBA'.
        assert outcome.result.output == "CBA"


class TestNoPolicy:
    def test_absence_of_policy_preserves_behaviour(self) -> None:
        m = AgentManager()
        m.register(REVERSE_MANIFEST)
        task = _single_task("no-policy", agent_name="reverse")
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "cba"

    def test_no_policy_means_no_policy_step(self, tmp_path: Path) -> None:
        m = AgentManager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = _single_task("no-policy-step", agent_name="reverse")
        outcome = m.run(task)
        assert outcome.result is not None
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert not any("policy" in d for d in descriptions)
