"""Minimal operator CLI for the Agent-centric kernel (Volley 017).

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
import json
import sys
from pathlib import Path
from typing import Any

# Make CLI output stream live, even when piped (``agent-centric fbp | ...``),
# so an operator sees progress as it happens rather than in bursts only when
# the pipe buffer flushes. Line-buffered stdout is the safe minimum.
try:
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[union-attr]
except (ValueError, OSError):  # pragma: no cover - e.g. detached streams
    pass

from agent_centric import AgentManager
from agent_centric.contracts.capability import Capability
from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from agent_centric.contracts.pipeline import PipelineVersion, SequentialComposition, StageSpec
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.contracts.tool import ToolDescriptor
from agent_centric.contracts.workspace import WorkspaceLayout
from agent_centric.control_plane.bills_registry import BillsOps, bills_tool_impls
from agent_centric.control_plane.email_tools import EmailTools, email_tool_impls
from agent_centric.control_plane.intake import IntakeOps, ensure_intake_layout, intake_tool_impls
from agent_centric.control_plane.tools import (
    BILLS_CALENDAR_DESCRIPTOR,
    BILLS_REGISTRY_MARK_PAID_DESCRIPTOR,
    BILLS_REGISTRY_MARK_STATUS_DESCRIPTOR,
    BILLS_REGISTRY_READ_DESCRIPTOR,
    BILLS_REGISTRY_UPSERT_DESCRIPTOR,
    EMAIL_FETCH_DESCRIPTOR,
    EMAIL_LIST_DESCRIPTOR,
    INBOX_INVENTORY_DESCRIPTOR,
    INTAKE_ACCEPT_DESCRIPTOR,
    INTAKE_DRAFTS_DESCRIPTOR,
    INTAKE_EMAIL_DRAFT_DESCRIPTOR,
    ToolRegistry,
)
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from agent_centric.control_plane.workspace import Workspace, register_workspace_tools
from agent_centric.providers import StubModelProvider
from agent_centric.providers.email import FakeEmailGateway

# The built-in demo agents, registered by the CLI so a demo task set is
# self-contained and deterministic.
_COUNTER_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="counter",
    entry_point="agent_centric.agents.counter:create_counter_agent",
    description="Counts occurrences of a target character in a string.",
    declared_capabilities=frozenset({Capability(name="count", version="1")}),
)

_REVERSE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="reverse",
    entry_point="agent_centric.agents.reverse:create_reverse_agent",
    description="Reverses a string.",
    declared_capabilities=frozenset({Capability(name="reverse", version="1")}),
)

_CASE_TOOL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="case_tool",
    entry_point="agent_centric.agents.case_tool:create_case_tool_agent",
    description="Uppercases a string via a mediated tool.",
    declared_capabilities=frozenset(),
)

_MODEL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="model",
    entry_point="agent_centric.agents.model_agent:create_model_agent",
    description="Answers a constrained prompt via a mediated language model.",
    declared_capabilities=frozenset({Capability(name="llm", version="1")}),
)

_BILLS_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="bills",
    entry_point="agent_centric.agents.bills:create_bills_agent",
    description="Computes deterministic totals for a structured bill.",
    declared_capabilities=frozenset({Capability(name="bills", version="1")}),
)

_WORKSPACE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="workspace",
    entry_point="agent_centric.agents.workspace:create_workspace_agent",
    description="Performs an allowlisted workspace operation via mediated file tools.",
    declared_capabilities=frozenset({Capability(name="workspace", version="1")}),
)

_EMAIL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="email",
    entry_point="agent_centric.agents.email:create_email_agent",
    description="Performs a read-only email operation via mediated tools.",
    declared_capabilities=frozenset({Capability(name="email.read", version="1")}),
)

_BILLS_REGISTRY_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="bills_registry",
    entry_point="agent_centric.agents.bills_registry:create_bills_registry_agent",
    description="Reads the bills registry, projects a deterministic agenda, and maintains bills.",
    declared_capabilities=frozenset(
        {
            Capability(name="bills.registry", version="1"),
            Capability(name="bills.calendar", version="1"),
            Capability(name="bills.maintain", version="1"),
        }
    ),
)

_INTAKE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="intake",
    entry_point="agent_centric.agents.intake:create_intake_agent",
    description="Runs a dump-intake operation: inventory, drafts, email draft, or explicit accept.",
    declared_capabilities=frozenset(
        {
            Capability(name="intake.inventory", version="1"),
            Capability(name="intake.draft_bills", version="1"),
            Capability(name="intake.draft_from_email", version="1"),
            Capability(name="intake.accept_bills", version="1"),
        }
    ),
)

# A demo bills registry, written into the allowlisted workspace so the demo
# calendar task projects a deterministic agenda.
_DEMO_REGISTRY = {
    "version": "bills_registry.v1",
    "description": "demo registry",
    "bills": [
        {
            "id": "b1",
            "vendor": "NetCo",
            "amount_cents": 3000,
            "due_date": "2026-09-01",
            "status": "due",
        },
        {
            "id": "b2",
            "vendor": "WaterCo",
            "amount_cents": 2000,
            "due_date": "2026-09-05",
            "status": "paid",
        },
        {
            "id": "b3",
            "vendor": "PowerCo",
            "amount_cents": 5000,
            "due_date": "2026-09-10",
            "status": "due",
        },
    ],
}

_DEMO_MANIFESTS = (
    _COUNTER_MANIFEST,
    _REVERSE_MANIFEST,
    _CASE_TOOL_MANIFEST,
    _MODEL_MANIFEST,
    _BILLS_MANIFEST,
    _WORKSPACE_MANIFEST,
    _EMAIL_MANIFEST,
    _BILLS_REGISTRY_MANIFEST,
    _INTAKE_MANIFEST,
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
        TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="demo-bills-calendar",
            agent_name="bills_registry",
            payload={
                "operation": "calendar",
                "registry": _DEMO_REGISTRY,
                "from_date": "2026-09-01",
                "to_date": "2026-09-30",
                "include_paid": False,
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("bills_calendar",),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="demo-bills-mark-paid",
            agent_name="bills_registry",
            payload={
                "operation": "mark_paid",
                "registry": _DEMO_REGISTRY,
                "bill_id": "b3",
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("bills_registry_mark_paid",),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="demo-intake",
            agent_name="intake",
            payload={"operation": "drafts"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("intake_drafts",),
        ),
        TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="demo-intake-email-draft",
            agent_name="intake",
            payload={
                "operation": "draft_from_email",
                "message": {
                    "id": "m-bill",
                    "folder": "INBOX",
                    "subject": "Your bill from GasCo",
                    "from_address": "billing@gasco.example",
                    "date": "2026-08-05",
                    "body": "Amount total: $123.45. Due date: 2026-09-20.",
                },
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("intake_email_draft",),
        ),
    )


def _email_tool_descriptor(name: str) -> ToolDescriptor:
    """Return the ToolDescriptor for an email tool name."""
    if name == "email_list":
        return EMAIL_LIST_DESCRIPTOR
    if name == "email_fetch":
        return EMAIL_FETCH_DESCRIPTOR
    raise AssertionError(f"unknown email tool {name!r}")


def _bills_tool_descriptor(name: str) -> ToolDescriptor:
    """Return the ToolDescriptor for a bills-registry tool name."""
    return {
        "bills_registry_read": BILLS_REGISTRY_READ_DESCRIPTOR,
        "bills_calendar": BILLS_CALENDAR_DESCRIPTOR,
        "bills_registry_upsert": BILLS_REGISTRY_UPSERT_DESCRIPTOR,
        "bills_registry_mark_paid": BILLS_REGISTRY_MARK_PAID_DESCRIPTOR,
        "bills_registry_mark_status": BILLS_REGISTRY_MARK_STATUS_DESCRIPTOR,
    }[name]

def _intake_tool_descriptor(name: str) -> ToolDescriptor:
    """Return the ToolDescriptor for an intake tool name."""
    return {
        "inbox_inventory": INBOX_INVENTORY_DESCRIPTOR,
        "intake_drafts": INTAKE_DRAFTS_DESCRIPTOR,
        "intake_accept": INTAKE_ACCEPT_DESCRIPTOR,
        "intake_email_draft": INTAKE_EMAIL_DRAFT_DESCRIPTOR,
    }[name]


def _manager(store_dir: Path) -> AgentManager:
    # The stub model provider makes the demo-model task deterministic and
    # replayable without any network access or credentials.
    tools = ToolRegistry(model_provider=StubModelProvider())
    # A small, allowlisted workspace for the demo tasks. It lives under the
    # trajectory store directory so it is local, self-contained, and does not
    # touch the repository. The bills-registry layout is added via
    # ensure_bills_layout so the registry can live on the allowlist.
    workspace = Workspace(
        store_dir / "workspace",
        ensure_intake_layout(
            WorkspaceLayout(files=("invoices/note.txt",), directories=("invoices",))
        ),
    )
    register_workspace_tools(tools, workspace)
    # Pre-create the allowlisted directories so writes can run deterministically.
    workspace.create_workspace_dir("invoices")
    workspace.create_workspace_dir("bills")
    workspace.write_workspace_file("bills/registry.json", json.dumps(_DEMO_REGISTRY))
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
                    {
                        "id": "m-bill",
                        "subject": "Your bill from GasCo",
                        "from": "billing@gasco.example",
                        "date": "2026-08-05",
                        "body": "Amount total: $123.45. Due date: 2026-09-20.",
                    },
                ),
            }
        ),
        default_folders=("INBOX",),
    )
    for _name, _impl in email_tool_impls(email).items():
        tools.register_impl(_email_tool_descriptor(_name), _impl)
    # The deterministic bills-registry read + calendar tools, bound to the
    # allowlisted workspace registry.
    bills_ops = BillsOps(workspace)
    for _name, _impl in bills_tool_impls(bills_ops).items():
        tools.register_impl(_bills_tool_descriptor(_name), _impl)
    # A deterministic dump-intake inbox for the demo-intake task. The inbox and
    # registry are on the allowlist; drafts stay unverified until an explicit
    # accept (which the demo does not perform).
    workspace.create_workspace_dir("inbox")
    workspace.write_workspace_file(
        "inbox/bill1.json",
        json.dumps(
            {
                "vendor": "PostCo",
                "amount_cents": 1200,
                "due_date": "2026-09-15",
                "notes": "demo inbox draft",
            }
        ),
    )
    intake_ops = IntakeOps(workspace)
    for _name, _impl in intake_tool_impls(intake_ops).items():
        tools.register_impl(_intake_tool_descriptor(_name), _impl)
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
        prog="agent-centric",
        description="Agent-centric kernel operator CLI (local-only, fail-closed).",
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

    p_fbp = sub.add_parser("fbp", help="Drive the FBP subsystem demo (inproc, offline).")
    p_fbp.add_argument(
        "--transport",
        choices=("inproc", "tcp", "ipc"),
        default="inproc",
        help="Transport to prove the protocol over (default: inproc).",
    )
    p_fbp.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Optional durable directive-ledger path to record the demo session to.",
    )

    p_fbp_replay = sub.add_parser(
        "fbp-replay",
        help="Re-open a durable directive ledger and re-verify (replay) it.",
    )
    p_fbp_replay.add_argument("ledger_path", type=Path, help="The durable ledger file.")
    p_fbp_replay.add_argument(
        "--transport",
        choices=("inproc", "tcp", "ipc"),
        default="inproc",
        help="Transport to replay over (default: inproc).",
    )

    p_fbp_summary = sub.add_parser(
        "fbp-summary",
        help="Summarise a durable directive ledger (operator-facing, read-only).",
    )
    p_fbp_summary.add_argument(
        "ledger_path", type=Path, help="The durable ledger file."
    )
    return parser


def _fbp_double(value: int) -> int:
    return value * 2


def _fbp_even(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 0


def _fbp_odd(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 1


def _fbp_cpm(nodes: Any) -> Any:
    """Deterministic critical-path analysis as a registered capability."""
    from agent_centric.fbp.critical_path import cpm_from_dict

    return cpm_from_dict(nodes).to_dict()


def _seed_fbp_callables(fbp: Any) -> None:
    """Register the deterministic demo callables in the module-level registry.

    These are needed both to drive the live demo and to re-resolve directives
    when a durable ledger is replayed in a fresh process. The names match what
    the demo's directives reference (double/even/odd/cpm), so a replayed ledger
    re-resolves them deterministically.
    """
    fbp.register_callable("double", _fbp_double, source_url="file:///tasks/double")
    fbp.register_callable("even", _fbp_even)
    fbp.register_callable("odd", _fbp_odd)
    fbp.register_callable("cpm", _fbp_cpm)


def _fbp_endpoint(transport: str) -> str:
    """A transport-appropriate root endpoint for the FBP driver/CLI."""
    return {
        "inproc": "root",
        "tcp": "127.0.0.1:5599",
        "ipc": "/tmp/agent-centric-fbp-root",
    }[transport]


def _cmd_fbp(transport: str, ledger: Path | None = None) -> int:
    """Drive the FBP subsystem demo over the directive/response protocol.

    Uses the high-level ``FbpDriver`` (the easy-UX layer) to prove the core
    properties on a real tree: registry-as-agent, configure, local run,
    mediated spawn + delegation, the correctness spine (parent re-verifies a
    child's value on the way up), and fail-closed delegation. All offline; the
    transport exercises ``inproc``/``tcp``/``ipc``. When ``ledger`` is given, the
    session is recorded to a durable directive ledger (recoverable replay).
    """
    import agent_centric.fbp as fbp

    _seed_fbp_callables(fbp)

    # A transport-appropriate root endpoint: "inproc" uses a bare name;
    # "tcp" needs host:port; "ipc" needs a path. Child endpoints are
    # resolved by the driver against the same transport.
    endpoint = _fbp_endpoint(transport)
    import tempfile

    from agent_centric.fbp import open_state, open_trajectory

    _workdir = tempfile.mkdtemp(prefix="agent-centric-fbp-")
    driver_kwargs: dict[str, Any] = {}
    if ledger is not None:
        driver_kwargs["ledger_path"] = str(ledger)
    with fbp.FbpDriver(transport=transport, endpoint=endpoint, **driver_kwargs) as driver:
        driver.register("double", _fbp_double, source_url="file:///tasks/double")
        driver.register("even", _fbp_even)
        driver.register("odd", _fbp_odd)
        driver.configure(
            tasks=("double",),
            verifiers=("even", "odd"),
            state=f"{_workdir}/state.db",
            trajectory=f"{_workdir}/audit.db",
        )

        local = driver.run("double", {"value": 21})
        print(f"local  : {local.kind} verified={local.verified} value={local.value!r}")

        # Durable state: write, replay, and read back idempotently.
        driver.state_set("bill-b3", {"status": "paid", "amount_cents": 12345})
        driver.state_set("bill-b3", {"status": "paid", "amount_cents": 12345})  # replay
        got = driver.state_get("bill-b3")
        print(f"state  : bill-b3 -> {got.value!r} verified={got.verified}")

        driver.spawn("child")
        driver.configure_child("child", tasks=("double",))
        delegated = driver.run("double", {"value": 21}, child="child")
        print(
            f"delegate: {delegated.kind} verified={delegated.verified} "
            f"value={delegated.value!r} node={delegated.node!r}"
        )

        # A deterministic plan (sequence of run steps) fails closed on the first
        # unverified step; here all steps verify.
        plan = driver.run_plan(
            [
                {"task": "double", "args": {"value": 21}},
                {"task": "double", "args": {"value": 5}, "child": "child"},
            ]
        )
        print(
            f"plan    : ok={plan['ok']} completed={plan['completed']} "
            f"values={[r['value'] for r in plan['results']]}"
        )

        # Correctness spine on the upward path: root's verifier demotes the
        # child's even result to an explicit failure.
        driver.configure(verifier="odd")
        demoted = driver.run("double", {"value": 21}, child="child")
        print(
            f"demote  : {demoted.kind} verified={demoted.verified} "
            f"error={demoted.error!r}"
        )

        # Fail closed: unknown delegation target.
        driver.configure(verifier="even")
        unknown = driver.run("double", {"value": 21}, child="ghost")
        print(
            f"unknown : {unknown.kind} verified={unknown.verified} error={unknown.error!r}"
        )

        # Local audit = start of chain audit.
        audit = driver.audit()
        relays = [r for r in audit.value if r["kind"] == "relay"]
        print(f"audit  : {len(audit.value)} events, {len(relays)} relay hop(s) recorded")

        # Durability: reopen the stores after the driver is gone.
        st = open_state(f"{_workdir}/state.db")
        _durable = st.get("bill-b3")
        st.close()
        tr = open_trajectory(f"{_workdir}/audit.db")
        _rows = tr.count()
        tr.close()
        print(f"durable: state bill-b3={_durable!r} audit_rows={_rows}")

        # Store/registry agent: a single-writer resource reached via delegation.
        # Clear the root verifier (a recorded directive) so the store's
        # (non-numeric) values are not re-verified-and-demoted by the parent on
        # relay — and so replay reproduces it faithfully.
        driver.configure(clear_verifier=True)
        driver.spawn("store", kind="store")
        driver.configure_child(
            "store",
            state=f"{_workdir}/store.db",
            store_keys=("bill-c1", "bill-c2"),
        )
        driver.run(
            "store_set", {"key": "bill-c1", "value": {"due": "2026-10-01"}},
            child="store",
        )
        stored = driver.run("store_get", {"key": "bill-c1"}, child="store")
        denied = driver.run(
            "store_set", {"key": "bill-zz", "value": 1}, child="store"
        )
        print(
            f"store   : store_get bill-c1={stored.value!r} "
            f"ungranted_key_denied={denied.verified is False}"
        )

        # CPM: a read-only, deterministic capability (a registered callable,
        # not an agent — it is a pure observation, not a unit of work).
        driver.register("cpm", _fbp_cpm)
        driver.configure(tasks=("cpm",))
        cpm = driver.run(
            "cpm",
            {
                "nodes": [
                    {"id": "a", "duration": 3},
                    {"id": "b", "duration": 2, "depends_on": ["a"]},
                    {"id": "c", "duration": 1, "depends_on": ["a"]},
                    {"id": "d", "duration": 2, "depends_on": ["b", "c"]},
                ]
            },
        )
        print(
            f"cpm     : duration={cpm.value.get('duration') if cpm.verified else None} "
            f"critical_path={cpm.value.get('critical_path') if cpm.verified else None}"
        )

        # Bills loop: intake -> human-gated accept -> durable registry -> calendar.
        driver.spawn("bills", kind="bills")
        driver.run(
            "bills_setup",
            {"state": f"{_workdir}/bills.db", "store_keys": ["bill-d1", "inbox/txt-bill.txt"]},
            child="bills",
        )
        draft = driver.run(
            "bills_intake",
            {
                "draft": {
                    "id": "bill-d1",
                    "vendor": "GasCo",
                    "amount_cents": 12345,
                    "due_date": "2026-10-01",
                }
            },
            child="bills",
        )
        accepted = driver.run("bills_accept", {"draft": draft.value}, child="bills")
        agenda = driver.run(
            "bills_calendar",
            {"from_date": "2026-10-01", "to_date": "2026-10-31"},
            child="bills",
        )
        print(
            f"bills   : accepted={accepted.verified} "
            f"calendar_total_cents={agenda.value.get('total_cents') if agenda.verified else None}"
        )

        # Agent-level intake from a structured source (unverified -> human accept).
        f_draft = driver.run(
            "bills_intake_file",
            {
                "source_path": "inbox/txt-bill.txt",
                "content": "vendor: PostCo\namount_cents: 3000\ndue_date: 2026-09-15\n",
            },
            child="bills",
        )
        f_accepted = driver.run("bills_accept", {"draft": f_draft.value}, child="bills")
        print(f"intake  : file draft verified={f_draft.verified} "
              f"accepted={f_accepted.verified}")

        # Registry maintenance: mark the first bill paid (drops out of the open
        # calendar), then re-project the agenda.
        paid = driver.run("bills_mark_paid", {"id": "bill-d1"}, child="bills")
        agenda2 = driver.run(
            "bills_calendar",
            {"from_date": "2026-10-01", "to_date": "2026-10-31"},
            child="bills",
        )
        print(
            f"maintain: mark_paid={paid.verified} "
            f"status={paid.value.get('status') if paid.verified else None}"
        )
        open_ids = (
            [e["id"] for e in agenda2.value.get("entries", [])]
            if agenda2.verified
            else []
        )
        print(f"maintain: open_after_paid={open_ids}")

        # Allowlisted workspace capability: a fail-closed file-resource guard.
        from agent_centric.fbp import WorkspaceError, WorkspaceFS, WorkspaceLayout

        ws = WorkspaceFS(
            f"{_workdir}/ws",
            WorkspaceLayout(
                files=("bills/registry.json",), directories=("bills",),
                prefixes=(),
            ),
        )
        ws.create_dir("bills")
        ws.write_text("bills/registry.json", '{"bills": []}')
        _denied = False
        try:
            ws.write_text("../secret.txt", "x")
        except WorkspaceError:
            _denied = True
        print(
            f"workspace: read={ws.read_text('bills/registry.json').content!r} "
            f"traversal_denied={_denied}"
        )

        # Model agent: an LLM as an ordinary first-class agent (deterministic
        # stub by default, source references attached, parent re-verifies).
        driver.spawn("model", kind="model")
        model_resp = driver.run(
            "model", {"prompt": "hello"}, child="model"
        )
        print(
            f"model   : verified={model_resp.verified} "
            f"source={model_resp.sources[0]['id'] if model_resp.sources else None}"
        )

        # Determinism: an ambiguous draft scores low (human judgment); an
        # approved rule makes a matching draft deterministic (auto-resolve).
        from agent_centric.fbp import Rule, RuleSet, resolve_with_rules, score_determinism

        ambiguous = {"vendor": "GasCo"}
        score = score_determinism(ambiguous)
        rules = RuleSet(
            [Rule(id="r-gasco", domain="vendor", method="from_vendor",
                  matcher={"vendor": "GasCo"})]
        )
        _resolved, rule = resolve_with_rules(
            {"vendor": "GasCo", "amount_cents": 12345, "due_date": "2026-10-01"}, rules
        )
        print(
            f"determinism: ambiguous_score={score.score:.2f} "
            f"rule_matched={rule.id if rule else None}"
        )

        # Audit as proof + deterministic replay of a local run.
        chains = driver.reconstruct_audit()
        relay_count = sum(
            1 for c in chains for e in c.get("events", []) if e["kind"] == "relay"
        )
        print(f"audit   : {len(chains)} chains reconstructed, {relay_count} relay hop(s)")
        # Replay the first local (non-delegated) run directive for a faithful check.
        local_run = next(
            (cid for cid, d in driver.ledger().items()
             if d["kind"] == "run" and "child" not in d["payload"]),
            None,
        )
        replay = driver.replay(target=local_run) if local_run else {"passed": False}
        print(f"replay  : passed={replay['passed']}")

        return 0 if local.verified and delegated.verified and replay["passed"] else 1


def _cmd_fbp_replay(ledger_path: Path, transport: str) -> int:
    """Re-open a durable directive ledger and re-verify (replay) it.

    This is the crash-safe recovery path: a session recorded to a durable
    ledger (via ``fbp --ledger <path>``) is re-issued on a fresh, state-isolated
    tree and every run outcome compared to the recorded one.
    """
    import agent_centric.fbp as fbp

    # ``replay_ledger`` re-seeds the recorded callables from the ledger's
    # registry manifest (importing module.qualname). The deterministic demo set
    # is still re-registered here when available, keeping manual seeding the
    # documented fallback for non-importable callables.
    _seed_fbp_callables(fbp)
    endpoint = _fbp_endpoint(transport)
    try:
        result = fbp.replay_ledger(
            str(ledger_path), transport=transport, endpoint=endpoint
        )
    except FileNotFoundError:
        print(f"ledger  : no ledger file at {ledger_path}")
        return 1
    print(
        f"ledger  : total={result['total']} runs={result['runs']} "
        f"passed={result['passed']} failed={len(result['failed'])}"
    )
    for f in result["failed"]:
        print(f"  FAIL run_id={f.get('run_id')} diff={f.get('diff')}")
    return 0 if result["ok"] else 1


def _cmd_fbp_summary(ledger_path: Path) -> int:
    """Summarise a durable directive ledger (operator-facing, read-only)."""
    import agent_centric.fbp as fbp

    try:
        s = fbp.summarise_ledger(str(ledger_path))
    except FileNotFoundError:
        print(f"ledger  : no ledger file at {ledger_path}")
        return 1
    kinds = " ".join(f"{k}={v}" for k, v in s["kinds"].items())
    print(f"ledger  : run_count={s['run_count']} verified={s['verified_runs']} "
          f"errors={s['error_runs']} ok={s['ok']} kinds=[{kinds}]")
    for r in s["runs"]:
        status = "ok" if r["verified"] else "ERR"
        target = f"->{r['child']}" if r["child"] else ""
        print(f"  {r['correlation_id']:<16} {status:<3} {r['task']}{target}")
    return 0 if s["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(args.store)
    if args.command == "summarise":
        return _cmd_summarise(args.store, args.trajectory_id)
    if args.command == "replay-verify":
        return _cmd_replay_verify(args.store, args.trajectory_id)
    if args.command == "fbp":
        return _cmd_fbp(args.transport, ledger=args.ledger)
    if args.command == "fbp-replay":
        return _cmd_fbp_replay(args.ledger_path, args.transport)
    if args.command == "fbp-summary":
        return _cmd_fbp_summary(args.ledger_path)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())