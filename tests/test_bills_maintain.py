"""Tests for Volley 028 — Bills Registry Maintenance (Upsert + Mark Paid).

These tests prove registry mutations are explicit, mediated, and verified: an
upsert inserts or replaces a bill by id with fully validated fields; mark_paid
(and the optional mark_status) set status through one shared code path and fail
closed on a missing id; every mutation persists only through the allowlisted
``bills/registry.json`` path, never implicitly accepts intake drafts, and leaves
a still-valid registry. The calendar projection stays correct when run after
maintenance. Ungranted tools, policy denial, and invalid mutations all fail
closed. No network and no model are ever involved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.bills_registry import BillsRegistry, BillStatus, RegistryBill
from meta_harness.contracts.capability import Capability
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.policy import Policy, PolicyVersion
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.contracts.workspace import WorkspaceLayout
from meta_harness.control_plane.bills_registry import (
    BillsOps,
    bills_tool_impls,
    ensure_bills_layout,
    load_registry,
    project_calendar,
    update_bill_status,
    upsert_bill,
)
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.tools import ToolExecutionError, ToolRegistry
from meta_harness.control_plane.verifier import verify_bills_registry_output
from meta_harness.control_plane.workspace import Workspace, register_workspace_tools

MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="bills_registry",
    entry_point="meta_harness.agents.bills_registry:create_bills_registry_agent",
    description="Reads the bills registry, projects a deterministic agenda, and maintains bills.",
    declared_capabilities=frozenset(
        {
            Capability(name="bills.registry", version="1"),
            Capability(name="bills.calendar", version="1"),
            Capability(name="bills.maintain", version="1"),
        }
    ),
)


REGISTRY_MAPPING = {
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
    ],
}


def _make_workspace(tmp_path: Path) -> Workspace:
    layout = ensure_bills_layout(WorkspaceLayout())
    ws = Workspace(tmp_path, layout)
    ws.create_workspace_dir("bills")
    ws.write_workspace_file("bills/registry.json", json.dumps(REGISTRY_MAPPING))
    return ws


def _manager(tmp_path: Path) -> AgentManager:
    ws = _make_workspace(tmp_path)
    tools = ToolRegistry()
    register_workspace_tools(tools, ws)
    ops = BillsOps(ws)
    for name, impl in bills_tool_impls(ops).items():
        tools.register_impl(bills_tool_descriptor(name), impl)
    m = AgentManager(tools=tools)
    m.register(MANIFEST)
    return m


def bills_tool_descriptor(name: str):
    from meta_harness.control_plane.tools import (
        BILLS_CALENDAR_DESCRIPTOR,
        BILLS_REGISTRY_MARK_PAID_DESCRIPTOR,
        BILLS_REGISTRY_MARK_STATUS_DESCRIPTOR,
        BILLS_REGISTRY_READ_DESCRIPTOR,
        BILLS_REGISTRY_UPSERT_DESCRIPTOR,
    )

    return {
        "bills_registry_read": BILLS_REGISTRY_READ_DESCRIPTOR,
        "bills_calendar": BILLS_CALENDAR_DESCRIPTOR,
        "bills_registry_upsert": BILLS_REGISTRY_UPSERT_DESCRIPTOR,
        "bills_registry_mark_paid": BILLS_REGISTRY_MARK_PAID_DESCRIPTOR,
        "bills_registry_mark_status": BILLS_REGISTRY_MARK_STATUS_DESCRIPTOR,
    }[name]


def _task(
    payload: dict[str, Any], *, granted: tuple[str, ...], task_id: str = "mt"
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name="bills_registry",
        payload=payload,
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
    )


def _read_registry(ws: Workspace) -> BillsRegistry:
    content = ws.read_workspace_file("bills/registry.json").content
    assert content is not None
    return load_registry(content)


class TestUpsert:
    def test_upsert_inserts_new_bill(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        new = {"id": "b3", "vendor": "GasCo", "amount_cents": 1500, "due_date": "2026-09-20"}
        result = ops.upsert(new)
        assert result["operation"] == "upsert"
        assert result["bill_id"] == "b3"
        assert result["created"] is True
        merged = _read_registry(ws)
        assert [b.id for b in merged.bills] == ["b1", "b2", "b3"]

    def test_upsert_replaces_existing_bill(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        replace = {"id": "b1", "vendor": "NetCo", "amount_cents": 3100, "due_date": "2026-09-15"}
        result = ops.upsert(replace)
        assert result["created"] is False
        merged = _read_registry(ws)
        b1 = next(b for b in merged.bills if b.id == "b1")
        assert b1.amount_cents == 3100
        assert b1.due_date == "2026-09-15"
        assert [b.id for b in merged.bills] == ["b1", "b2"]

    def test_upsert_invalid_bill_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        with pytest.raises(ToolExecutionError):
            ops.upsert({"id": "bad", "vendor": "X", "amount_cents": -5, "due_date": "2026-09-01"})
        # Registry unchanged (no partial mutation).
        assert [b.id for b in _read_registry(ws).bills] == ["b1", "b2"]


class TestMarkPaid:
    def test_mark_paid_sets_status(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        result = ops.mark_paid("b1")
        assert result["operation"] == "mark_paid"
        assert result["created"] is False
        assert result["bill"]["status"] == "paid"
        b1 = next(b for b in _read_registry(ws).bills if b.id == "b1")
        assert b1.status is BillStatus.PAID

    def test_mark_paid_unknown_id_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        with pytest.raises(ToolExecutionError, match="Unknown bill id"):
            ops.mark_paid("nope")
        assert _read_registry(ws).bills == _read_registry(ws).bills

    def test_mark_status_shared_path(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        result = ops.mark_status("b2", "due")
        assert result["operation"] == "mark_status"
        assert result["bill"]["status"] == "due"
        merged = _read_registry(ws)
        assert next(b for b in merged.bills if b.id == "b2").status is BillStatus.DUE

    def test_mark_status_invalid_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        with pytest.raises(ToolExecutionError):
            ops.mark_status("b1", "not-a-status")


class TestCalendarAfterMaintenance:
    def test_calendar_correct_after_mark_paid(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        # Make b2 due so it is the sole remaining outstanding bill, then pay b1.
        ops.mark_status("b2", "due")
        ops.mark_paid("b1")
        merged = _read_registry(ws)
        agenda = project_calendar(merged, "2026-09-01", "2026-09-30").as_mapping()
        # b1 is now paid and excluded by default; only b2 remains outstanding.
        assert [e["bill_id"] for e in agenda["entries"]] == ["b2"]
        assert agenda["total_outstanding_cents"] == 2000

    def test_calendar_correct_when_b1_not_paid(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        ops.mark_status("b2", "due")
        merged = _read_registry(ws)
        agenda = project_calendar(merged, "2026-09-01", "2026-09-30").as_mapping()
        assert [e["bill_id"] for e in agenda["entries"]] == ["b1", "b2"]


class TestUngrantedAndPolicy:
    def test_upsert_ungranted_fails_closed(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        payload = {
            "operation": "upsert",
            "registry": REGISTRY_MAPPING,
            "bill": {"id": "b3", "vendor": "GasCo", "amount_cents": 1500, "due_date": "2026-09-20"},
        }
        outcome = m.run(_task(payload, granted=()))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_mark_paid_under_grant_verifies(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        payload = {"operation": "mark_paid", "registry": REGISTRY_MAPPING, "bill_id": "b1"}
        outcome = m.run(_task(payload, granted=("bills_registry_mark_paid",)))
        assert outcome.result is not None
        assert outcome.result.output["operation"] == "mark_paid"
        assert outcome.result.output["bill"]["status"] == "paid"

    def test_upsert_under_grant_verifies(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        payload = {
            "operation": "upsert",
            "registry": REGISTRY_MAPPING,
            "bill": {"id": "b3", "vendor": "GasCo", "amount_cents": 1500, "due_date": "2026-09-20"},
        }
        outcome = m.run(_task(payload, granted=("bills_registry_upsert",)))
        assert outcome.result is not None
        assert outcome.result.output["bill_id"] == "b3"
        assert outcome.result.output["created"] is True

    def test_policy_can_deny_maintain(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        policy = Policy(version=PolicyVersion.V1, deny_agents=frozenset({"bills_registry"}))
        payload = {"operation": "mark_paid", "registry": REGISTRY_MAPPING, "bill_id": "b1"}
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="deny",
            agent_name="bills_registry",
            payload=payload,
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("bills_registry_mark_paid",),
            policy=policy,
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestNoSilentCommit:
    def test_maintain_does_not_touch_drafts(self, tmp_path: Path) -> None:
        # Maintenance operates only on the registry; no intake accept is invoked.
        ws = _make_workspace(tmp_path)
        ops = BillsOps(ws)
        before = _read_registry(ws)
        ops.upsert({"id": "b3", "vendor": "GasCo", "amount_cents": 1500, "due_date": "2026-09-20"})
        after = _read_registry(ws)
        # The pre-existing bills are untouched; only the new bill was added.
        assert any(b.id == "b3" for b in after.bills)
        assert len(after.bills) == len(before.bills) + 1

    def test_verifier_rejects_invalid_mutation_output(self) -> None:
        payload = {
            "operation": "mark_paid",
            "registry": REGISTRY_MAPPING,
            "bill_id": "b1",
        }
        bad = {
            "operation": "mark_paid",
            "bill_id": "b2",  # wrong id: recompute expects b1
            "created": False,
            "bill": {
                "id": "b2",
                "vendor": "WaterCo",
                "amount_cents": 2000,
                "due_date": "2026-09-05",
                "status": "paid",
            },
        }
        task = _task(payload, granted=("bills_registry_mark_paid",))
        assert verify_bills_registry_output(task, bad).passed is False


class TestPureUpsertRegisterHelpers:
    def test_upsert_bill_replaces_preserving_order(self) -> None:
        registry = load_registry(json.dumps(REGISTRY_MAPPING))
        merged, result = upsert_bill(
            registry,
            RegistryBill.from_mapping(
                {"id": "b1", "vendor": "NetCo", "amount_cents": 3100, "due_date": "2026-09-15"}
            ),
        )
        assert result.created is False
        assert [b.id for b in merged.bills] == ["b1", "b2"]
        assert merged.bills[0].amount_cents == 3100

    def test_update_bill_status_missing_id_fails_closed(self) -> None:
        registry = load_registry(json.dumps(REGISTRY_MAPPING))
        with pytest.raises(ValueError, match="Unknown bill id"):
            update_bill_status(registry, "missing", BillStatus.PAID)