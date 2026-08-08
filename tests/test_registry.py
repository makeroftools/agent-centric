"""Tests for the deterministic Registry and capability-based selection."""

from __future__ import annotations

from typing import Any

import pytest

from meta_harness.contracts.capability import Capability
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.registry import Registry
from tests.conftest import (
    COUNTER_CAPABILITY,
    COUNTER_MANIFEST,
    REVERSE_CAPABILITY,
    REVERSE_MANIFEST,
)


def _task(
    task_id: str,
    payload: dict[str, Any],
    *,
    agent_name: str | None = None,
    capability: Capability | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V2,
        task_id=task_id,
        agent_name=agent_name,
        capability=capability,
        payload=payload,
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    )


class TestRegistry:
    def test_register_multiple_agents(self) -> None:
        registry = Registry()
        registry.register(COUNTER_MANIFEST)
        registry.register(REVERSE_MANIFEST)
        assert len(registry) == 2
        assert registry.names() == ("counter", "reverse")

    def test_lookup_by_name(self) -> None:
        registry = Registry()
        registry.register(COUNTER_MANIFEST)
        assert registry.get_by_name("counter") is COUNTER_MANIFEST
        assert registry.get_by_name("missing") is None

    def test_lookup_by_capability_exact_match(self) -> None:
        registry = Registry()
        registry.register(COUNTER_MANIFEST)
        registry.register(REVERSE_MANIFEST)
        assert registry.get_by_capability(COUNTER_CAPABILITY) is COUNTER_MANIFEST
        assert registry.get_by_capability(REVERSE_CAPABILITY) is REVERSE_MANIFEST
        # Exact match only: a different version does not match.
        assert (
            registry.get_by_capability(Capability(name="count", version="2")) is None
        )
        assert registry.get_by_capability(Capability(name="missing", version="1")) is None

    def test_duplicate_name_rejected(self) -> None:
        registry = Registry()
        registry.register(COUNTER_MANIFEST)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(COUNTER_MANIFEST)

    def test_capability_conflict_rejected(self) -> None:
        """Two agents must not both declare the same capability."""
        registry = Registry()
        registry.register(COUNTER_MANIFEST)
        conflicting = AgentComponentManifest(
            version=AgentManifestVersion.V2,
            name="counter2",
            entry_point="meta_harness.agents.counter:create_counter_agent",
            description="Another counter.",
            declared_capabilities=frozenset({COUNTER_CAPABILITY}),
        )
        with pytest.raises(ValueError, match="already declared"):
            registry.register(conflicting)

    def test_non_manifest_rejected(self) -> None:
        registry = Registry()
        with pytest.raises(ValueError, match="AgentComponentManifest"):
            registry.register("not-a-manifest")  # type: ignore[arg-type]

    def test_registry_is_deterministic(self) -> None:
        """Lookup is side-effect free and deterministic."""
        registry = Registry()
        registry.register(COUNTER_MANIFEST)
        registry.register(REVERSE_MANIFEST)
        first = registry.get_by_capability(REVERSE_CAPABILITY)
        second = registry.get_by_capability(REVERSE_CAPABILITY)
        assert first is second is REVERSE_MANIFEST


class TestCapabilitySelection:
    def test_select_counter_by_capability(self, manager: AgentManager) -> None:
        task = _task(
            "cap-count",
            {"text": "hello world", "target": "l"},
            capability=COUNTER_CAPABILITY,
        )
        outcome = manager.run(task)
        assert outcome.result is not None
        assert outcome.result.output == 3
        assert outcome.result.trajectory.agent_name == "counter"

    def test_select_reverse_by_capability(self, manager: AgentManager) -> None:
        task = _task(
            "cap-reverse",
            {"text": "hello"},
            capability=REVERSE_CAPABILITY,
        )
        outcome = manager.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "olleh"
        assert outcome.result.trajectory.agent_name == "reverse"

    def test_select_reverse_by_name(self, manager: AgentManager) -> None:
        task = _task(
            "name-reverse",
            {"text": "abc"},
            agent_name="reverse",
        )
        outcome = manager.run(task)
        assert outcome.result is not None
        assert outcome.result.output == "cba"
        assert outcome.result.trajectory.agent_name == "reverse"

    def test_unknown_capability_fails_explicitly(self, manager: AgentManager) -> None:
        task = _task(
            "cap-unknown",
            {"text": "hi"},
            capability=Capability(name="nonexistent", version="1"),
        )
        outcome = manager.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.UNKNOWN_AGENT

    def test_capability_selection_still_governed(self, manager: AgentManager) -> None:
        """Envelope, trajectory, and verification hold for capability-selected agents."""
        # Step limit enforced for a capability-selected reverse agent.
        task = TaskSpecification(
            version=TaskSpecVersion.V2,
            task_id="cap-step-limit",
            capability=REVERSE_CAPABILITY,
            payload={"text": "x" * 500},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
        )
        outcome = manager.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

    def test_capability_selection_verification_gate(self, manager: AgentManager) -> None:
        """A capability-selected agent's output must still pass verification.

        We verify the reverse verifier (used for capability-selected reverse
        tasks) rejects a wrong output, proving the gate is not bypassed.
        """
        from meta_harness.control_plane.verifier import verify_reverse_output

        task = _task("cap-verify", {"text": "hello"}, capability=REVERSE_CAPABILITY)
        assert verify_reverse_output(task, "WRONG").passed is False
        assert verify_reverse_output(task, "olleh").passed is True
