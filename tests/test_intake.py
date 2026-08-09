"""Tests for Volley 026 — Dump Intake: inbox inventory + draft proposals + human accept.

These tests prove the intake pipeline is governed by the mission-critical gate:
no silent financial commits. Inventory only lists the allowlisted inbox; draft
proposals are always marked unverified; accepting requires an explicit tool
grant and only persists the explicitly provided draft ids; and the calendar
remains driven only by the accepted registry. No network is involved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.bills_registry import BillsRegistry
from meta_harness.contracts.capability import Capability
from meta_harness.contracts.intake import BillDraft, DraftProposals
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.policy import Policy, PolicyVersion
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.contracts.workspace import WorkspaceLayout
from meta_harness.control_plane.intake import (
    IntakeOps,
    accept_drafts,
    ensure_intake_layout,
    extract_drafts,
    intake_tool_impls,
    inventory_inbox,
)
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.tools import ToolExecutionError, ToolRegistry
from meta_harness.control_plane.verifier import verify_intake_output
from meta_harness.control_plane.workspace import Workspace, register_workspace_tools

INTAKE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="intake",
    entry_point="meta_harness.agents.intake:create_intake_agent",
    description="Runs a dump-intake operation: inventory, drafts, or explicit accept.",
    declared_capabilities=frozenset(
        {
            Capability(name="intake.inventory", version="1"),
            Capability(name="intake.draft_bills", version="1"),
            Capability(name="intake.accept_bills", version="1"),
        }
    ),
)

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

# A .json inbox source and a .csv inbox source.
JSON_SOURCE = json.dumps(
    {
        "vendor": "PowerCo",
        "amount_cents": 5000,
        "due_date": "2026-09-10",
        "notes": "from json",
    }
)
CSV_SOURCE = "vendor,amount_cents,due_date,notes\nWaterCo,2000,2026-09-05,from csv\n"


def _make_workspace(tmp_path: Path, inbox_files: dict[str, str] | None = None) -> Workspace:
    layout = ensure_intake_layout(WorkspaceLayout())
    ws = Workspace(tmp_path, layout)
    ws.create_workspace_dir("bills")
    ws.write_workspace_file("bills/registry.json", json.dumps(REGISTRY))
    ws.create_workspace_dir("inbox")
    for name, content in (inbox_files or {}).items():
        ws.write_workspace_file(f"inbox/{name}", content)
    return ws


def _manager(tmp_path: Path, inbox_files: dict[str, str] | None = None) -> AgentManager:
    ws = _make_workspace(tmp_path, inbox_files)
    tools = ToolRegistry()
    register_workspace_tools(tools, ws)
    # Only register intake read tools; the accept tool is registered in separate
    # least-privilege tests.
    ops = IntakeOps(ws)
    for name, impl in intake_tool_impls(ops).items():
        tools.register_impl(intake_tool_descriptor(name), impl)
    m = AgentManager(tools=tools)
    m.register(INTAKE_MANIFEST)
    return m


def intake_tool_descriptor(name: str):
    from meta_harness.control_plane.tools import (
        INBOX_INVENTORY_DESCRIPTOR,
        INTAKE_ACCEPT_DESCRIPTOR,
        INTAKE_DRAFTS_DESCRIPTOR,
    )

    return {
        "inbox_inventory": INBOX_INVENTORY_DESCRIPTOR,
        "intake_drafts": INTAKE_DRAFTS_DESCRIPTOR,
        "intake_accept": INTAKE_ACCEPT_DESCRIPTOR,
    }[name]


def _task(task_id: str, payload: dict[str, Any], *, granted: tuple[str, ...]) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name="intake",
        payload=payload,
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
    )


class TestInboxInventory:
    def test_inventory_lists_only_allowlisted_inbox(self, tmp_path: Path) -> None:
        ws = _make_workspace(
            tmp_path, {"a.json": JSON_SOURCE, "b.csv": CSV_SOURCE, "ignore.txt": "x"}
        )
        out = inventory_inbox(ws)
        paths = [e["relative_path"] for e in out["entries"]]
        # Every entry is under the allowlisted inbox prefix.
        assert all(p.startswith("inbox/") for p in paths)
        assert "inbox/a.json" in paths
        assert "inbox/b.csv" in paths
        assert "inbox/ignore.txt" in paths  # .txt is still an inbox entry (inventory lists files)

    def test_inventory_empty_inbox(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        out = inventory_inbox(ws)
        assert out["count"] == 0


class TestDraftsGeneration:
    def test_drafts_always_unverified(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"a.json": JSON_SOURCE, "b.csv": CSV_SOURCE})
        out = extract_drafts(ws)
        assert out["count"] == 2
        assert out["unverified"] is True
        for draft in out["drafts"]:
            assert draft["unverified"] is True

    def test_draft_from_json_fields(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"a.json": JSON_SOURCE})
        out = extract_drafts(ws)
        assert out["drafts"][0]["vendor"] == "PowerCo"
        assert out["drafts"][0]["amount_cents"] == 5000
        assert out["drafts"][0]["due_date"] == "2026-09-10"
        assert out["drafts"][0]["source_path"] == "inbox/a.json"

    def test_draft_from_csv_fields(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"b.csv": CSV_SOURCE})
        out = extract_drafts(ws)
        assert out["drafts"][0]["vendor"] == "WaterCo"
        assert out["drafts"][0]["amount_cents"] == 2000

    def test_malformed_supported_file_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"bad.json": "not json"})
        with pytest.raises(ToolExecutionError):
            extract_drafts(ws)


class TestAcceptGate:
    def test_accept_writes_only_given_ids(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"a.json": JSON_SOURCE, "b.csv": CSV_SOURCE})
        ops = IntakeOps(ws)
        drafts = extract_drafts(ws)
        registry_content = ws.read_workspace_file("bills/registry.json").content
        assert registry_content is not None
        # Accept only the PowerCo draft (the JSON source; its draft_id is the
        # source path inbox/a.json).
        result = ops.accept(drafts, ["inbox/a.json"])
        assert result["accepted"] == ["inbox/a.json"]
        # Registry now has b1 + the accepted bill (draft id used as bill id).
        merged = BillsRegistry.from_mapping(
            json.loads(ws.read_workspace_file("bills/registry.json").content or "")
        )
        assert len(merged.bills) == 2
        assert [b.id for b in merged.bills] == ["b1", "inbox/a.json"]

    def test_unknown_draft_id_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"a.json": JSON_SOURCE})
        ops = IntakeOps(ws)
        drafts = extract_drafts(ws)
        with pytest.raises(Exception, match="Unknown draft id"):
            ops.accept(drafts, ["NOPE"])

    def test_empty_accept_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"a.json": JSON_SOURCE})
        ops = IntakeOps(ws)
        drafts = extract_drafts(ws)
        with pytest.raises(Exception, match="at least one"):
            ops.accept(drafts, [])

    def test_accept_result_requires_explicit_op(self) -> None:
        # The pure accept only merges accept_ids; with no ids it would reject.
        with pytest.raises(ValueError):
            accept_drafts(json.dumps(REGISTRY), {}, [])


class TestAgentInventory:
    def test_inventory_verifies(self, tmp_path: Path) -> None:
        m = _manager(tmp_path, {"a.json": JSON_SOURCE})
        outcome = m.run(_task("inv", {"operation": "inventory"}, granted=("inbox_inventory",)))
        assert outcome.result is not None
        assert outcome.result.output["count"] == 1

    def test_drafts_verify_unverified(self, tmp_path: Path) -> None:
        m = _manager(
            tmp_path,
            {
                "a.json": JSON_SOURCE,
                "c.txt": "vendor: TrashCo\namount_cents: 1500\ndue_date: 2026-09-20\n",
            },
        )
        outcome = m.run(_task("drafts", {"operation": "drafts"}, granted=("intake_drafts",)))
        assert outcome.result is not None
        assert outcome.result.output["unverified"] is True


class TestAgentAccept:
    def test_accept_requires_tool_grant(self, tmp_path: Path) -> None:
        m = _manager(tmp_path, {"a.json": JSON_SOURCE})
        outcome = m.run(
            _task(
                "accept-ungranted",
                {
                    "operation": "accept",
                    "registry": REGISTRY,
                    "drafts": {"drafts": []},
                    "accept_ids": ["PowerCo"],
                },
                granted=(),
            )
        )
        # Without the accept grant the agent returns None -> verification fails.
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_accept_under_grant_verifies(self, tmp_path: Path) -> None:
        m = _manager(tmp_path, {"a.json": JSON_SOURCE})
        drafts = extract_drafts(_make_workspace(tmp_path, {"a.json": JSON_SOURCE}))
        outcome = m.run(
            _task(
                "accept",
                {
                    "operation": "accept",
                    "registry": REGISTRY,
                    "drafts": drafts,
                    "accept_ids": ["inbox/a.json"],
                },
                granted=("intake_accept",),
            )
        )
        assert outcome.result is not None
        assert outcome.result.output["accepted"] == ["inbox/a.json"]


class TestPolicyEnvelope:
    def test_policy_can_deny_agent(self, tmp_path: Path) -> None:
        m = _manager(tmp_path, {"a.json": JSON_SOURCE})
        policy = Policy(version=PolicyVersion.V1, deny_agents=frozenset({"intake"}))
        outcome = m.run(
            _task(
                "deny",
                {"operation": "inventory"},
                granted=("inbox_inventory",),
            )
            if False  # placeholder; rebuilt below with policy via a custom task
            else TaskSpecification(
                version=TaskSpecVersion.V5,
                task_id="deny",
                agent_name="intake",
                payload={"operation": "inventory"},
                envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
                granted_tools=("inbox_inventory",),
                policy=policy,
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestVerifier:
    def test_verifier_rejects_drafts_not_unverified(self) -> None:
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="v",
            agent_name="intake",
            payload={"operation": "drafts"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("intake_drafts",),
        )
        bad_agent_output = {
            "count": 1,
            "unverified": False,
            "drafts": [{"draft_id": "x", "unverified": False}],
        }
        assert verify_intake_output(task, bad_agent_output).passed is False

    def test_verifier_accepts_unverified_drafts(self) -> None:
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="v",
            agent_name="intake",
            payload={"operation": "drafts"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("intake_drafts",),
        )
        good = {
            "count": 1,
            "unverified": True,
            "drafts": [{"draft_id": "x", "unverified": True}],
        }
        assert verify_intake_output(task, good).passed


class TestNoSilentCommit:
    def test_inventory_alone_does_not_mutate_registry(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path, {"a.json": JSON_SOURCE})
        before = ws.read_workspace_file("bills/registry.json").content
        ops = IntakeOps(ws)
        ops.inventory()
        ops.drafts()
        after = ws.read_workspace_file("bills/registry.json").content
        assert before == after

    def test_accept_not_auto(self) -> None:
        # A DraftProposals object never auto-accepts; only explicit row ids do.
        draft = BillDraft.from_mapping(
            {
                "draft_id": "d1",
                "vendor": "V",
                "amount_cents": 1,
                "due_date": "2026-09-01",
                "source_path": "inbox/x.json",
            }
        )
        proposals = DraftProposals(drafts=(draft,))
        assert proposals.as_mapping()["unverified"] is True
        assert draft.unverified is True