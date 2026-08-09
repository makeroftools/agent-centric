"""The deterministic Agent Manager (control plane).

The Manager is the sole authority that governs agent execution. It:

1. Registers agent components from their manifests.
2. Accepts a task under a strict resource envelope.
3. Instantiates and isolates the agent via its entry point.
4. Drives the agent step by step, recording a complete, durable trajectory.
5. Enforces resource bounds and timeouts hard.
6. Mediates all tool access: an agent may only use the tools explicitly
   granted for its task, and the Manager is the sole executor.
7. Applies the mandatory verification gate.
8. Returns either a verified result or an explicit, audited failure.

The Manager is deterministic: for a given manifest, task, and deterministic
agent, it reproduces the same trajectory content and outcome.

Every step and the terminal outcome are persisted through a durable, append-only
:class:`TrajectoryStore`. A verified result is returned only after its outcome
is durably recorded. If the store fails mid-task, the Manager fails closed with
an explicit ``INTERNAL`` failure rather than returning an unrecorded result.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

from ..agents.interface import (
    AgentResult,
    AgentStep,
    ToolContext,
    ToolRequest,
    ToolResult,
)
from ..contracts.handoff import HandoffSchema, validate_handoff
from ..contracts.manifest import AgentComponentManifest
from ..contracts.pipeline import StageSpec
from ..contracts.policy import Policy
from ..contracts.replay import ReplayDiff, ReplayResult, ReplayVersion
from ..contracts.result import Failure, FailureReason, VerifiedResult, VerifiedResultVersion
from ..contracts.summary import TrajectorySummary
from ..contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from ..contracts.tool import ToolDescriptor
from ..contracts.trajectory import StepRecord, StepStatus, Trajectory, TrajectoryVersion
from .execution import (
    AgentExecutionError,
    ExecutionBackend,
    InProcessBackend,
)
from .registry import Registry
from .replay import verify_replay
from .summary import summarise_stored
from .tools import ToolExecutionError, ToolRegistry
from .trajectory_store import (
    InMemoryTrajectoryStore,
    StoredOutcome,
    StoredTrajectory,
    TrajectoryStore,
    TrajectoryStoreError,
)
from .verifier import VerifierRegistry


@dataclass(frozen=True)
class Outcome:
    """The sealed result of a task execution.

    Exactly one of ``result`` or ``failure`` is set. This mirrors the contract
    that a task terminates in a verified result or an explicit failure.

    Attributes:
        result: The verified result, if the task succeeded.
        failure: The explicit failure, if the task failed.
        trajectory_id: The durable record id for this execution, allowing
            later retrieval and replay via the Manager. None if the trajectory
            could not be persisted.
    """

    result: VerifiedResult | None
    failure: Failure | None
    trajectory_id: str | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.failure is None):
            raise ValueError("Outcome must have exactly one of result or failure set.")


@dataclass(frozen=True)
class _ToolDelivery:
    """Bundles the result to deliver to an agent with the steps consumed."""

    tool_result: ToolResult
    consumed: int


@dataclass(frozen=True)
class _StageFailure:
    """A lightweight branch failure produced by a parallel stage worker.

    Used when ``record_outcome=False`` so the Manager decides and records the
    single terminal outcome after joining the parallel group.

    Attributes:
        reason: The machine-readable failure reason.
        message: A human-readable explanation.
    """

    reason: FailureReason
    message: str


@dataclass(frozen=True)
class _CompositionLimit:
    """Parent-envelope limits enforced across a whole pipeline composition.

    Attributes:
        envelope: The parent task envelope (bounds the whole composition).
        start: The monotonic time the composition began.
        base_steps: The number of steps recorded before the composition began
            (0, since steps is reset per run).
    """

    envelope: ResourceEnvelope
    start: float
    base_steps: int = 0


class AgentManager:
    """Deterministic control plane for governed agents."""

    def __init__(
        self,
        verifiers: VerifierRegistry | None = None,
        registry: Registry | None = None,
        store: TrajectoryStore | None = None,
        tools: ToolRegistry | None = None,
        backend: ExecutionBackend | None = None,
    ) -> None:
        self._registry = registry or Registry()
        self._verifiers = verifiers or VerifierRegistry()
        self._store: TrajectoryStore = store or InMemoryTrajectoryStore()
        self._tools = tools or ToolRegistry()
        self._backend = backend or InProcessBackend()
        self._run_seq = 0
        # Serializes durable step appends so a parallel composition's trajectory
        # stays coherent and append-only under concurrent stage threads.
        self._step_lock = threading.Lock()

    def register(self, manifest: AgentComponentManifest) -> None:
        """Register an agent component from its manifest.

        Raises:
            ValueError: If the manifest is invalid, the name is already
                registered, or a declared capability conflicts with another
                registered agent.
        """
        self._registry.register(manifest)

    def load(self, trajectory_id: str) -> StoredTrajectory | None:
        """Load and reconstruct a durable trajectory by id, or None if absent."""
        return self._store.load(trajectory_id)

    def contains(self, trajectory_id: str) -> bool:
        """Return True if a trajectory with the given id is durably stored."""
        return self._store.contains(trajectory_id)

    def summarise(self, trajectory_id: str) -> TrajectorySummary | None:
        """Load a durable trajectory by id and return its deterministic summary.

        This is a read-only, on-demand inspection API. It never mutates the
        trajectory store or any control-plane state; summaries are computed
        fresh from the stored audit record. Returns ``None`` if no trajectory
        with the given id is stored.
        """
        stored = self._store.load(trajectory_id)
        if stored is None:
            return None
        return summarise_stored(stored)

    def replay(self, task: TaskSpecification, trajectory_id: str) -> ReplayResult:
        """Re-execute a task and verify the new trajectory is equivalent.

        Re-runs ``task`` under the same deterministic configuration (registered
        agents, tools, and provider) and compares the freshly produced trajectory
        to the stored one identified by ``trajectory_id`` under the documented
        equivalence definition (see :mod:`meta_harness.control_plane.replay`).

        This is read-only with respect to the *original* trajectory: it never
        mutates the stored audit record. The replayed run is recorded under a new
        trajectory id. Returns a fail-closed :class:`ReplayResult`; the original
        trajectory is never modified.

        Raises:
            ValueError: If no trajectory with ``trajectory_id`` is stored.
        """
        original = self._store.load(trajectory_id)
        if original is None:
            raise ValueError(f"No stored trajectory with id {trajectory_id!r}.")
        outcome = self.run(task)
        if outcome.trajectory_id is None:
            # The replayed run could not be durably recorded; fail closed.
            return ReplayResult(
                version=ReplayVersion.V1,
                passed=False,
                original_trajectory_id=trajectory_id,
                replayed_trajectory_id=None,
                diffs=(
                    ReplayDiff(
                        field="replay",
                        original=None,
                        replayed="replayed run was not durably recorded",
                    ),
                ),
                message="The replayed run could not be durably recorded.",
            )
        replayed = self._store.load(outcome.trajectory_id)
        assert replayed is not None
        return verify_replay(original, replayed)

    def _resolve(self, task: TaskSpecification) -> AgentComponentManifest | None:
        """Resolve the agent manifest for a task by name or capability."""
        if task.agent_name is not None:
            return self._registry.get_by_name(task.agent_name)
        assert task.capability is not None
        return self._registry.get_by_capability(task.capability)

    def _build_tool_context(self, task: TaskSpecification) -> ToolContext:
        """Build the ToolContext of tools explicitly granted to the agent.

        Granted names come from ``task.granted_tools``. A granted name that does
        not correspond to a registered tool is simply omitted, keeping the grant
        set deterministic and fail-closed: an agent can only ever request tools
        that were explicitly granted.
        """
        descriptors: list[ToolDescriptor] = []
        for name in task.granted_tools:
            descriptor = self._tools.descriptor(name)
            if descriptor is not None:
                descriptors.append(descriptor)
        return ToolContext(tools=tuple(descriptors))

    def _evaluate_task_policy(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        policy: Policy,
    ) -> Outcome | None:
        """Evaluate a task policy before any agent is instantiated or stage begins.

        Checks the task's agent/capability selector and granted tools, plus (for
        a pipeline) every stage's agent/capability and granted tools. On the
        first violation the composition aborts with an explicit, audited
        ``POLICY_VIOLATION`` failure and no restricted work runs. On success a
        durable ``policy accepted`` step is recorded.

        Returns ``None`` on acceptance, or the sealed failure outcome on
        violation.
        """
        checks: list[tuple[str, str]] = []  # (kind, label)

        if task.agent_name is not None:
            decision = policy.check_agent(task.agent_name)
            if not decision.allowed:
                return self._policy_violation(
                    task, agent_name, trajectory_id, steps, decision.reason or "agent denied"
                )
            checks.append(("agent", task.agent_name))
        elif task.capability is not None:
            decision = policy.check_capability(task.capability)
            if not decision.allowed:
                return self._policy_violation(
                    task, agent_name, trajectory_id, steps, decision.reason or "capability denied"
                )
            checks.append(("capability", task.capability.name))

        for tool in task.granted_tools:
            decision = policy.check_tool(tool)
            if not decision.allowed:
                return self._policy_violation(
                    task, agent_name, trajectory_id, steps, decision.reason or "tool denied"
                )
            checks.append(("tool", tool))

        if task.pipeline is not None:
            for stage in task.pipeline.stages:
                if stage.agent_name is not None:
                    decision = policy.check_agent(stage.agent_name)
                    if not decision.allowed:
                        return self._policy_violation(
                            task, agent_name, trajectory_id, steps,
                            decision.reason or "stage agent denied",
                        )
                    checks.append(("stage agent", stage.agent_name))
                elif stage.capability is not None:
                    decision = policy.check_capability(stage.capability)
                    if not decision.allowed:
                        return self._policy_violation(
                            task, agent_name, trajectory_id, steps,
                            decision.reason or "stage capability denied",
                        )
                    checks.append(("stage capability", stage.capability.name))
                for tool in stage.granted_tools:
                    decision = policy.check_tool(tool)
                    if not decision.allowed:
                        return self._policy_violation(
                            task, agent_name, trajectory_id, steps,
                            decision.reason or "stage tool denied",
                        )
                    checks.append(("stage tool", tool))

        if task.parallel is not None:
            for stage in task.parallel.stages:
                if stage.agent_name is not None:
                    decision = policy.check_agent(stage.agent_name)
                    if not decision.allowed:
                        return self._policy_violation(
                            task, agent_name, trajectory_id, steps,
                            decision.reason or "stage agent denied",
                        )
                    checks.append(("parallel stage agent", stage.agent_name))
                elif stage.capability is not None:
                    decision = policy.check_capability(stage.capability)
                    if not decision.allowed:
                        return self._policy_violation(
                            task, agent_name, trajectory_id, steps,
                            decision.reason or "stage capability denied",
                        )
                    checks.append(("parallel stage capability", stage.capability.name))
                for tool in stage.granted_tools:
                    decision = policy.check_tool(tool)
                    if not decision.allowed:
                        return self._policy_violation(
                            task, agent_name, trajectory_id, steps,
                            decision.reason or "stage tool denied",
                        )
                    checks.append(("parallel stage tool", tool))

        record = StepRecord(
            step_index=len(steps),
            status=StepStatus.COMPLETED,
            description="policy accepted",
            input={"constraints": checks},
            output=None,
            elapsed_seconds=0.0,
        )
        steps.append(record)
        if self._store_append(trajectory_id, record) is not None:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                "Failed to persist policy acceptance step.",
            )
        return None

    def _policy_violation(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        message: str,
    ) -> Outcome:
        """Record an explicit, audited policy-violation failure."""
        record = StepRecord(
            step_index=len(steps),
            status=StepStatus.REJECTED,
            description="policy rejected",
            input=None,
            output=None,
            error=message,
            elapsed_seconds=0.0,
        )
        steps.append(record)
        if self._store_append(trajectory_id, record) is not None:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                "Failed to persist policy rejection step.",
            )
        return self._record_failure(
            task, agent_name, trajectory_id, steps, FailureReason.POLICY_VIOLATION, message
        )

    def run(self, task: TaskSpecification) -> Outcome:
        """Execute a task under its resource envelope and return the sealed outcome.

        Dispatches to a single governed agent or to a Manager-orchestrated
        sequential composition (pipeline), depending on the task.

        This method never raises for agent, verification, tool, or durability
        failures; it converts them into explicit, audited ``Failure`` outcomes.
        """
        trajectory_id = f"{task.task_id}#{self._run_seq}"
        self._run_seq += 1

        if task.pipeline is not None:
            return self._run_pipeline(task, trajectory_id)
        if task.parallel is not None:
            return self._run_parallel(task, trajectory_id)
        return self._run_single(task, trajectory_id)

    def _run_single(self, task: TaskSpecification, trajectory_id: str) -> Outcome:
        """Execute a single governed agent and return the sealed outcome."""
        manifest = self._resolve(task)
        if manifest is None:
            return self._run_unknown_agent(task, trajectory_id)

        agent_name = manifest.name
        start = time.monotonic()
        envelope = task.envelope
        steps: list[StepRecord] = []

        try:
            self._store.begin(trajectory_id, task.task_id, agent_name)
        except TrajectoryStoreError as exc:
            return self._build_internal_failure(
                task, agent_name, f"Could not begin durable trajectory: {exc}"
            )

        # Evaluate the task policy before any agent is instantiated. A violation
        # aborts immediately with an explicit, audited failure and no work runs.
        if task.policy is not None:
            policy_failure = self._evaluate_task_policy(
                task, agent_name, trajectory_id, steps, task.policy
            )
            if policy_failure is not None:
                return policy_failure

        tool_context = self._build_tool_context(task)
        failure, output = self._execute_agent(
            task, agent_name, trajectory_id, envelope, tool_context, steps, start
        )
        if failure is not None:
            assert not isinstance(failure, _StageFailure)  # not a parallel worker
            return failure
        # Persist the verified outcome before certifying the result.
        return self._record_success(
            task, agent_name, trajectory_id, steps, output
        )

    def _run_pipeline(self, task: TaskSpecification, trajectory_id: str) -> Outcome:
        """Execute a Manager-orchestrated sequential composition.

        Each stage runs as a fully governed single-agent execution. The verified
        output of a stage is handed off as the input to the next. Any failure or
        verification failure at a stage aborts the composition. The whole run
        produces one coherent, durable trajectory with explicit stage boundaries.

        Resource accounting: each stage runs under its own effective envelope
        (the stage's ``stage_envelope`` if declared, else the parent task
        envelope), and the parent task envelope additionally bounds the whole
        composition. Consumption is recorded at stage boundaries and in a final
        summary.
        """
        pipeline = task.pipeline
        assert pipeline is not None

        first_agent = self._resolve_stage(pipeline.stages[0])
        if first_agent is None:
            return self._run_unknown_agent(task, trajectory_id)
        agent_name = first_agent.name

        composition_start = time.monotonic()
        composition = _CompositionLimit(envelope=task.envelope, start=composition_start)
        steps: list[StepRecord] = []

        try:
            self._store.begin(trajectory_id, task.task_id, agent_name)
        except TrajectoryStoreError as exc:
            return self._build_internal_failure(
                task, agent_name, f"Could not begin durable trajectory: {exc}"
            )

        # Evaluate the task policy before any stage begins. A violation aborts
        # immediately with an explicit, audited failure and no work runs.
        if task.policy is not None:
            policy_failure = self._evaluate_task_policy(
                task, agent_name, trajectory_id, steps, task.policy
            )
            if policy_failure is not None:
                return policy_failure

        current_payload: Any = task.payload
        stage_accounts: list[dict[str, Any]] = []
        for stage_index, stage in enumerate(pipeline.stages):
            stage_start = time.monotonic()
            effective = stage.stage_envelope or task.envelope
            started_steps = len(steps)

            # Record an explicit stage-boundary marker with the accounting.
            boundary = StepRecord(
                step_index=len(steps),
                status=StepStatus.STARTED,
                description=f"pipeline stage {stage_index} begin",
                input={
                    "stage": stage_index,
                    "agent": self._stage_label(stage),
                    "envelope": self._summarise_envelope(effective),
                },
            )
            steps.append(boundary)
            if self._store_append(trajectory_id, boundary) is not None:
                return self._record_failure(
                    task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                    f"Failed to persist stage-boundary step for stage {stage_index}.",
                )

            manifest = self._resolve_stage(stage)
            if manifest is None:
                return self._record_failure(
                    task, agent_name, trajectory_id, steps, FailureReason.UNKNOWN_AGENT,
                    f"Stage {stage_index} references an unknown agent "
                    f"({self._stage_label(stage)}).",
                )

            stage_name = manifest.name
            stage_tool_context = self._build_tool_context_for_stage(stage)
            stage_task = self._stage_task(task, current_payload)
            stage_failure, stage_output = self._execute_agent(
                stage_task, stage_name, trajectory_id, effective,
                stage_tool_context, steps, stage_start, composition,
            )

            elapsed = time.monotonic() - stage_start
            consumed_steps = len(steps) - started_steps
            stage_accounts.append(
                {
                    "stage": stage_index,
                    "agent": stage_name,
                    "elapsed_seconds": elapsed,
                    "steps": consumed_steps,
                }
            )

            if stage_failure is not None:
                assert not isinstance(stage_failure, _StageFailure)  # not a parallel worker
                # Abort the composition; record the stage's consumption before
                # returning the already-persisted failure.
                if not self._persist_stage_summary(
                    task, agent_name, trajectory_id, steps, stage_accounts
                ):
                    return self._record_failure(
                        task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                        "Failed to persist pipeline resource accounting.",
                    )
                return stage_failure

            # Hand off the verified output as the next stage's input. The
            # payload is validated against the producing stage's output_schema
            # and the consuming stage's input_schema before it is accepted, so
            # no schema-invalid data flows to a subsequent stage.
            if stage_index < len(pipeline.stages) - 1:
                handed_off = stage_output
                if not isinstance(handed_off, dict):
                    handed_off = {"text": handed_off}
                next_stage = pipeline.stages[stage_index + 1]
                handoff_failure = self._validate_handoff(
                    task, agent_name, trajectory_id, steps,
                    stage, next_stage, handed_off, stage_index,
                )
                if handoff_failure is not None:
                    # Abort the composition; record the stage's consumption
                    # before returning the already-persisted failure.
                    if not self._persist_stage_summary(
                        task, agent_name, trajectory_id, steps, stage_accounts
                    ):
                        return self._record_failure(
                            task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                            "Failed to persist pipeline resource accounting.",
                        )
                    return handoff_failure
                current_payload = handed_off
            else:
                current_payload = stage_output

        # All stages completed and verified: record a cumulative summary and seal
        # the final result.
        if not self._persist_stage_summary(
            task, agent_name, trajectory_id, steps, stage_accounts
        ):
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                "Failed to persist pipeline resource accounting.",
            )
        return self._record_success(
            task, agent_name, trajectory_id, steps, current_payload
        )

    def _summarise_envelope(self, envelope: ResourceEnvelope) -> dict[str, Any]:
        """Return a JSON-serialisable summary of an envelope."""
        return {
            "max_steps": envelope.max_steps,
            "timeout_seconds": envelope.timeout_seconds,
            "max_step_seconds": envelope.max_step_seconds,
        }

    def _persist_stage_summary(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        accounts: list[dict[str, Any]],
    ) -> bool:
        """Record a resource-consumption summary step into the trajectory.

        Returns True on success, or False if the summary could not be persisted
        (in which case the caller converts it into an explicit failure).
        """
        summary = StepRecord(
            step_index=len(steps),
            status=StepStatus.COMPLETED,
            description="pipeline resource accounting",
            input=None,
            output={
                "stages": accounts,
                # Includes the summary step itself, so total_steps equals the
                # number of steps in the final trajectory.
                "total_steps": len(steps) + 1,
            },
            elapsed_seconds=0.0,
        )
        steps.append(summary)
        return self._store_append(trajectory_id, summary) is None

    def _run_parallel(self, task: TaskSpecification, trajectory_id: str) -> Outcome:
        """Execute a Manager-orchestrated parallel composition (fan-out / join).

        Independent stages are dispatched to worker threads that share the same
        append-only trajectory. A lock serialises durable step appends so step
        indices stay globally ordered and reconstructible. The parent envelope
        bounds the whole group; per-stage envelopes still apply. On any stage
        failure, remaining running siblings are cooperatively cancelled via a
        shared ``Event`` and the composition fails closed; no partial success is
        returned. Only if every stage succeeds and verifies does the Manager
        produce the deterministic join (an ordered list of stage outputs in
        declared order).
        """
        parallel = task.parallel
        assert parallel is not None

        first_manifest = self._resolve_stage(parallel.stages[0])
        if first_manifest is None:
            return self._run_unknown_agent(task, trajectory_id)
        agent_name = first_manifest.name

        group_start = time.monotonic()
        composition = _CompositionLimit(envelope=task.envelope, start=group_start)
        steps: list[StepRecord] = []
        cancel_event = threading.Event()

        try:
            self._store.begin(trajectory_id, task.task_id, agent_name)
        except TrajectoryStoreError as exc:
            return self._build_internal_failure(
                task, agent_name, f"Could not begin durable trajectory: {exc}"
            )

        # Evaluate the task policy before any stage begins.
        if task.policy is not None:
            policy_failure = self._evaluate_task_policy(
                task, agent_name, trajectory_id, steps, task.policy
            )
            if policy_failure is not None:
                return policy_failure

        # Record the parallel group begin marker.
        group_begin = StepRecord(
            step_index=len(steps),
            status=StepStatus.STARTED,
            description="parallel group begin",
            input={
                "stages": [self._stage_label(s) for s in parallel.stages],
                "envelope": self._summarise_envelope(task.envelope),
            },
            output=None,
            elapsed_seconds=0.0,
        )
        steps.append(group_begin)
        if self._store_append(trajectory_id, group_begin) is not None:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                "Failed to persist parallel group begin step.",
            )

        # Resolve every stage up front so unknown agents abort before any thread
        # runs, and record a per-stage begin marker.
        resolved: list[tuple[StageSpec, str]] = []
        stage_starts: list[float] = []
        stage_accounts: list[dict[str, Any]] = []
        for stage_index, stage in enumerate(parallel.stages):
            manifest = self._resolve_stage(stage)
            if manifest is None:
                return self._record_failure(
                    task, agent_name, trajectory_id, steps, FailureReason.UNKNOWN_AGENT,
                    f"Parallel stage {stage_index} references an unknown agent "
                    f"({self._stage_label(stage)}).",
                )
            resolved.append((stage, manifest.name))
            effective = stage.stage_envelope or task.envelope
            marker = StepRecord(
                step_index=len(steps),
                status=StepStatus.STARTED,
                description=f"parallel stage {stage_index} begin",
                input={
                    "stage": stage_index,
                    "agent": self._stage_label(stage),
                    "envelope": self._summarise_envelope(effective),
                },
                output=None,
                elapsed_seconds=0.0,
            )
            steps.append(marker)
            if self._store_append(trajectory_id, marker) is not None:
                return self._record_failure(
                    task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                    f"Failed to persist parallel stage {stage_index} begin step.",
                )
            stage_starts.append(time.monotonic())
            stage_accounts.append({"agent": manifest.name, "stage": stage_index})

        # Dispatch each stage to a worker thread.
        with ThreadPoolExecutor(max_workers=len(resolved)) as executor:
            futures = {}
            for stage_index, (stage, stage_name) in enumerate(resolved):
                effective = stage.stage_envelope or task.envelope
                tool_context = self._build_tool_context_for_stage(stage)
                stage_task = self._stage_task(task, task.payload)

                def _worker(
                    idx: int = stage_index,
                    agent: str = stage_name,
                    st: TaskSpecification = stage_task,
                    eff: ResourceEnvelope = effective,
                    tools: ToolContext = tool_context,
                ) -> tuple[int, Any, Any]:
                    failure, output = self._execute_agent(
                        st, agent, trajectory_id, eff, tools, steps,
                        stage_starts[idx], composition,
                        stage_worker=True, cancel_event=cancel_event,
                    )
                    return idx, output, failure

                futures[executor.submit(_worker)] = stage_index

            # Wait for all stages; the first failure cancels siblings via the
            # shared event, and every worker still returns (cooperatively).
            results: dict[int, tuple[int, Any, Any]] = {}
            for future, idx in futures.items():
                if future.exception() is not None:
                    # A worker raised unexpectedly (should not happen, since the
                    # core converts failures to results); fail closed.
                    cancel_event.set()
                    results[idx] = (idx, None, _StageFailure(
                        FailureReason.INTERNAL, f"Parallel worker raised: {future.exception()}"
                    ))
                else:
                    results[idx] = future.result()

        # Determine whether the composition succeeded.
        failure = None
        joined: list[Any] = []
        for stage_index, (_stage, _stage_name) in enumerate(resolved):
            idx, output, outcome = results[stage_index]
            elapsed = time.monotonic() - stage_starts[idx]

            if outcome is None:
                # Success: verified output for this stage.
                stage_accounts[idx]["elapsed_seconds"] = elapsed
                joined.append((stage_index, _stage_name, output))
            else:
                # First failure decides the terminal outcome; later ones are
                # siblings that were cancelled.
                if failure is None:
                    failure = outcome
                    stage_accounts[idx]["elapsed_seconds"] = elapsed

        # Record the parallel group end marker.
        group_end = StepRecord(
            step_index=len(steps),
            status=StepStatus.COMPLETED,
            description="parallel group end",
            input=None,
            output={"stages": stage_accounts},
            elapsed_seconds=time.monotonic() - group_start,
        )
        steps.append(group_end)
        if self._store_append(trajectory_id, group_end) is not None:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                "Failed to persist parallel group end step.",
            )

        if failure is not None:
            # No partial success is returned; record a single terminal failure.
            return self._record_failure(
                task, agent_name, trajectory_id, steps,
                failure.reason, failure.message,
            )

        # Success: build the deterministic join and seal the verified result.
        joined_result = {"stages": joined}
        return self._record_success(
            task, agent_name, trajectory_id, steps, joined_result
        )

    def _stage_label(self, stage: StageSpec) -> str:
        """Human-readable label for a stage selector."""
        if stage.agent_name is not None:
            return stage.agent_name
        assert stage.capability is not None
        return f"capability:{stage.capability.name}"

    def _resolve_stage(self, stage: StageSpec) -> AgentComponentManifest | None:
        """Resolve the agent manifest for a pipeline stage."""
        if stage.agent_name is not None:
            return self._registry.get_by_name(stage.agent_name)
        assert stage.capability is not None
        return self._registry.get_by_capability(stage.capability)

    def _build_tool_context_for_stage(self, stage: StageSpec) -> ToolContext:
        """Build the ToolContext for a pipeline stage from its granted tools."""
        descriptors: list[ToolDescriptor] = []
        for name in stage.granted_tools:
            descriptor = self._tools.descriptor(name)
            if descriptor is not None:
                descriptors.append(descriptor)
        return ToolContext(tools=tuple(descriptors))

    def _stage_task(self, task: TaskSpecification, payload: Any) -> TaskSpecification:
        """Build a single-stage task carrying the current payload.

        The selector is a placeholder; ``_execute_agent`` resolves the agent by
        the explicit ``agent_name`` argument, not via the task selector. The
        payload is what the verifier uses to re-derive the expected output.
        """
        return TaskSpecification(
            version=TaskSpecVersion.V6,
            task_id=task.task_id,
            agent_name="placeholder",
            payload=payload,
            envelope=task.envelope,
        )

    def _validate_handoff(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        producing: StageSpec,
        consuming: StageSpec,
        payload: Any,
        stage_index: int,
    ) -> Outcome | None:
        """Validate a stage's verified output before it is handed off.

        The payload must satisfy the producing stage's ``output_schema`` (if
        declared) and the consuming stage's ``input_schema`` (if declared). A
        stage that declares neither schema is validated under the conservative
        default: the payload must be a mapping (the shape the harness agents
        expect). A validation failure aborts the composition with an explicit,
        audited ``HANDOFF_FAILED`` failure and no data proceeds.

        On success, a durable step records the validated hand-off and its shape.
        Returns ``None`` on success, or the sealed failure outcome on abort.
        """
        checks: list[tuple[str, HandoffSchema]] = []
        if producing.output_schema is not None:
            checks.append((f"stage {stage_index} output_schema", producing.output_schema))
        if consuming.input_schema is not None:
            checks.append((f"stage {stage_index + 1} input_schema", consuming.input_schema))

        if not checks:
            # Conservative default: the handed-off payload must be a mapping.
            checks.append(("default object shape", {"text": "any"}))

        for label, schema in checks:
            passed, message = validate_handoff(payload, schema)
            if not passed:
                return self._record_failure(
                    task, agent_name, trajectory_id, steps, FailureReason.HANDOFF_FAILED,
                    f"Hand-off from stage {stage_index} rejected by {label}: {message}",
                )

        record = StepRecord(
            step_index=len(steps),
            status=StepStatus.COMPLETED,
            description=f"stage {stage_index} hand-off validated",
            input={
                "from_stage": stage_index,
                "to_stage": stage_index + 1,
                "shape": self._summarise_payload(payload),
            },
            output=None,
            elapsed_seconds=0.0,
        )
        steps.append(record)
        if self._store_append(trajectory_id, record) is not None:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                "Failed to persist stage hand-off validation step.",
            )
        return None

    @staticmethod
    def _summarise_payload(payload: Any) -> dict[str, Any]:
        """Return a JSON-serialisable shape summary of a handed-off payload.

        Only the shape (keys and value types) is recorded, never the data
        itself, keeping the trajectory inspectable without duplicating content.
        """
        if isinstance(payload, dict):
            return {
                "kind": "object",
                "fields": {k: type(v).__name__ for k, v in payload.items()},
            }
        return {"kind": type(payload).__name__}

    def _execute_agent(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        envelope: ResourceEnvelope,
        tool_context: ToolContext,
        steps: list[StepRecord],
        stage_start: float,
        composition: _CompositionLimit | None = None,
        stage_worker: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> tuple[Outcome | None | _StageFailure, Any]:
        """Drive a single agent to completion, recording steps and verifying.

        This is the shared execution core used by single-agent tasks, each
        sequential (pipeline) stage, and each parallel stage. It appends to the
        shared ``steps`` list so a composition produces one coherent trajectory.
        Step indices are absolute (across the whole run) so records stay
        reconstructible, while the *enforced* step budget is counted per-stage so
        stage envelopes apply regardless of absolute position.

        ``envelope`` is the effective limit for this particular run: the task
        envelope for a single-agent task, or the stage's effective envelope for a
        pipeline / parallel stage. ``composition`` (optional) enforces the parent
        task envelope across the whole composition.

        ``stage_worker`` is True when running inside a parallel worker thread. In
        that mode a failure is *not* recorded as a terminal outcome (the joiner
        decides and records the single terminal outcome after cancelling
        siblings); instead a lightweight ``_StageFailure`` is returned.

        ``cancel_event`` (parallel only) is set by the joiner when a sibling has
        failed. The worker observes it and cooperatively cancels itself so no
        later stage keeps running after another stage has failed.

        Returns ``(None, verified_output)`` on success, or ``(reason, None)`` on
        failure where ``reason`` is either a fully-recorded ``Outcome`` (default)
        or a ``_StageFailure`` (in ``stage_worker`` mode). The caller is
        responsible for recording the final success outcome exactly once.
        """
        try:
            manifest = self._registry.get_by_name(agent_name)
            if manifest is None:
                return (
                    self._deferred_or_recorded_failure(
                        task, agent_name, trajectory_id, steps,
                        FailureReason.UNKNOWN_AGENT,
                        f"No agent registered with name {agent_name!r}.",
                        stage_worker,
                    ),
                    None,
                )
            session = self._backend.session(
                manifest, task.payload, envelope.max_steps, tool_context
            )
        except Exception as exc:  # noqa: BLE001 - converted to explicit failure
            message = f"Failed to start agent: {exc}"
            return (
                self._deferred_or_recorded_failure(
                    task, agent_name, trajectory_id, steps,
                    FailureReason.INTERNAL, message, stage_worker,
                ),
                None,
            )

        stage_step = 0
        final_output: Any = None
        produced_output = False
        sent: Any = None

        def _fail(reason: FailureReason, message: str) -> tuple[Outcome | _StageFailure, Any]:
            return (
                self._deferred_or_recorded_failure(
                    task, agent_name, trajectory_id, steps, reason, message, stage_worker
                ),
                None,
            )

        def _cancel(reason: FailureReason, message: str) -> tuple[Outcome | _StageFailure, Any]:
            """Cooperatively cancel the agent and fail explicitly.

            Records the cancellation in the trajectory, delivers a ``Cancelled``
            signal to the agent (cooperatively, so a well-behaved agent may
            observe it and exit), then fails regardless of what the agent does.
            A cancellation never produces an unverified success.
            """
            record = StepRecord(
                step_index=len(steps),
                status=StepStatus.CANCELLED,
                description="agent cancelled",
                input=None,
                output=None,
                error=message,
                elapsed_seconds=0.0,
            )
            if self._append_step_locked(trajectory_id, steps, record) is not None:
                return self._deferred_or_recorded_failure(
                    task, agent_name, trajectory_id, steps,
                    FailureReason.INTERNAL,
                    "Failed to persist agent cancellation step.", stage_worker,
                ), None
            # Deliver the cooperative signal without trusting its outcome.
            session.cancel(reason=message)
            session.close()
            return _fail(reason, message)

        try:
            while True:
                # Cooperative cancellation requested by the joiner (a sibling
                # failed): stop this stage too.
                if cancel_event is not None and cancel_event.is_set():
                    return _cancel(
                        FailureReason.CANCELLED,
                        "Parallel composition aborted because a sibling stage failed.",
                    )

                # Overall (composition) timeout, enforced across the whole run.
                if composition is not None:
                    comp_elapsed = time.monotonic() - composition.start
                    if comp_elapsed > composition.envelope.timeout_seconds:
                        return _cancel(
                            FailureReason.TIMEOUT,
                            f"Composition exceeded overall timeout of "
                            f"{composition.envelope.timeout_seconds}s.",
                        )

                # Stage / single-agent timeout.
                elapsed = time.monotonic() - stage_start
                if elapsed > envelope.timeout_seconds:
                    return _cancel(
                        FailureReason.TIMEOUT,
                        f"Agent exceeded overall timeout of {envelope.timeout_seconds}s.",
                    )

                # Overall (composition) step budget, counted across the whole
                # run (absolute steps recorded so far).
                if composition is not None:
                    comp_steps = len(steps) - composition.base_steps
                    if comp_steps >= composition.envelope.max_steps:
                        return _cancel(
                            FailureReason.STEP_LIMIT,
                            f"Composition exceeded step limit of "
                            f"{composition.envelope.max_steps}.",
                        )

                # Per-stage step budget.
                if stage_step >= envelope.max_steps:
                    return _cancel(
                        FailureReason.STEP_LIMIT,
                        f"Agent exceeded step limit of {envelope.max_steps}.",
                    )

                step_start = time.monotonic()
                try:
                    item = session.next_step(sent)
                except AgentExecutionError as exc:
                    return _fail(
                        FailureReason.AGENT_ERROR,
                        f"Agent execution failed: {exc}",
                    )

                if isinstance(item, AgentResult):
                    # The agent returned its final result.
                    final_output = item.output
                    produced_output = True
                    break

                step_elapsed = time.monotonic() - step_start

                # Enforce the per-step budget.
                if (
                    envelope.max_step_seconds is not None
                    and step_elapsed > envelope.max_step_seconds
                ):
                    return _cancel(
                        FailureReason.TIMEOUT,
                        f"Step {stage_step} exceeded per-step budget of "
                        f"{envelope.max_step_seconds}s.",
                    )

                if isinstance(item, AgentStep):
                    step_index = len(steps)
                    outcome = self._persist_step(
                        task, agent_name, trajectory_id, steps,
                        step_index, item, step_elapsed,
                    )
                    if outcome is not None:
                        if not stage_worker:
                            return outcome, None
                        # Defer a persistence failure to the joiner.
                        failure = outcome.failure
                        message = failure.message if failure else "Failed to persist step."
                        return self._deferred_or_recorded_failure(
                            task, agent_name, trajectory_id, steps,
                            FailureReason.INTERNAL, message, stage_worker,
                        ), None
                    stage_step += 1
                    sent = None
                elif isinstance(item, ToolRequest):
                    delivery = self._mediate_tool(
                        task, agent_name, trajectory_id, tool_context, item, steps
                    )
                    if delivery is None:
                        return _fail(
                            FailureReason.INTERNAL,
                            "Tool mediation could not be durably recorded.",
                        )
                    stage_step += delivery.consumed
                    sent = delivery.tool_result
                else:
                    return _fail(
                        FailureReason.AGENT_ERROR,
                        f"Agent yielded an unsupported value at step {stage_step}.",
                    )
        except Exception as exc:  # noqa: BLE001 - converted to explicit failure
            return _fail(
                FailureReason.AGENT_ERROR,
                f"Agent raised an exception: {exc}",
            )

        if not produced_output:
            return _fail(
                FailureReason.AGENT_ERROR,
                "Agent terminated without producing a result.",
            )

        # Mandatory verification gate: no result is accepted without passing.
        verification = self._verifiers.verify(agent_name, task, final_output)
        if not verification.passed:
            return _fail(
                FailureReason.VERIFICATION_FAILED, verification.message,
            )

        # Success: return the verified output; the caller records the outcome.
        return None, final_output

    def _deferred_or_recorded_failure(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        reason: FailureReason,
        message: str,
        stage_worker: bool,
    ) -> Outcome | _StageFailure:
        """Return a recorded ``Outcome`` or a deferred ``_StageFailure``.

        When ``stage_worker`` is False (single-agent / sequential stage) the
        failure outcome is durably recorded here, matching prior behaviour. When
        True (parallel worker) the terminal outcome is deferred so the parallel
        joiner records exactly one terminal outcome after cancelling siblings.
        """
        if not stage_worker:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, reason, message
            )
        return _StageFailure(reason=reason, message=message)

    # -- step and tool mediation helpers ----------------------------------------

    def _persist_step(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        step_index: int,
        agent_step: AgentStep,
        elapsed: float,
    ) -> Outcome | None:
        """Record an AgentStep into memory and the durable store.

        Returns an Outcome only on persistence failure, else None.
        """
        record = StepRecord(
            step_index=step_index,
            status=StepStatus.COMPLETED,
            description=agent_step.description,
            input=None,
            output=agent_step.detail,
            elapsed_seconds=elapsed,
        )
        if self._append_step_locked(trajectory_id, steps, record) is not None:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                f"Failed to persist step {step_index}.",
            )
        return None

    def _mediate_tool(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        tool_context: ToolContext,
        request: ToolRequest,
        steps: list[StepRecord],
    ) -> _ToolDelivery | None:
        """Mediate a tool request: validate the grant, execute, and record.

        The Manager is the sole executor. Both the request and the outcome are
        recorded as first-class, ordered steps in the durable trajectory. A tool
        failure is delivered back to the agent (it never yields an unverified
        success) and does not bypass the final verification gate.

        Returns a ``_ToolDelivery`` on success; ``None`` if a persistence failure
        occurred (the caller aborts the task).
        """
        step_index = len(steps)

        # Validate the grant: only explicitly granted tools are usable.
        if not tool_context.available(request.name):
            rejected = StepRecord(
                step_index=step_index,
                status=StepStatus.REJECTED,
                description=f"tool {request.name!r} request rejected",
                input={"tool": request.name, "args": request.args},
                output=None,
                error="tool not granted to this agent",
            )
            if self._append_step_locked(trajectory_id, steps, rejected) is not None:
                return None
            return _ToolDelivery(
                tool_result=ToolResult(success=False, error="tool not granted to this agent"),
                consumed=1,
            )

        # Record the request.
        request_record = StepRecord(
            step_index=step_index,
            status=StepStatus.COMPLETED,
            description=f"tool {request.name!r} request",
            input={"tool": request.name, "args": request.args},
            output=None,
            elapsed_seconds=0.0,
        )
        if self._append_step_locked(trajectory_id, steps, request_record) is not None:
            return None

        # Execute via the Manager-controlled ToolRegistry (or record a failure).
        try:
            output = self._tools.execute(request.name, request.args)
        except ToolExecutionError as exc:
            failure_record = StepRecord(
                step_index=step_index + 1,
                status=StepStatus.FAILED,
                description=f"tool {request.name!r} execution failed",
                input=None,
                output=None,
                error=str(exc),
                elapsed_seconds=0.0,
            )
            if self._append_step_locked(trajectory_id, steps, failure_record) is not None:
                return None
            return _ToolDelivery(
                tool_result=ToolResult(success=False, error=str(exc)),
                consumed=2,
            )

        result_record = StepRecord(
            step_index=step_index + 1,
            status=StepStatus.COMPLETED,
            description=f"tool {request.name!r} result",
            input=None,
            output=output,
            elapsed_seconds=0.0,
        )
        if self._append_step_locked(trajectory_id, steps, result_record) is not None:
            return None
        return _ToolDelivery(
            tool_result=ToolResult(success=True, output=output),
            consumed=2,
        )

    def _store_append(self, trajectory_id: str, record: StepRecord) -> TrajectoryStoreError | None:
        """Append a step to the durable store, returning the error or None."""
        try:
            self._store.append_step(trajectory_id, record)
            return None
        except TrajectoryStoreError as exc:
            return exc

    def _append_step_locked(
        self, trajectory_id: str, steps: list[StepRecord], record: StepRecord
    ) -> TrajectoryStoreError | None:
        """Append a step to memory and the durable store atomically under a lock.

        Assigns the step index under the lock from the current list length so a
        parallel composition's trajectory stays globally ordered and
        reconstructible, even when multiple stage threads append concurrently.

        Returns the store error on failure (the record is still held in memory
        by the caller's local list), or None on success.
        """
        with self._step_lock:
            record = replace(record, step_index=len(steps))
            steps.append(record)
            return self._store_append(trajectory_id, record)

    # -- durable outcome helpers ------------------------------------------------

    def _run_unknown_agent(self, task: TaskSpecification, trajectory_id: str) -> Outcome:
        """Record and return an explicit UNKNOWN_AGENT failure."""
        agent_name = task.agent_name or ""
        message = (
            f"No agent registered for selector "
            f"{(task.agent_name if task.agent_name is not None else task.capability)!r}."
        )
        try:
            self._store.begin(trajectory_id, task.task_id, agent_name)
            self._store.record_outcome(
                trajectory_id, StoredOutcome.failure(FailureReason.UNKNOWN_AGENT, message)
            )
        except TrajectoryStoreError as exc:
            return self._build_internal_failure(
                task, agent_name, f"Could not persist unknown-agent failure: {exc}"
            )
        empty = Trajectory(TrajectoryVersion.V1, task.task_id, agent_name)
        return Outcome(
            result=None,
            failure=Failure(
                task_id=task.task_id,
                reason=FailureReason.UNKNOWN_AGENT,
                message=message,
                trajectory=empty,
            ),
            trajectory_id=trajectory_id,
        )

    def _record_success(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        final_output: Any,
    ) -> Outcome:
        """Persist a verified outcome and return the sealed success result."""
        try:
            self._store.record_outcome(trajectory_id, StoredOutcome.verified(final_output))
        except TrajectoryStoreError as exc:
            return self._record_failure(
                task, agent_name, trajectory_id, steps, FailureReason.INTERNAL,
                f"Failed to persist verified outcome: {exc}",
            )
        trajectory = Trajectory(TrajectoryVersion.V1, task.task_id, agent_name, tuple(steps))
        return Outcome(
            result=VerifiedResult(
                version=VerifiedResultVersion.V1,
                task_id=task.task_id,
                output=final_output,
                trajectory=trajectory,
            ),
            failure=None,
            trajectory_id=trajectory_id,
        )

    def _record_failure(
        self,
        task: TaskSpecification,
        agent_name: str,
        trajectory_id: str,
        steps: list[StepRecord],
        reason: FailureReason,
        message: str,
    ) -> Outcome:
        """Persist a failure outcome and return the sealed, audited failure."""
        try:
            self._store.record_outcome(trajectory_id, StoredOutcome.failure(reason, message))
        except TrajectoryStoreError as exc:
            return self._build_internal_failure(
                task, agent_name,
                f"Failed to persist failure outcome ({reason.value}): {exc}",
            )
        trajectory = Trajectory(TrajectoryVersion.V1, task.task_id, agent_name, tuple(steps))
        return Outcome(
            result=None,
            failure=Failure(
                task_id=task.task_id,
                reason=reason,
                message=message,
                trajectory=trajectory,
            ),
            trajectory_id=trajectory_id,
        )

    @staticmethod
    def _build_internal_failure(
        task: TaskSpecification, agent_name: str, message: str
    ) -> Outcome:
        """Fail closed when the store itself cannot persist (trajectory_id=None)."""
        empty = Trajectory(TrajectoryVersion.V1, task.task_id, agent_name)
        return Outcome(
            result=None,
            failure=Failure(
                task_id=task.task_id,
                reason=FailureReason.INTERNAL,
                message=message,
                trajectory=empty,
            ),
            trajectory_id=None,
        )