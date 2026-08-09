"""Minimal operator CLI for the Meta-Harness kernel (Volley 017).

This is a local-only, fail-closed command-line interface over the public
surface. It provides three operations:

- ``run`` — execute a deterministic demo task set against a file-backed store
  and print each outcome and its durable trajectory id.
- ``summarise <trajectory_id>`` — load a durable trajectory and print its
  deterministic summary.
- ``replay-verify <trajectory_id>`` — re-run the demo task that produced the
  stored trajectory and verify the fresh trajectory is equivalent.

It never starts a network service, never reads credentials, and exits with a
non-zero status on any failure (fail-closed). The demo tasks are deterministic
and use only the built-in agents and the stub model provider, so trajectories
replay identically.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from meta_harness import AgentManager
from meta_harness.contracts.capability import Capability
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.contracts.tool import ToolDescriptor
from meta_harness.contracts.workspace import WorkspaceLayout
from meta_harness.control_plane.email_tools import EmailTools, email_tool_impls
from meta_harness.control_plane.tools import (
    EMAIL_FETCH_DESCRIPTOR,
    EMAIL_LIST_DESCRIPTOR,
    ToolRegistry,
)
from meta_harness.control_plane.trajectory_store import FileTrajectoryStore
from meta_harness.control_plane.workspace import Workspace, register_workspace_tools
from meta_harness.providers import StubModelProvider
from meta_harness.providers.email import FakeEmailGateway

# The built-in demo agents, registered by the CLI so a demo task set is
# self-contained and deterministic.
_COUNTER_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="counter",
    entry_point="meta_harness.agents.counter:create_counter_agent",
    description="Counts occurrences of a target character in a string.",
    declared_capabilities=frozenset({Capability(name="count", version="1")}),
)

_REVERSE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="reverse",
    entry_point="meta_harness.agents.reverse:create_reverse_agent",
    description="Reverses a string.",
    declared_capabilities=frozenset({Capability(name="reverse", version="1")}),
)

_CASE_TOOL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="case_tool",
    entry_point="meta_harness.agents.case_tool:create_case_tool_agent",
    description="Uppercases a string via a mediated tool.",
    declared_capabilities=frozenset(),
)

_MODEL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="model",
    entry_point="meta_harness.agents.model_agent:create_model_agent",
    description="Answers a constrained prompt via a mediated language model.",
    declared_capabilities=frozenset({Capability(name="llm", version="1")}),
)

_BILLS_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="bills",
    entry_point="meta_harness.agents.bills:create_bills_agent",
    description="Computes deterministic totals for a structured bill.",
    declared_capabilities=frozenset({Capability(name="bills", version="1")}),
)

_WORKSPACE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="workspace",
    entry_point="meta_harness.agents.workspace:create_workspace_agent",
    description="Performs an allowlisted workspace operation via mediated file tools.",
    declared_capabilities=frozenset({Capability(name="workspace", version="1")}),
)

_EMAIL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="email",
    entry_point="meta_harness.agents.email:create_email_agent",
    description="Performs a read-only email operation via mediated tools.",
    declared_capabilities=frozenset({Capability(name="email.read", version="1")}),
)

_DEMO_MANIFESTS = (
    _COUNTER_MANIFEST,
    _REVERSE_MANIFEST,
    _CASE_TOOL_MANIFEST,
    _MODEL_MANIFEST,
    _BILLS_MANIFEST,
    _WORKSPACE_MANIFEST,
    _EMAIL_MANIFEST,
)


def _demo_tasks() -> tuple[TaskSpecification, ...]:
    """The deterministic demo task set, keyed by stable ``task_id``.

    Each task uses only built-in agents and the stub model provider, so every
    run produces an identical, replayable trajectory for the same task id.
    """
    return (
        TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="demo-counter",
            agent_name="counter",
            payload={"text": "the quick brown fox jumps over the lazy dog", "target": "o"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="demo-reverse",
            capability=Capability(name="reverse", version="1"),
            payload={"text": "agent-centric"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="demo-tool",
            agent_name="case_tool",
            payload={"text": "mediated tools"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("to_upper",),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="demo-model",
            agent_name="model",
            payload={"prompt": "hello", "expected": "stub response"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("llm_complete",),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V4,
            task_id="demo-pipeline",
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
        TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="demo-bills",
            agent_name="bills",
            payload={
                "lines": [
                    {"description": "widget", "quantity": 2, "unit_price_cents": 1000},
                    {"description": "gadget", "quantity": 3, "unit_price_cents": 250},
                ],
                "discount_bps": 1000,
                "tax_bps": 500,
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("bill_total",),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="demo-workspace",
            agent_name="workspace",
            payload={
                "operation": "write",
                "relative_path": "invoices/note.txt",
                "content": "hello workspace",
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("create_workspace_dir", "write_workspace_file"),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="demo-email",
            agent_name="email",
            payload={"operation": "list", "folder": "INBOX", "limit": 10},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("email_list",),
        ),
    )


def _email_tool_descriptor(name: str) -> ToolDescriptor:
    """Return the ToolDescriptor for an email tool name."""
    if name == "email_list":
        return EMAIL_LIST_DESCRIPTOR
    if name == "email_fetch":
        return EMAIL_FETCH_DESCRIPTOR
    raise AssertionError(f"unknown email tool {name!r}")


def _manager(store_dir: Path) -> AgentManager:
    # The stub model provider makes the demo-model task deterministic and
    # replayable without any network access or credentials.
    tools = ToolRegistry(model_provider=StubModelProvider())
    # A small, allowlisted workspace for the demo-workspace task. It lives under
    # the trajectory store directory so it is local, self-contained, and does
    # not touch the repository.
    workspace = Workspace(
        store_dir / "workspace",
        WorkspaceLayout(files=("invoices/note.txt",), directories=("invoices",)),
    )
    register_workspace_tools(tools, workspace)
    # Pre-create the allowlisted directory so the demo-workspace write task can
    # run deterministically (writes require an existing parent directory).
    workspace.create_workspace_dir("invoices")
    # A fake, deterministic read-only email gateway for the demo-email task; the
    # real IMAP path stays opt-in and off by default (no network in the demo).
    email = EmailTools(
        FakeEmailGateway(
            mailbox={
                "INBOX": (
                    {
                        "id": "m1",
                        "subject": "Hello",
                        "from": "a@example.test",
                        "date": "2026-08-01",
                        "body": "first",
                    },
                ),
            }
        ),
        default_folders=("INBOX",),
    )
    for _name, _impl in email_tool_impls(email).items():
        tools.register_impl(_email_tool_descriptor(_name), _impl)
    manager = AgentManager(store=FileTrajectoryStore(store_dir), tools=tools)
    for manifest in _DEMO_MANIFESTS:
        manager.register(manifest)
    return manager


def _cmd_run(store_dir: Path) -> int:
    """Run the deterministic demo task set and print each outcome."""
    manager = _manager(store_dir)
    for task in _demo_tasks():
        outcome = manager.run(task)
        if outcome.result is not None:
            print(
                f"{task.task_id}: VERIFIED output={outcome.result.output!r} "
                f"trajectory_id={outcome.trajectory_id}"
            )
        else:
            assert outcome.failure is not None
            print(
                f"{task.task_id}: FAILURE[{outcome.failure.reason.value}] "
                f"{outcome.failure.message} trajectory_id={outcome.trajectory_id}"
            )
            return 1
    return 0


def _cmd_summarise(store_dir: Path, trajectory_id: str) -> int:
    """Print the deterministic summary for a stored trajectory."""
    manager = _manager(store_dir)
    summary = manager.summarise(trajectory_id)
    if summary is None:
        print(f"error: no trajectory with id {trajectory_id!r} in {store_dir}", file=sys.stderr)
        return 1
    print(f"trajectory_id: {summary.trajectory_id}")
    print(f"task_id:       {summary.task_id}")
    print(f"agent:         {summary.agent_name}")
    print(f"agents:        {', '.join(summary.agents)}")
    print(f"state:         {summary.state.value}")
    if summary.failure_reason is not None:
        print(f"failure:       {summary.failure_reason}: {summary.failure_message}")
    if summary.state.value == "verified":
        print(f"output:        {summary.output!r}")
    print(f"stage_kind:    {summary.stage_kind.value}")
    for stage in summary.stages:
        print(f"  stage {stage.index}: {stage.agent} [{stage.status}]")
    for tool in summary.tools:
        print(
            f"tool:          {tool.name} granted={tool.granted} "
            f"requests={tool.requests} ok={tool.succeeded} "
            f"fail={tool.failed} rejected={tool.rejected}"
        )
    print(f"steps:         {summary.steps}")
    print(f"time_seconds:  {summary.approximate_time_seconds:.4f}")
    print(f"cancellations: {summary.cancellations}")
    return 0


def _cmd_replay_verify(store_dir: Path, trajectory_id: str) -> int:
    """Re-run the demo task that produced a stored trajectory and verify it."""
    manager = _manager(store_dir)
    stored = manager.load(trajectory_id)
    if stored is None:
        print(f"error: no trajectory with id {trajectory_id!r} in {store_dir}", file=sys.stderr)
        return 1
    task = _task_for_id(stored.task_id)
    if task is None:
        print(
            f"error: trajectory {trajectory_id!r} was not produced by a demo task "
            f"(task_id={stored.task_id!r}); replay-verify only supports demo tasks",
            file=sys.stderr,
        )
        return 1
    result = manager.replay(task, trajectory_id)
    if result.passed:
        print(
            f"replay-verify: PASSED (original={result.original_trajectory_id} "
            f"replayed={result.replayed_trajectory_id})"
        )
        return 0
    print(f"replay-verify: FAILED (original={result.original_trajectory_id})", file=sys.stderr)
    for diff in result.diffs:
        print(f"  diff: {diff.field}: {diff.original!r} != {diff.replayed!r}", file=sys.stderr)
    print(f"  {result.message}", file=sys.stderr)
    return 1


def _task_for_id(task_id: str) -> TaskSpecification | None:
    for task in _demo_tasks():
        if task.task_id == task_id:
            return task
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-harness",
        description="Meta-Harness kernel operator CLI (local-only, fail-closed).",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=Path("examples/.trajectories"),
        help="Directory for the durable trajectory store (default: examples/.trajectories).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Run the deterministic demo task set.")

    p_sum = sub.add_parser("summarise", help="Summarise a trajectory by id.")
    p_sum.add_argument("trajectory_id", help="The durable trajectory id to summarise.")

    p_replay = sub.add_parser("replay-verify", help="Re-run and verify a demo trajectory.")
    p_replay.add_argument("trajectory_id", help="The durable trajectory id to verify.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(args.store)
    if args.command == "summarise":
        return _cmd_summarise(args.store, args.trajectory_id)
    if args.command == "replay-verify":
        return _cmd_replay_verify(args.store, args.trajectory_id)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())