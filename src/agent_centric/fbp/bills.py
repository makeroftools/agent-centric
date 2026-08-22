"""The bills loop as a real, end-to-end FBP graph.

This is the first *real* demonstration of the FBP foundation on a
mission-relevant workflow: intake -> human-gated accept -> durable registry ->
verified calendar projection. It exercises the correctness spine where it
matters most — **no unverified money/dates**.

Design (fits the architecture we hardened):

- **Pure domain functions** (`bill_total`, `draft_from_intake`, `accept_draft`,
  `project_calendar`) are registered **capabilities** — deterministic, read-only
  where possible, fail-closed on malformed input.
- **A single-writer `StoreAgent`** owns the durable registry (keyed by bill id).
  Nothing writes the registry except the store agent, under grant.
- **A coordinating `BillsAgent`** drives the loop over the bus: it asks the
  store for the registry, applies the pure functions, and persists accepted
  bills back through the store. It never touches the store file directly.
- **Human-gated accept**: a draft becomes a registry bill only via an explicit
  `accept` step; nothing auto-accepts. Amounts are integer cents; dates are
  ISO; a malformed draft fails closed (no invented facts).

The whole loop is deterministic and auditable: every step is a directive, every
response is recorded in the local audit, and the parent re-verifies on the way
up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class BillsError(ValueError):
    """A bills-loop input violated the domain contract (fail-closed)."""


@dataclass(frozen=True)
class BillDraft:
    """An unverified bill proposal from intake.

    Attributes:
        id: A stable, deterministic id (never auto-generated).
        vendor: The vendor name.
        amount_cents: The amount in integer cents (>= 0).
        due_date: An ISO date string (YYYY-MM-DD).
    """

    id: str
    vendor: str
    amount_cents: int
    due_date: str


def bill_total(
    lines: list[dict[str, Any]], discount_bps: int = 0, tax_bps: int = 0
) -> dict[str, Any]:
    """Compute a deterministic bill total from line items.

    Args:
        lines: ``[{"description": str, "quantity": int, "unit_price_cents": int}]``.
        discount_bps / tax_bps: Basis points (1/10000) applied to the subtotal.

    Returns:
        ``{"subtotal_cents": int, "discount_cents": int, "tax_cents": int,
        "total_cents": int}``.

    Raises:
        BillsError: On malformed lines or negative quantities/prices.
    """
    if not isinstance(lines, list) or not lines:
        raise BillsError("bill_total requires a non-empty 'lines' list")
    subtotal = 0
    for line in lines:
        if not isinstance(line, dict):
            raise BillsError(f"line is not a dict: {line!r}")
        qty = line.get("quantity")
        price = line.get("unit_price_cents")
        if not isinstance(qty, int) or qty < 0:
            raise BillsError(f"line requires a non-negative integer 'quantity': {line!r}")
        if not isinstance(price, int) or price < 0:
            raise BillsError(
                f"line requires a non-negative integer 'unit_price_cents': {line!r}"
            )
        subtotal += qty * price
    discount = round(subtotal * discount_bps / 10000)
    tax = round((subtotal - discount) * tax_bps / 10000)
    total = subtotal - discount + tax
    return {
        "subtotal_cents": subtotal,
        "discount_cents": discount,
        "tax_cents": tax,
        "total_cents": total,
    }


def draft_from_intake(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn an intake row into an unverified bill draft (fail-closed).

    Args:
        raw: ``{"id": str, "vendor": str, "amount_cents": int, "due_date": str}``.

    Returns:
        A draft dict. Raises ``BillsError`` on malformed data — a weak/absent
        parse fails closed to no draft (no invented facts).
    """
    bid = raw.get("id")
    vendor = raw.get("vendor")
    amount = raw.get("amount_cents")
    due = raw.get("due_date")
    if not isinstance(bid, str) or not bid:
        raise BillsError("draft requires a string 'id'")
    if not isinstance(vendor, str) or not vendor:
        raise BillsError(f"draft {bid!r} requires a string 'vendor'")
    if not isinstance(amount, int) or amount < 0:
        raise BillsError(f"draft {bid!r} requires a non-negative integer 'amount_cents'")
    if not isinstance(due, str) or len(due) != 10:
        raise BillsError(f"draft {bid!r} requires an ISO 'due_date' (YYYY-MM-DD)")
    return {"id": bid, "vendor": vendor, "amount_cents": amount, "due_date": due}


def accept_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """The human-gated accept: promote a verified draft to a registry bill.

    This is the only path that writes a bill into the registry. It is explicit
    and never automatic. The draft must already be well-formed (it came from
    ``draft_from_intake``); accept adds a ``status`` of ``open``.
    """
    if not isinstance(draft, dict) or not draft.get("id"):
        raise BillsError("accept requires a well-formed draft")
    return {
        "id": draft["id"],
        "vendor": draft["vendor"],
        "amount_cents": draft["amount_cents"],
        "due_date": draft["due_date"],
        "status": "open",
    }


# The allowed bill statuses (deterministic, closed set).
ALLOWED_STATUSES = ("open", "paid", "void", "overdue")


def mark_bill_status(bill: dict[str, Any], status: str, *, note: str = "") -> dict[str, Any]:
    """The registry-maintenance status update (a pure, deterministic merge).

    Returns a ball with its ``status`` set to a member of ``ALLOWED_STATUSES``,
    preserving every other field. ``note`` (e.g. a payment reference) is attached
    if given. An unknown status fails closed (``BillsError``).

    This is explicit and mediated: it only ever mutates ``status`` (and an
    optional ``note``); it never implicitly re-accepts an intake draft or changes
    money/dates.
    """
    if not isinstance(status, str) or status not in ALLOWED_STATUSES:
        raise BillsError(
            f"invalid status {status!r} (allowed: {', '.join(ALLOWED_STATUSES)})"
        )
    if not isinstance(bill, dict) or not bill.get("id"):
        raise BillsError("mark_status requires a well-formed registry bill")
    updated = dict(bill)
    updated["status"] = status
    if note:
        updated["note"] = note
    return updated


def project_calendar(
    registry: dict[str, dict[str, Any]], from_date: str, to_date: str
) -> dict[str, Any]:
    """Project a deterministic agenda from the accepted registry.

    Args:
        registry: ``{bill_id: bill_dict}`` (the durable registry).
        from_date / to_date: ISO date bounds (inclusive).

    Returns:
        ``{"entries": [{"id", "vendor", "amount_cents", "due_date", "status"}],
        "total_cents": int}`` for bills due in [from_date, to_date] with
        status ``open``. Deterministic (sorted by due_date then id).
    """
    entries = []
    for bid in sorted(registry):
        bill = registry[bid]
        due = bill.get("due_date")
        status = bill.get("status")
        if status != "open":
            continue
        if due is None or not (from_date <= due <= to_date):
            continue
        entries.append(
            {
                "id": bill["id"],
                "vendor": bill["vendor"],
                "amount_cents": bill["amount_cents"],
                "due_date": due,
                "status": status,
            }
        )
    entries.sort(key=lambda e: (e["due_date"], e["id"]))
    total = sum(e["amount_cents"] for e in entries)
    return {"entries": entries, "total_cents": total}