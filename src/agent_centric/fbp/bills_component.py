"""Pure, allowlisted behavior for the example ``bills_registry`` components.

These are the **real** execution entries behind the example components
(``examples/components/bills_registry`` and its ``agenda`` referenced child):
the distributed bundles may ship their own reference code for documentation, but
Phase 1b executes only allowlisted in-tree entries (see
:mod:`agent_centric.fbp.component_runtime`). Each entry is a pure, deterministic
function over a JSON-style mapping — no I/O, no model, no clock — so a run is
reproducible and independently verifiable.

All three reuse the canonical ``bills_registry.v1`` contract and the
deterministic projection logic in
:mod:`agent_centric.control_plane.bills_registry`; nothing here invents due
dates or amounts.
"""

from __future__ import annotations

from typing import Any

from ..contracts.bills_registry import (
    BillsRegistry,
    RegistryBill,
)
from ..control_plane.bills_registry import project_calendar


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping.")
    return value


def registry_snapshot(payload: Any) -> dict[str, Any]:
    """Validate a registry mapping and return its canonical form (pure).

    Payload: ``{"registry": {...}}``. Rejects a missing or malformed registry
    (fail-closed via the contract's own validation).
    """
    data = _mapping(payload, "registry_snapshot payload")
    registry = BillsRegistry.from_mapping(data.get("registry"))
    return registry.as_mapping()


def validate_bill(payload: Any) -> dict[str, Any]:
    """Validate a single bill mapping and return its canonical form (pure).

    Payload: ``{"bill": {...}}``. Rejects a malformed bill (fail-closed).
    """
    data = _mapping(payload, "validate_bill payload")
    bill = RegistryBill.from_mapping(data.get("bill"))
    return bill.as_mapping()


def project_agenda(payload: Any) -> dict[str, Any]:
    """Project a deterministic agenda from a validated registry (pure).

    Payload: ``{"registry": {...}, "from_date": "YYYY-MM-DD",
    "to_date": "YYYY-MM-DD", "include_paid": bool}``. Reads due dates and
    amounts from registry data only; never invents them.
    """
    data = _mapping(payload, "project_agenda payload")
    registry = BillsRegistry.from_mapping(data.get("registry"))
    from_date = data.get("from_date")
    to_date = data.get("to_date")
    if not isinstance(from_date, str) or not from_date:
        raise TypeError("project_agenda payload['from_date'] must be a string.")
    if not isinstance(to_date, str) or not to_date:
        raise TypeError("project_agenda payload['to_date'] must be a string.")
    include_paid = data.get("include_paid", False)
    if not isinstance(include_paid, bool):
        raise TypeError("project_agenda payload['include_paid'] must be a bool.")
    projection = project_calendar(
        registry, from_date, to_date, include_paid=include_paid
    )
    return projection.as_mapping()
