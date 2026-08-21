"""End-to-end demo of the Agent-centric control plane.

Shows two governed agents (CounterAgent and ReverseAgent) running under strict
resource envelopes, selected by explicit identity or by capability, with full
recorded trajectories and the mandatory verification gate.

Also demonstrates durability: trajectories are persisted to a file-based,
append-only store and can be replayed after the Manager (and its in-memory
state) is discarded.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric import AgentManager
from agent_centric.contracts.capability import Capability
from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore

COUNTER_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="counter",
    entry_point="agent_centric.agents.counter:create_counter_agent",
    description="Counts occurrences of a target character in a string.",
    declared_capabilities=frozenset({Capability(name="count", version="1")}),
)

REVERSE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="reverse",
    entry_point="agent_centric.agents.reverse:create_reverse_agent",
    description="Reverses a string.",
    declared_capabilities=frozenset({Capability(name="reverse", version="1")}),
)

CASE_TOOL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="case_tool",
    entry_point="agent_centric.agents.case_tool:create_case_tool_agent",
    description="Uppercases a string via a mediated tool.",
    declared_capabilities=frozenset(),
)


def run_and_print(manager: AgentManager, task: TaskSpecification, label: str) -> None:
    outcome = manager.run(task)
    if outcome.result is not None:
        print(f"{label}: VERIFIED RESULT = {outcome.result.output!r} "
              f"(agent={outcome.result.trajectory.agent_name}, "
              f"trajectory_id={outcome.trajectory_id})")
        print("  TRAJECTORY:")
        for step in outcome.result.trajectory.steps:
            print(f"    [{step.step_index}] {step.description} {step.output}")
    else:
        assert outcome.failure is not None
        print(f"{label}: FAILURE[{outcome.failure.reason.value}] {outcome.failure.message} "
              f"(trajectory_id={outcome.trajectory_id})")


def main() -> None:
    store_dir = Path("examples/.trajectories")
    # Start from a clean store so the demo is reproducible on every run.
    if store_dir.exists():
        for p in store_dir.iterdir():
            p.unlink()
    manager = AgentManager(store=FileTrajectoryStore(store_dir))
    manager.register(COUNTER_MANIFEST)
    manager.register(REVERSE_MANIFEST)
    manager.register(CASE_TOOL_MANIFEST)

    run_and_print(
        manager,
        TaskSpecification(
            version=TaskSpecVersion.V2,
            task_id="demo-001",
            agent_name="counter",
            payload={"text": "the quick brown fox jumps over the lazy dog", "target": "o"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        ),
        "counter by name",
    )

    run_and_print(
        manager,
        TaskSpecification(
            version=TaskSpecVersion.V2,
            task_id="demo-002",
            capability=Capability(name="reverse", version="1"),
            payload={"text": "agent-centric"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        ),
        "reverse by capability",
    )

    # Demonstrate step-limit enforcement for a capability-selected agent.
    run_and_print(
        manager,
        TaskSpecification(
            version=TaskSpecVersion.V2,
            task_id="demo-003",
            capability=Capability(name="reverse", version="1"),
            payload={"text": "x" * 1000},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
        ),
        "reverse by capability with step limit 1",
    )

    # Demonstrate mediated tool access: granted tool succeeds; ungranted fails.
    run_and_print(
        manager,
        TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="demo-004",
            agent_name="case_tool",
            payload={"text": "mediated tools"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("to_upper",),
        ),
        "case_tool with to_upper granted",
    )
    run_and_print(
        manager,
        TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="demo-005",
            agent_name="case_tool",
            payload={"text": "mediated tools"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),  # to_upper NOT granted
        ),
        "case_tool without to_upper granted",
    )

    # Demonstrate Manager-orchestrated sequential composition: case_tool then
    # reverse, with a per-stage envelope on stage 0 and composition accounting.
    run_and_print(
        manager,
        TaskSpecification(
            version=TaskSpecVersion.V4,
            task_id="demo-006",
            payload={"text": "sequential composition"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
            pipeline=SequentialComposition(
                version=PipelineVersion.V2,
                stages=(
                    StageSpec(
                        agent_name="case_tool",
                        granted_tools=("to_upper",),
                        stage_envelope=ResourceEnvelope(timeout_seconds=5.0, max_steps=50),
                    ),
                    StageSpec(agent_name="reverse"),
                ),
            ),
        ),
        "pipeline: case_tool -> reverse (per-stage envelope)",
    )

    # Demonstrate durability: discard the Manager and replay from disk.
    print("\nReplaying durable trajectories after discarding the Manager...")
    replayer = AgentManager(store=FileTrajectoryStore(store_dir))
    for tid in (
        "demo-001#0", "demo-002#1", "demo-003#2", "demo-004#3",
        "demo-005#4", "demo-006#5",
    ):
        stored = replayer.load(tid)
        if stored is None:
            print(f"  {tid}: NOT FOUND")
            continue
        print(f"  {tid}: task={stored.task_id!r} agent={stored.agent_name!r} "
              f"outcome={stored.outcome.kind} steps={len(stored.steps)}")


if __name__ == "__main__":
    main()