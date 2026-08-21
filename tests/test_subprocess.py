"""Tests for subprocess isolation of agent execution (Volley 013).

These tests prove that running an agent in a separate child process preserves
every control-plane invariant: the same verified result and coherent,
reconstructible, ordered trajectory as the in-process backend; mediated tool
access; fail-closed handling of child crashes and protocol errors; envelope
exhaustion and cooperative cancellation across the boundary; and policy
enforcement before any work begins. Isolation is additive — it never relaxes
verification or mediation.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric.agents.interface import AgentResult, ToolContext
from agent_centric.contracts.policy import Policy, PolicyVersion
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.execution import SubprocessBackend, SubprocessSession
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from tests.conftest import CASE_TOOL_MANIFEST, REVERSE_MANIFEST
from tests.fake_agent import (
    COOPERATIVE_CANCEL_MANIFEST,
    CRASH_AFTER_STEP_MANIFEST,
    IGNORING_CANCEL_MANIFEST,
    SILENT_HUNG_MANIFEST,
    UNGUARDED_TOOL_AGENT_MANIFEST,
    UNSUPPORTED_YIELD_MANIFEST,
)


def _manager(store: FileTrajectoryStore | None = None) -> AgentManager:
    return AgentManager(backend=SubprocessBackend(), store=store)


def _reverse_task(task_id: str, text: str = "abcdef") -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=task_id,
        agent_name="reverse",
        payload={"text": text},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    )


def _case_task(task_id: str, text: str, *, granted: tuple[str, ...]) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=task_id,
        agent_name="case_tool",
        payload={"text": text},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
    )


def _unguarded_tool_task(task_id: str, payload: str) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V3,
        task_id=task_id,
        agent_name="unguarded_tool",
        payload=payload,
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=(),
    )


def _trajectory_signature(outcome) -> list[tuple[int, str, str]]:
    """A stable, order-sensitive signature of a trajectory's steps."""
    return [
        (s.step_index, s.status.value, s.description)
        for s in outcome.result.trajectory.steps
    ]


class TestSubprocessSuccess:
    def test_subprocess_run_matches_in_process(self) -> None:
        """A subprocess run yields the same verified result and trajectory.

        The reverse agent is deterministic; the subprocess trajectory must be
        coherent, reconstructible, and ordered exactly like the in-process one.
        """
        sub = _manager()
        sub.register(REVERSE_MANIFEST)
        sub_outcome = sub.run(_reverse_task("sub", "abcdef"))

        assert sub_outcome.result is not None
        assert sub_outcome.result.output == "fedcba"

        # Same verified result as the in-process backend.
        inproc = AgentManager()
        inproc.register(REVERSE_MANIFEST)
        inproc_outcome = inproc.run(_reverse_task("inproc", "abcdef"))
        assert inproc_outcome.result is not None
        assert inproc_outcome.result.output == sub_outcome.result.output

        # Same coherent, ordered trajectory (step indices are absolute and
        # contiguous; descriptions match the in-process run).
        assert _trajectory_signature(sub_outcome) == _trajectory_signature(inproc_outcome)
        indices = [s.step_index for s in sub_outcome.result.trajectory.steps]
        assert indices == list(range(len(indices)))

    def test_subprocess_trajectory_is_reconstructible(self, tmp_path: Path) -> None:
        """A subprocess trajectory persists and reconstructs identically."""
        m = _manager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        outcome = m.run(_reverse_task("reconstruct", "hello"))
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        assert stored.task_id == "reconstruct"
        assert [s.description for s in stored.steps] == [
            s.description for s in outcome.result.trajectory.steps
        ]


class TestSubprocessToolMediation:
    def test_granted_tool_round_trips_across_boundary(self) -> None:
        """A mediated tool result is delivered back into the child agent."""
        m = _manager()
        m.register(CASE_TOOL_MANIFEST)
        outcome = m.run(_case_task("granted", "hello", granted=("to_upper",)))
        assert outcome.result is not None
        assert outcome.result.output == "HELLO"

        # The tool request and result are first-class, ordered steps.
        descriptions = [s.description for s in outcome.result.trajectory.steps]
        assert any("to_upper' request" in d for d in descriptions)
        assert any("to_upper' result" in d for d in descriptions)

    def test_ungranted_tool_rejected_across_boundary(self) -> None:
        """The Manager (not the child) still enforces the grant."""
        m = _manager()
        m.register(UNGUARDED_TOOL_AGENT_MANIFEST)
        outcome = m.run(_unguarded_tool_task("ungranted", "abc"))
        assert outcome.result is not None
        rejected = [
            s for s in outcome.result.trajectory.steps if s.status.value == "rejected"
        ]
        assert rejected and "not granted" in (rejected[0].error or "")

    def test_ungranted_tool_cannot_produce_verified_success(self) -> None:
        """Without the grant the case_tool agent cannot verify its output."""
        m = _manager()
        m.register(CASE_TOOL_MANIFEST)
        outcome = m.run(_case_task("no-grant", "hello", granted=()))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED


