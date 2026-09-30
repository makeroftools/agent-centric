"""Bundled reference behavior for the example ``agenda`` atomic component.

Projects a deterministic agenda (bills due in a date window) from a validated
registry mapping. It is the pointed-to (``ref``) child of the example
``bills_registry`` composite. The allowlisted in-tree entry is
``agent_centric.cbp.bills_component:project_agenda``.

Deterministic and read-only: entries are ordered by due date then bill id; the
total is the sum of the included amounts. Nothing is invented.
"""

from __future__ import annotations


def project_agenda(registry: dict, from_date: str, to_date: str) -> dict:
    """Project the agenda for ``[from_date, to_date]`` (fail-closed)."""
    if not isinstance(registry, dict):
        raise ValueError("registry must be a mapping")
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    entries = []
    for bill in registry.get("bills", ()):
        due = bill.get("due_date")
        if due is None or not (from_date <= due <= to_date):
            continue
        if bill.get("status", "due") == "paid":
            continue
        entries.append(
            {
                "due_date": due,
                "bill_id": bill.get("id"),
                "vendor": bill.get("vendor"),
                "amount_cents": bill.get("amount_cents"),
            }
        )
    entries.sort(key=lambda e: (e["due_date"], e["bill_id"]))
    return {"entries": entries, "count": len(entries)}
