"""Tests for Volley 023 — Workspace.

These tests prove the workspace specialty agent is governed by the same
invariants as every other agent: a local, allowlisted workspace with mediated
file tools that reject any disallowed path (fail-closed), real verification,
full trajectory recording, deterministic replay, hard resource envelopes, and
policy enforcement. No deletion, rename, or arbitrary filesystem traversal is
possible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.capability import Capability
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.policy import Policy, PolicyVersion
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.contracts.workspace import WorkspaceLayout
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.tools import ToolExecutionError, ToolRegistry
from meta_harness.control_plane.verifier import verify_workspace_output
from meta_harness.control_plane.workspace import Workspace, WorkspaceError, register_workspace_tools

WORKSPACE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="workspace",
    entry_point="meta_harness.agents.workspace:create_workspace_agent",
    description="Performs an allowlisted workspace operation via mediated file tools.",
    declared_capabilities=frozenset({Capability(name="workspace", version="1")}),
)

# A representative allowlist: one file and one directory.
_LAYOUT = WorkspaceLayout(
    files=("invoices/note.txt",),
    directories=("invoices",),
)


def _manager(tmp_path: Path, layout: WorkspaceLayout = _LAYOUT) -> AgentManager:
    workspace = Workspace(tmp_path, layout)
    tools = ToolRegistry()
    register_workspace_tools(tools, workspace)
    m = AgentManager(tools=tools)
    m.register(WORKSPACE_MANIFEST)
    return m


def _ws_task(
    task_id: str,
    payload: dict[str, Any],
    *,
    granted: tuple[str, ...],
    envelope: ResourceEnvelope | None = None,
    policy: Policy | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name="workspace",
        payload=payload,
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
        policy=policy,
    )


class TestWorkspaceLayout:
    def test_layout_validates_paths(self) -> None:
        with pytest.raises(ValueError, match="relative"):
            WorkspaceLayout(files=("/abs/path",))
        with pytest.raises(ValueError, match="traversal"):
            WorkspaceLayout(files=("a/../b",))
        with pytest.raises(ValueError, match="non-empty"):
            WorkspaceLayout(files=("",))

    def test_layout_allows(self) -> None:
        layout = WorkspaceLayout(files=("a.txt",), directories=("d",))
        assert layout.allows_file("a.txt")
        assert not layout.allows_file("b.txt")
        assert layout.allows_directory("d")
        assert not layout.allows_directory("a.txt")


class TestWorkspaceTools:
    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path, _LAYOUT)
        ws.create_workspace_dir("invoices")
        ws.write_workspace_file("invoices/note.txt", "hello")
        entry = ws.read_workspace_file("invoices/note.txt")
        assert entry.content == "hello"
        assert entry.kind.value == "file"

    def test_write_requires_existing_parent(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path, _LAYOUT)
        with pytest.raises(WorkspaceError, match="Parent directory"):
            ws.write_workspace_file("invoices/note.txt", "x")

    def test_read_missing_file_fails_closed(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path, _LAYOUT)
        with pytest.raises(WorkspaceError, match="does not exist"):
            ws.read_workspace_file("invoices/note.txt")

    def test_disallowed_file_read_fails_closed(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path, _LAYOUT)
        with pytest.raises(WorkspaceError, match="allowlist"):
            ws.read_workspace_file("other.txt")

    def test_disallowed_dir_create_fails_closed(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path, _LAYOUT)
        with pytest.raises(WorkspaceError, match="allowlist"):
            ws.create_workspace_dir("other")

    def test_list_workspace(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path, _LAYOUT)
        ws.create_workspace_dir("invoices")
        ws.write_workspace_file("invoices/note.txt", "hi")
        listing = ws.list_workspace()
        assert listing["invoices"] == "directory"
        assert listing["invoices/note.txt"] == "file"

    def test_traversal_escapes_root_fails_closed(self, tmp_path: Path) -> None:
        ws = Workspace(tmp_path, WorkspaceLayout(files=("a.txt",)))
        # A path that resolves outside the root is rejected even if on a layout.
        with pytest.raises(WorkspaceError):
            ws._resolve("../escape")  # type: ignore[attr-defined]


class TestWorkspaceRegistry:
    def test_tools_registered(self, tmp_path: Path) -> None:
        tools = ToolRegistry()
        names = register_workspace_tools(tools, Workspace(tmp_path, _LAYOUT))
        assert "list_workspace" in names
        assert "read_workspace_file" in names
        assert "write_workspace_file" in names
        assert "create_workspace_dir" in names
        for name in names:
            assert tools.descriptor(name) is not None

    def test_disallowed_tool_call_raises_tool_error(self, tmp_path: Path) -> None:
        tools = ToolRegistry()
        register_workspace_tools(tools, Workspace(tmp_path, _LAYOUT))
        with pytest.raises(ToolExecutionError, match="allowlist"):
            tools.execute("read_workspace_file", {"relative_path": "other.txt"})


class TestWorkspaceAgent:
    def test_write_then_read_verifies(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        # Create the dir, then write the file.
        mk = m.run(
            _ws_task(
                "mkdir",
                {"operation": "mkdir", "relative_path": "invoices"},
                granted=("create_workspace_dir",),
            )
        )
        assert mk.result is not None
        wr = m.run(
            _ws_task(
                "write",
                {"operation": "write", "relative_path": "invoices/note.txt", "content": "hello"},
                granted=("write_workspace_file",),
            )
        )
        assert wr.result is not None
        assert wr.result.output["content"] == "hello"
        rd = m.run(
            _ws_task(
                "read",
                {"operation": "read", "relative_path": "invoices/note.txt"},
                granted=("read_workspace_file",),
            )
        )
        assert rd.result is not None
        assert rd.result.output["content"] == "hello"

    def test_list_verifies(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        outcome = m.run(
            _ws_task("list", {"operation": "list"}, granted=("list_workspace",))
        )
        assert outcome.result is not None
        assert isinstance(outcome.result.output, dict)

    def test_ungranted_tool_fails_closed(self, tmp_path: Path) -> None:
        """Without the tool grant the agent returns None, which fails the gate."""
        m = _manager(tmp_path)
        outcome = m.run(
            _ws_task(
                "read-ungranted",
                {"operation": "read", "relative_path": "invoices/note.txt"},
                granted=(),
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_disallowed_path_fails_closed(self, tmp_path: Path) -> None:
        """A path outside the allowlist is rejected and never verifies."""
        m = _manager(tmp_path)
        outcome = m.run(
            _ws_task(
                "read-disallowed",
                {"operation": "read", "relative_path": "other.txt"},
                granted=("read_workspace_file",),
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_bad_payload_fails_closed(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        outcome = m.run(
            _ws_task("bad", {"operation": "nope"}, granted=("list_workspace",))
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.AGENT_ERROR

    def test_verifier_rejects_bad_output(self) -> None:
        task = _ws_task(
            "v",
            {"operation": "write", "relative_path": "invoices/note.txt", "content": "hello"},
            granted=("write_workspace_file",),
        )
        good = {
            "relative_path": "invoices/note.txt",
            "kind": "file",
            "content": "hello",
        }
        assert verify_workspace_output(task, good).passed
        assert verify_workspace_output(
            task,
            {"relative_path": "other.txt", "kind": "file", "content": "hello"},
        ).passed is False
        assert verify_workspace_output(task, None).passed is False


class TestWorkspaceEnvelope:
    def test_step_limit_fails_closed(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        outcome = m.run(
            _ws_task(
                "budget",
                {"operation": "list"},
                granted=("list_workspace",),
                envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT


class TestWorkspacePolicy:
    def test_policy_can_deny_the_workspace_agent(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        policy = Policy(version=PolicyVersion.V1, deny_agents=frozenset({"workspace"}))
        outcome = m.run(
            _ws_task(
                "deny-agent",
                {"operation": "list"},
                granted=("list_workspace",),
                policy=policy,
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestWorkspaceDeterminism:
    def test_deterministic_and_replayable(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        m.run(
            _ws_task(
                "mkdir",
                {"operation": "mkdir", "relative_path": "invoices"},
                granted=("create_workspace_dir",),
            )
        )
        m.run(
            _ws_task(
                "write",
                {"operation": "write", "relative_path": "invoices/note.txt", "content": "hello"},
                granted=("write_workspace_file",),
            )
        )
        first = m.run(
            _ws_task(
                "det",
                {"operation": "read", "relative_path": "invoices/note.txt"},
                granted=("read_workspace_file",),
            )
        )
        second = m.run(
            _ws_task(
                "det",
                {"operation": "read", "relative_path": "invoices/note.txt"},
                granted=("read_workspace_file",),
            )
        )
        assert first.result is not None and second.result is not None
        assert first.result.output == second.result.output

        def sig(outcome) -> list[tuple[int, str, str, Any]]:
            return [
                (s.step_index, s.status.value, s.description, s.output)
                for s in outcome.result.trajectory.steps
            ]

        assert sig(first) == sig(second)


class TestWorkspaceSubprocess:
    def test_runs_under_subprocess_backend(self, tmp_path: Path) -> None:
        from meta_harness.control_plane.execution import SubprocessBackend

        workspace = Workspace(tmp_path, _LAYOUT)
        workspace.create_workspace_dir("invoices")
        workspace.write_workspace_file("invoices/note.txt", "hello")
        tools = ToolRegistry()
        register_workspace_tools(tools, workspace)
        m = AgentManager(tools=tools, backend=SubprocessBackend())
        m.register(WORKSPACE_MANIFEST)
        outcome = m.run(
            _ws_task(
                "sub",
                {"operation": "read", "relative_path": "invoices/note.txt"},
                granted=("read_workspace_file",),
            )
        )
        assert outcome.result is not None
        assert outcome.result.output["content"] == "hello"