class TestSubprocessFailClosed:
    def test_crashing_agent_is_an_audited_failure(self, tmp_path: Path) -> None:
        """A child that crashes mid-run fails closed: no verified success."""
        m = _manager(store=FileTrajectoryStore(tmp_path))
        m.register(CRASH_AFTER_STEP_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="crash",
            agent_name="crash_after_step",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.AGENT_ERROR
        assert "Agent execution failed" in outcome.failure.message

        # The failure is durably recorded.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        assert stored.outcome is not None
        assert stored.outcome.reason == "agent_error"

    def test_protocol_error_is_an_audited_failure(self, tmp_path: Path) -> None:
        """A child that violates the IPC protocol fails closed."""
        m = _manager(store=FileTrajectoryStore(tmp_path))
        m.register(UNSUPPORTED_YIELD_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="protocol",
            agent_name="unsupported_yield",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.AGENT_ERROR

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        assert stored.outcome is not None
        assert stored.outcome.reason == "agent_error"


class TestSubprocessEnvelopeAndCancellation:
    def test_step_limit_cancels_across_boundary(self, tmp_path: Path) -> None:
        """Step-limit exhaustion cooperatively cancels the child agent."""
        m = _manager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="step-limit",
            agent_name="cooperative_cancel",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=2),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        assert any(s.status.value == "cancelled" for s in stored.steps)
        assert any(d == "agent cancelled" for d in [s.description for s in stored.steps])

    def test_timeout_terminates_non_cooperative_child_fail_closed(self) -> None:
        """A child that ignores cancellation is terminated as a last resort."""
        m = _manager()
        m.register(IGNORING_CANCEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="ignore",
            agent_name="ignoring_cancel",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=0.05, max_steps=1000),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.TIMEOUT
        assert any(
            s.status.value == "cancelled" for s in outcome.failure.trajectory.steps
        )


class TestSubprocessLifecycle:
    def test_silent_hung_child_is_bounded_fail_closed(self, tmp_path: Path) -> None:
        """A child that stops responding is bounded by the envelope deadline.

        A silent hang (no further output, no crash) must not block the Manager
        indefinitely: it is mapped to an explicit ``TIMEOUT``, the child is
        force-terminated as a last resort, and the forced kill is recorded
        honestly. No verified success is produced.
        """
        m = _manager(store=FileTrajectoryStore(tmp_path))
        m.register(SILENT_HUNG_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="silent-hung",
            agent_name="silent_hung",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=0.2, max_steps=1000),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.TIMEOUT

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        # Partial work before the hang remains recorded.
        assert any(d == "first step" for d in descriptions)
        # The forced kill is recorded honestly (cooperative cancel was ignored).
        assert any(d == "agent forcibly terminated" for d in descriptions)

    def test_success_reaps_child_no_zombie(self) -> None:
        """A successful subprocess run reaps the child (no zombie).

        After a deterministic agent completes, ``close()`` must reap the child
        process so no zombie survives a normal test path, and the child must not
        have been force-killed.
        """
        session = SubprocessSession(
            REVERSE_MANIFEST,
            {"text": "abcdef"},
            100,
            ToolContext(tools=()),
            timeout_seconds=10.0,
        )
        sent: object = None
        while True:
            item = session.next_step(sent)
            if isinstance(item, AgentResult):
                break
            sent = None
        session.close()
        # The child is reaped (no zombie) and was not force-killed.
        assert session._proc.poll() is not None
        assert session.termination != "forced"

    def test_cooperative_cancel_termination_recorded(self, tmp_path: Path) -> None:
        """A cooperative child exits cleanly; no forced-kill step is recorded."""
        m = _manager(store=FileTrajectoryStore(tmp_path))
        m.register(COOPERATIVE_CANCEL_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V3,
            task_id="coop-cancel",
            agent_name="cooperative_cancel",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=2),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "agent cancelled" for d in descriptions)
        # No forced kill: the child cooperated and exited cleanly.
        assert not any(d == "agent forcibly terminated" for d in descriptions)


class TestSubprocessPolicy:
    def test_policy_applies_before_start(self, tmp_path: Path) -> None:
        """Policy is enforced before any agent work begins, even isolated."""
        m = _manager(store=FileTrajectoryStore(tmp_path))
        m.register(REVERSE_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="policy",
            agent_name="reverse",
            payload={"text": "abc"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=200),
            policy=Policy(
                version=PolicyVersion.V1,
                deny_agents=frozenset({"reverse"}),
            ),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION

        # No agent work occurred: only the policy rejection is recorded.
        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "policy rejected" for d in descriptions)
        assert not any("reversed chunk" in d for d in descriptions)