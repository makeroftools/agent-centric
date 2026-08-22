"""Tests for the FBP bills-loop pure domain functions (``fbp/bills.py``).

These exercise the deterministic, read-only capabilities that the bills agent
uses: total computation, intake→draft, human-gated accept, status maintenance,
and calendar projection — including every fail-closed branch.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.bills import (
    BillsError,
    accept_draft,
    bill_total,
    draft_from_intake,
    mark_bill_status,
    project_calendar,
)


def _reg(*bills: dict) -> dict:
    return {b["id"]: b for b in bills}


class TestBillTotal:
    def test_computes_totals_with_discount_and_tax(self) -> None:
        out = bill_total(
            [
                {"description": "a", "quantity": 2, "unit_price_cents": 1000},
                {"description": "b", "quantity": 3, "unit_price_cents": 250},
            ],
            discount_bps=500,  # 5%
            tax_bps=800,  # 8%
        )
        subtotal = 2000 + 750
        discount = round(subtotal * 500 / 10000)
        tax = round((subtotal - discount) * 800 / 10000)
        assert out["subtotal_cents"] == subtotal
        assert out["discount_cents"] == discount
        assert out["tax_cents"] == tax
        assert out["total_cents"] == subtotal - discount + tax

    def test_requires_non_empty_lines(self) -> None:
        with pytest.raises(BillsError, match="non-empty"):
            bill_total([])

    def test_rejects_non_dict_line(self) -> None:
        with pytest.raises(BillsError, match="not a dict"):
            bill_total(["not-a-dict"])

    def test_rejects_negative_quantity(self) -> None:
        with pytest.raises(BillsError, match="quantity"):
            bill_total([{"quantity": -1, "unit_price_cents": 100}])

    def test_rejects_negative_price(self) -> None:
        with pytest.raises(BillsError, match="unit_price_cents"):
            bill_total([{"quantity": 1, "unit_price_cents": -5}])


class TestDraftFromIntake:
    def test_builds_draft(self) -> None:
        d = draft_from_intake(
            {"id": "b1", "vendor": "GasCo", "amount_cents": 12345, "due_date": "2026-10-01"}
        )
        assert d["id"] == "b1" and d["amount_cents"] == 12345

    def test_fail_closed_on_bad_field(self) -> None:
        with pytest.raises(BillsError):
            draft_from_intake({"id": "b1", "vendor": "X", "amount_cents": "NaN", "due_date": "x"})


class TestAcceptDraft:
    def test_adds_open_status(self) -> None:
        d = draft_from_intake(
            {"id": "b1", "vendor": "GasCo", "amount_cents": 100, "due_date": "2026-10-01"}
        )
        accepted = accept_draft(d)
        assert accepted["status"] == "open"

    def test_rejects_malformed_draft(self) -> None:
        with pytest.raises(BillsError, match="well-formed"):
            accept_draft({})


class TestMarkBillStatus:
    def test_mark_paid_preserves_fields_and_note(self) -> None:
        bill = {"id": "b1", "vendor": "GasCo", "amount_cents": 10, "status": "open"}
        updated = mark_bill_status(bill, "paid", note="ref-42")
        assert updated["status"] == "paid"
        assert updated["note"] == "ref-42"
        assert updated["amount_cents"] == 10  # money untouched

    def test_invalid_status_fails_closed(self) -> None:
        bill = {"id": "b1", "status": "open"}
        with pytest.raises(BillsError, match="invalid status"):
            mark_bill_status(bill, "somewhere")

    def test_malformed_bill_fails_closed(self) -> None:
        with pytest.raises(BillsError, match="well-formed"):
            mark_bill_status({}, "paid")


class TestProjectCalendar:
    def test_filters_by_status_and_window(self) -> None:
        reg = _reg(
            {"id": "a", "vendor": "A", "amount_cents": 100,
             "due_date": "2026-10-05", "status": "open"},
            {"id": "b", "vendor": "B", "amount_cents": 200,
             "due_date": "2026-10-06", "status": "open"},
            {"id": "c", "vendor": "C", "amount_cents": 50,
             "due_date": "2026-10-07", "status": "paid"},
        )
        out = project_calendar(reg, "2026-10-01", "2026-10-31")
        assert [e["id"] for e in out["entries"]] == ["a", "b"]
        assert out["total_cents"] == 300

    def test_window_excludes_outside(self) -> None:
        reg = _reg(
            {"id": "b1", "vendor": "A", "amount_cents": 100,
             "due_date": "2026-09-01", "status": "open"}
        )
        out = project_calendar(reg, "2026-10-01", "2026-10-31")
        assert out["entries"] == []
        assert out["total_cents"] == 0

    def test_empty_registry(self) -> None:
        out = project_calendar({}, "2026-10-01", "2026-10-31")
        assert out["entries"] == [] and out["total_cents"] == 0