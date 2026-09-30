"""Bundled reference behavior for the embedded ``bills_rules`` child.

An **embedded** (contained) atomic component of the example ``bills_registry``
composite: it validates a single bill record. Its files live inside the parent
bundle, so they are covered by the parent's ``tree_sha256`` — the "contains a
child" mode of ``SPEC-0007`` Phase 1b.

The allowlisted in-tree entry is
``agent_centric.cbp.bills_component:validate_bill``.
"""

from __future__ import annotations


def validate_bill(bill: dict) -> dict:
    """Return the validated bill with its fields normalised (fail-closed)."""
    if not isinstance(bill, dict):
        raise ValueError("bill must be a mapping")
    for field in ("id", "vendor", "amount_cents", "due_date"):
        if field not in bill:
            raise ValueError(f"bill missing required field {field!r}")
    return dict(bill)
