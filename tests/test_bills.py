"""Tests for Volley 022 — Accounting Bills v1.

These tests prove the bills specialty agent is governed by the same invariants
as every other agent: structured bills in, deterministic totals out, real
verification (independent recompute), fail-closed rejection of bad/missing
data, Manager-mediated tool access, full trajectory recording, deterministic
replay, hard resource envelopes, and policy enforcement. Money math is
integer-only (cents) and deterministic — no cloud model is involved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meta_harness.contracts.bill import Bill, BillLine, BillTotal
from meta_harness.contracts.capability import Capability
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.policy import Policy, PolicyVersion
from meta_harness.contracts.result import FailureReason
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.control_plane.manager import AgentManager
from meta_harness.control_plane.tools import ToolExecutionError, ToolRegistry
from meta_harness.control_plane.trajectory_store import FileTrajectoryStore
from meta_harness.control_plane.verifier import verify_bills_output

BILLS_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="bills",
    entry_point="meta_harness.agents.bills:create_bills_agent",
    description="Computes deterministic totals for a structured bill.",
    declared_capabilities=frozenset({Capability(name="bills", version="1")}),
)


def _bill_task(
    task_id: str,
    bill: dict[str, Any],
    *,
    granted: tuple[str, ...] = (),
    envelope: ResourceEnvelope | None = None,
    policy: Policy | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name="bills",
        payload=bill,
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
        policy=policy,
    )


def _manager(tmp_path: Path | None = None) -> AgentManager:
    m = AgentManager(store=FileTrajectoryStore(tmp_path) if tmp_path else None)
    m.register(BILLS_MANIFEST)
    return m


# A representative bill: 2 widgets @ $10.00 (1000c) + 3 gadgets @ $2.50 (250c).
_SIMPLE_BILL = {
    "lines": [
        {"description": "widget", "quantity": 2, "unit_price_cents": 1000},
        {"description": "gadget", "quantity": 3, "unit_price_cents": 250},
    ],
}


class TestBillContracts:
    def test_bill_total_compute_is_exact(self) -> None:
        bill = Bill.from_mapping(_SIMPLE_BILL)
        total = BillTotal.compute(bill)
        # 2*1000 + 3*250 = 2000 + 750 = 2750 cents.
        assert total.line_subtotal_cents == 2750
        assert total.discount_cents == 0
        assert total.taxable_amount_cents == 2750
        assert total.tax_cents == 0
        assert total.grand_total_cents == 2750

    def test_discount_and_tax_rounding(self) -> None:
        bill = Bill.from_mapping(
            {
                "lines": [{"description": "x", "quantity": 1, "unit_price_cents": 100}],
                "discount_bps": 1000,  # 10%
                "tax_bps": 500,  # 5%
            }
        )
        total = BillTotal.compute(bill)
        assert total.line_subtotal_cents == 100
        assert total.discount_cents == 10
        assert total.taxable_amount_cents == 90
        assert total.tax_cents == 5  # round(90 * 0.05) = round(4.5) = 5 (half-up)
        assert total.grand_total_cents == 95

    def test_half_up_rounding_is_deterministic(self) -> None:
        # 1 cent at 50% tax = 0.5 -> rounds half-up to 1.
        bill = Bill.from_mapping(
            {
                "lines": [{"description": "x", "quantity": 1, "unit_price_cents": 1}],
                "tax_bps": 5000,
            }
        )
        assert BillTotal.compute(bill).tax_cents == 1

    def test_bill_rejects_empty_lines(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            Bill.from_mapping({"lines": []})

    def test_bill_rejects_missing_lines(self) -> None:
        with pytest.raises(ValueError, match="lines"):
            Bill.from_mapping({})

    def test_bill_rejects_non_mapping(self) -> None:
        with pytest.raises(ValueError):
            Bill.from_mapping("not a bill")

    def test_line_rejects_bad_quantity(self) -> None:
        with pytest.raises(ValueError, match="quantity"):
            BillLine.from_mapping({"description": "x", "quantity": 0, "unit_price_cents": 100})

    def test_line_rejects_missing_price(self) -> None:
        with pytest.raises(ValueError, match="unit_price_cents"):
            BillLine.from_mapping({"description": "x", "quantity": 1})

    def test_line_rejects_negative_price(self) -> None:
        with pytest.raises(ValueError, match="unit_price_cents"):
            BillLine.from_mapping({"description": "x", "quantity": 1, "unit_price_cents": -5})

    def test_bill_rejects_out_of_range_rate(self) -> None:
        with pytest.raises(ValueError, match="discount_bps"):
            Bill.from_mapping(
                {
                    "lines": [{"description": "x", "quantity": 1, "unit_price_cents": 100}],
                    "discount_bps": 10001,
                }
            )

    def test_bill_total_as_mapping_round_trips(self) -> None:
        total = BillTotal.compute(Bill.from_mapping(_SIMPLE_BILL))
        mapping = total.as_mapping()
        assert mapping["grand_total_cents"] == 2750
        assert BillTotal(**mapping) == total


class TestBillsAgent:
    def test_simple_bill_verifies(self) -> None:
        m = _manager()
        outcome = m.run(_bill_task("simple", _SIMPLE_BILL))
        assert outcome.result is not None
        assert outcome.result.output == BillTotal.compute(
            Bill.from_mapping(_SIMPLE_BILL)
        ).as_mapping()
        assert outcome.result.output["grand_total_cents"] == 2750

    def test_discount_and_tax_bill_verifies(self) -> None:
        m = _manager()
        bill = {
            "lines": [{"description": "x", "quantity": 1, "unit_price_cents": 100}],
            "discount_bps": 1000,
            "tax_bps": 500,
        }
        outcome = m.run(_bill_task("rates", bill))
        assert outcome.result is not None
        assert outcome.result.output["grand_total_cents"] == 95

    def test_bad_payload_fails_closed(self) -> None:
        m = _manager()
        outcome = m.run(_bill_task("bad", {"lines": []}))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.AGENT_ERROR

    def test_missing_data_fails_closed(self) -> None:
        m = _manager()
        outcome = m.run(_bill_task("missing", {}))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.AGENT_ERROR

    def test_verifier_rejects_bad_output(self) -> None:
        task = _bill_task("v", _SIMPLE_BILL)
        good = BillTotal.compute(Bill.from_mapping(_SIMPLE_BILL)).as_mapping()
        assert verify_bills_output(task, good).passed
        wrong = {
            "line_subtotal_cents": 1,
            "discount_cents": 0,
            "taxable_amount_cents": 1,
            "tax_cents": 0,
            "grand_total_cents": 1,
        }
        assert verify_bills_output(task, wrong).passed is False
        assert verify_bills_output(task, None).passed is False
        assert verify_bills_output(task, {"line_subtotal_cents": 1}).passed is False

    def test_verifier_rejects_bad_payload(self) -> None:
        task = _bill_task("v", {"lines": []})
        assert verify_bills_output(task, None).passed is False


class TestBillsTool:
    def test_bill_total_tool_registered(self) -> None:
        reg = ToolRegistry()
        desc = reg.descriptor("bill_total")
        assert desc is not None
        assert desc.name == "bill_total"
        assert reg.execute("bill_total", {"bill": _SIMPLE_BILL})["grand_total_cents"] == 2750

    def test_bill_total_tool_rejects_bad_bill(self) -> None:
        reg = ToolRegistry()
        with pytest.raises(ToolExecutionError, match="Invalid bill"):
            reg.execute("bill_total", {"bill": {"lines": []}})

    def test_granted_tool_path_verifies(self) -> None:
        m = _manager()
        outcome = m.run(_bill_task("tool", _SIMPLE_BILL, granted=("bill_total",)))
        assert outcome.result is not None
        assert outcome.result.output["grand_total_cents"] == 2750

    def test_tool_interaction_recorded_in_trajectory(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        outcome = m.run(_bill_task("traj", _SIMPLE_BILL, granted=("bill_total",)))
        assert outcome.result is not None
        assert outcome.trajectory_id is not None
        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any("bill_total' request" in d for d in descriptions)
        assert any("bill_total' result" in d for d in descriptions)

    def test_ungranted_tool_still_verifies_locally(self) -> None:
        """Without the tool the agent computes locally; the result still verifies."""
        m = _manager()
        outcome = m.run(_bill_task("ungranted", _SIMPLE_BILL, granted=()))
        assert outcome.result is not None
        assert outcome.result.output["grand_total_cents"] == 2750


class TestBillsEnvelope:
    def test_step_limit_fails_closed(self) -> None:
        m = _manager()
        outcome = m.run(
            _bill_task(
                "budget",
                _SIMPLE_BILL,
                envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT


class TestBillsPolicy:
    def test_policy_can_deny_the_bills_agent(self) -> None:
        m = _manager()
        policy = Policy(version=PolicyVersion.V1, deny_agents=frozenset({"bills"}))
        outcome = m.run(_bill_task("deny-agent", _SIMPLE_BILL, policy=policy))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestBillsDeterminism:
    def test_deterministic_and_replayable(self, tmp_path: Path) -> None:
        m = _manager(tmp_path)
        first = m.run(_bill_task("det", _SIMPLE_BILL, granted=("bill_total",)))
        second = m.run(_bill_task("det", _SIMPLE_BILL, granted=("bill_total",)))
        assert first.result is not None and second.result is not None
        assert first.result.output == second.result.output

        def sig(outcome) -> list[tuple[int, str, str, Any]]:
            return [
                (s.step_index, s.status.value, s.description, s.output)
                for s in outcome.result.trajectory.steps
            ]

        assert sig(first) == sig(second)


class TestBillsSubprocess:
    def test_runs_under_subprocess_backend(self, tmp_path: Path) -> None:
        from meta_harness.control_plane.execution import SubprocessBackend

        m = AgentManager(
            store=FileTrajectoryStore(tmp_path),
            backend=SubprocessBackend(),
        )
        m.register(BILLS_MANIFEST)
        outcome = m.run(_bill_task("sub", _SIMPLE_BILL, granted=("bill_total",)))
        assert outcome.result is not None
        assert outcome.result.output["grand_total_cents"] == 2750