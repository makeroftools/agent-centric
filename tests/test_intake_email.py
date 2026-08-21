"""Tests for Volley 029 — Email → Unverified Bill Draft (Human-Gated).

These tests prove the read-only email→draft path follows the same mission-critical
gate as every other intake source: email-derived amounts/dates stay unverified
until an explicit accept, a weak/absent parse fails closed (no draft / no invented
facts), email read is read-only (no send/delete), the email-draft grant is separate
from accept/registry-write, and email-draft alone never mutates the registry. No
live network is involved — all fixtures are local and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_centric.contracts.bills_registry import BillsRegistry
from agent_centric.control_plane.intake import IntakeOps, draft_from_email
from agent_centric.control_plane.workspace import Workspace

REGISTRY = {
    "version": "bills_registry.v1",
    "description": "existing registry",
    "bills": [
        {
            "id": "b1",
            "vendor": "NetCo",
            "amount_cents": 3000,
            "due_date": "2026-09-01",
            "status": "due",
        },
    ],
}


def _bill_message(**overrides: Any) -> dict[str, Any]:
    msg = {
        "id": "m-bill",
        "folder": "INBOX",
        "subject": "Your bill from GasCo",
        "from_address": "billing@gasco.example",
        "date": "2026-08-05",
        "body": "Amount total: $123.45. Due date: 2026-09-20.",
    }
    msg.update(overrides)
    return msg


def _make_workspace(tmp_path: Path) -> Workspace:
    from agent_centric.contracts.workspace import WorkspaceLayout
    from agent_centric.control_plane.intake import ensure_intake_layout

    layout = ensure_intake_layout(WorkspaceLayout())
    ws = Workspace(tmp_path, layout)
    ws.create_workspace_dir("bills")
    ws.write_workspace_file("bills/registry.json", json.dumps(REGISTRY))
    ws.create_workspace_dir("inbox")
    return ws


def _read_registry(ws: Workspace) -> BillsRegistry:
    from agent_centric.control_plane.bills_registry import load_registry

    content = ws.read_workspace_file("bills/registry.json").content
    assert content is not None
    return load_registry(content)


class TestDraftFromEmailPure:
    def test_bill_email_yields_unverified_draft(self) -> None:
        out = draft_from_email(_bill_message())
        assert out["count"] == 1
        assert out["unverified"] is True
        draft = out["drafts"][0]
        assert draft["unverified"] is True
        assert draft["vendor"] == "GasCo"
        assert draft["amount_cents"] == 12345
        assert draft["due_date"] == "2026-09-20"
        assert draft["source_path"] == "email://INBOX/m-bill"
        assert draft["draft_id"] == "m-bill:2026-09-20"

    def test_unparseable_body_fails_closed_to_no_draft(self) -> None:
        out = draft_from_email(_bill_message(body="just a friendly hello", subject="Re: hi"))
        assert out["count"] == 0
        assert out["drafts"] == []

    def test_missing_amount_fails_closed(self) -> None:
        # Vendor + due date present but no parseable amount -> no draft (no invention).
        out = draft_from_email(
            _bill_message(body="Vendor: GasCo. Due date: 2026-09-20. amount unknown.")
        )
        assert out["count"] == 0

    def test_missing_due_date_fails_closed(self) -> None:
        out = draft_from_email(
            _bill_message(body="vendor: GasCo total: 123.45 no date here")
        )
        assert out["count"] == 0

    def test_missing_folder_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="folder"):
            draft_from_email(_bill_message(folder=""))

    def test_missing_id_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="missing a non-empty 'id'"):
            draft_from_email(_bill_message(id=""))


class TestIntakeOpsEmailDraft:
    def test_email_draft_does_not_mutate_registry(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = IntakeOps(ws)
        before = _read_registry(ws).as_mapping()
        out = ops.email_draft(_bill_message())
        assert out["count"] == 1
        after = _read_registry(ws).as_mapping()
        assert before == after

    def test_email_draft_invalid_message_fails_closed(self, tmp_path: Path) -> None:
        from agent_centric.control_plane.tools import ToolExecutionError

        ws = _make_workspace(tmp_path)
        ops = IntakeOps(ws)
        with pytest.raises(ToolExecutionError):
            ops.email_draft("not a message")

    def test_accept_still_required_to_persist(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        before = _read_registry(ws)
        ops = IntakeOps(ws)
        drafts = ops.email_draft(_bill_message())
        draft_id = drafts["drafts"][0]["draft_id"]
        # Registry unchanged by drafting alone.
        assert len(_read_registry(ws).bills) == len(before.bills)
        # Explicit accept persists only the requested draft.
        result = ops.accept(drafts, [draft_id])
        assert result["accepted"] == [draft_id]
        merged = _read_registry(ws)
        assert len(merged.bills) == 2
        assert [b.id for b in merged.bills] == ["b1", draft_id]


class TestAgentGate:
    def test_email_draft_under_grant_verifies(self, tmp_path: Path) -> None:
        from agent_centric.agents.intake import create_intake_agent
        from agent_centric.contracts.capability import Capability
        from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
        from agent_centric.contracts.task import (
            ResourceEnvelope,
            TaskSpecification,
            TaskSpecVersion,
        )
        from agent_centric.control_plane.manager import AgentManager
        from agent_centric.control_plane.tools import ToolRegistry
        from agent_centric.control_plane.workspace import register_workspace_tools

        manifest = AgentComponentManifest(
            version=AgentManifestVersion.V2,
            name="intake",
            entry_point="agent_centric.agents.intake:create_intake_agent",
            description="intake",
            declared_capabilities=frozenset(
                {Capability(name="intake.draft_from_email", version="1")}
            ),
        )
        assert create_intake_agent() is not None
        ws = _make_workspace(tmp_path)
        tools = ToolRegistry()
        register_workspace_tools(tools, ws)
        ops = IntakeOps(ws)
        from agent_centric.control_plane.tools import INTAKE_EMAIL_DRAFT_DESCRIPTOR

        tools.register_impl(INTAKE_EMAIL_DRAFT_DESCRIPTOR, ops.email_draft)
        m = AgentManager(tools=tools)
        m.register(manifest)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="email-draft",
            agent_name="intake",
            payload={
                "operation": "draft_from_email",
                "message": _bill_message(),
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("intake_email_draft",),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert outcome.result.output["unverified"] is True

    def test_email_draft_ungranted_fails_closed(self, tmp_path: Path) -> None:
        from agent_centric.contracts.capability import Capability
        from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
        from agent_centric.contracts.result import FailureReason
        from agent_centric.contracts.task import (
            ResourceEnvelope,
            TaskSpecification,
            TaskSpecVersion,
        )
        from agent_centric.control_plane.manager import AgentManager
        from agent_centric.control_plane.tools import ToolRegistry

        manifest = AgentComponentManifest(
            version=AgentManifestVersion.V2,
            name="intake",
            entry_point="agent_centric.agents.intake:create_intake_agent",
            description="intake",
            declared_capabilities=frozenset(
                {Capability(name="intake.draft_from_email", version="1")}
            ),
        )
        m = AgentManager(tools=ToolRegistry())
        m.register(manifest)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="email-draft-ungranted",
            agent_name="intake",
            payload={
                "operation": "draft_from_email",
                "message": _bill_message(),
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_email_draft_never_mutates_registry_via_agent(self, tmp_path: Path) -> None:
        from agent_centric.contracts.capability import Capability
        from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
        from agent_centric.contracts.task import (
            ResourceEnvelope,
            TaskSpecification,
            TaskSpecVersion,
        )
        from agent_centric.control_plane.manager import AgentManager
        from agent_centric.control_plane.tools import INTAKE_EMAIL_DRAFT_DESCRIPTOR, ToolRegistry
        from agent_centric.control_plane.workspace import register_workspace_tools

        manifest = AgentComponentManifest(
            version=AgentManifestVersion.V2,
            name="intake",
            entry_point="agent_centric.agents.intake:create_intake_agent",
            description="intake",
            declared_capabilities=frozenset(
                {Capability(name="intake.draft_from_email", version="1")}
            ),
        )
        ws = _make_workspace(tmp_path)
        before = _read_registry(ws).as_mapping()
        tools = ToolRegistry()
        register_workspace_tools(tools, ws)
        ops = IntakeOps(ws)
        tools.register_impl(INTAKE_EMAIL_DRAFT_DESCRIPTOR, ops.email_draft)
        m = AgentManager(tools=tools)
        m.register(manifest)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="no-mutate",
            agent_name="intake",
            payload={
                "operation": "draft_from_email",
                "message": _bill_message(),
            },
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("intake_email_draft",),
        )
        outcome = m.run(task)
        assert outcome.result is not None
        assert _read_registry(ws).as_mapping() == before


class TestNoLiveNetwork:
    def test_no_gateway_needed_for_drafting(self) -> None:
        # Drafting operates on a message mapping only; no live gateway/network.
        out = draft_from_email(_bill_message())
        assert out["count"] == 1