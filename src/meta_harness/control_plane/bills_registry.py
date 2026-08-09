"""Bills-registry and calendar projection logic + mediated tools (Volley 025).

This module provides the deterministic, pure logic for a canonical local bills
registry that lives in the allowlisted workspace, plus the mediated tools that
expose it to an agent under Manager governance.

- ``load_registry`` parses and validates a registry document (JSON) into a
  ``BillsRegistry``, rejecting bad/missing data fail-closed.
- ``project_calendar`` produces a deterministic, ordered ``CalendarProjection``
  for a date window with an explicit include/exclude-paid policy.
- ``BillsOps`` binds the pure logic to a ``Workspace`` so the tools read only
  the allowlisted ``bills/registry.json`` path, never arbitrary files.

No model is involved anywhere in this path — this is boring, correct projection
of registry data only. Due dates and amounts are never invented.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Callable
from typing import Any

from ..contracts.bills_registry import (
    AgendaEntry,
    BillsRegistry,
    BillStatus,
    CalendarProjection,
    MaintainResult,
    RegistryBill,
)
from ..contracts.workspace import WorkspaceLayout
from .tools import ToolExecutionError
from .workspace import Workspace, WorkspaceError

# The canonical relative path of the bills registry within the workspace.
BILLS_REGISTRY_PATH = "bills/registry.json"
BILLS_DIRECTORY = "bills"


def ensure_bills_layout(layout: WorkspaceLayout) -> WorkspaceLayout:
    """Return a ``WorkspaceLayout`` that also allowlists the bills registry.

    This is the documented default layout helper: it adds ``bills`` as an
    allowed directory and ``bills/registry.json`` as an allowed file, so demos
    and tests can build a workspace that hosts the canonical registry on the
    allowlist while reusing the existing workspace tools (no broader fs).
    """
    files = tuple(layout.files)
    directories = tuple(layout.directories)
    if BILLS_REGISTRY_PATH not in files:
        files = files + (BILLS_REGISTRY_PATH,)
    if BILLS_DIRECTORY not in directories:
        directories = directories + (BILLS_DIRECTORY,)
    return WorkspaceLayout(files=files, directories=directories)


def load_registry(content: str) -> BillsRegistry:
    """Parse and validate a bills-registry document (JSON).

    Raises:
        ValueError: If the content is not valid JSON or the registry is malformed.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bills registry is not valid JSON: {exc}") from exc
    return BillsRegistry.from_mapping(data)


def _status_included(status: BillStatus, include_paid: bool) -> bool:
    if include_paid:
        return True
    return status is not BillStatus.PAID


def project_calendar(
    registry: BillsRegistry,
    from_date: str,
    to_date: str,
    include_paid: bool = False,
) -> CalendarProjection:
    """Project a deterministic, ordered agenda from a registry for a window.

    Entries are filtered to those whose due date falls within
    ``[from_date, to_date]`` (inclusive). By default only unpaid bills (``due``
    and ``skipped``) are included; ``include_paid=True`` also includes ``paid``.
    Entries are ordered by due date, then by bill id (a stable, deterministic
    tie-break). The total outstanding is the sum of ``amount_cents`` over the
    included entries.

    Raises:
        ValueError: If ``from_date`` is after ``to_date``.
    """
    start = _dt.date.fromisoformat(from_date)
    end = _dt.date.fromisoformat(to_date)
    if start > end:
        raise ValueError("from_date must not be after to_date.")

    entries: list[AgendaEntry] = []
    for bill in registry.bills:
        due = _dt.date.fromisoformat(bill.due_date)
        if due < start or due > end:
            continue
        if not _status_included(bill.status, include_paid):
            continue
        entries.append(
            AgendaEntry(
                due_date=bill.due_date,
                bill_id=bill.id,
                vendor=bill.vendor,
                amount_cents=bill.amount_cents,
                status=bill.status,
            )
        )

    entries.sort(key=lambda e: (e.due_date, e.bill_id))
    total = sum(e.amount_cents for e in entries)
    return CalendarProjection(
        from_date=from_date,
        to_date=to_date,
        include_paid=include_paid,
        entries=tuple(entries),
        total_outstanding_cents=total,
    )


def upsert_bill(
    registry: BillsRegistry, bill: RegistryBill
) -> tuple[BillsRegistry, MaintainResult]:
    """Insert or replace a bill by id with fully validated fields (pure).

    If no bill carries ``bill.id``, the bill is appended as a new record; if one
    already exists, it is replaced in place (order preserved). The merged
    registry is validated at construction, so an invalid bill or a mutation that
    would break invariants (e.g. duplicate ids) fails closed. This is a pure
    upsert — it never implicitly accepts drafts and never projects a calendar on
    its own.

    Returns:
        ``(merged_registry, MaintainResult)`` where ``created`` reports whether
        the upsert inserted a new record.

    Raises:
        ValueError: If the bill is invalid or the merged registry would be invalid.
    """
    existing_ids = {b.id for b in registry.bills}
    created = bill.id not in existing_ids
    if created:
        merged = BillsRegistry(
            version=registry.version,
            bills=registry.bills + (bill,),
            description=registry.description,
        )
    else:
        new_bills = tuple(bill if b.id == bill.id else b for b in registry.bills)
        merged = BillsRegistry(
            version=registry.version,
            bills=new_bills,
            description=registry.description,
        )
    return merged, MaintainResult(
        operation="upsert",
        bill_id=bill.id,
        created=created,
        bill=bill,
    )


_OP_FOR_STATUS = {
    BillStatus.PAID: "mark_paid",
    BillStatus.SKIPPED: "mark_status",
    BillStatus.DUE: "mark_status",
}


def _set_status(
    registry: BillsRegistry, bill_id: str, status: BillStatus
) -> tuple[BillsRegistry, MaintainResult]:
    """Set a bill's status by id (pure), failing closed on a missing id.

    Raises:
        ValueError: If ``bill_id`` does not exist in the registry.
    """
    matched: RegistryBill | None = None
    new_bills: list[RegistryBill] = []
    for current in registry.bills:
        if current.id == bill_id:
            matched = RegistryBill(
                id=current.id,
                vendor=current.vendor,
                amount_cents=current.amount_cents,
                due_date=current.due_date,
                status=status,
                category=current.category,
            )
            new_bills.append(matched)
        else:
            new_bills.append(current)
    if matched is None:
        raise ValueError(f"Unknown bill id {bill_id!r}.")
    merged = BillsRegistry(
        version=registry.version,
        bills=tuple(new_bills),
        description=registry.description,
    )
    return merged, MaintainResult(
        operation=_OP_FOR_STATUS[status],
        bill_id=bill_id,
        created=False,
        bill=matched,
    )


def update_bill_status(
    registry: BillsRegistry, bill_id: str, status: BillStatus
) -> tuple[BillsRegistry, MaintainResult]:
    """Set a bill's status by id via one shared code path (pure).

    ``mark_paid`` is a status set to ``paid``; ``mark_status`` accepts any valid
    status (due / paid / skipped). Both reuse ``_set_status`` so the required
    mark-paid path and the optional v1 mark-status practice stay one code path.

    Raises:
        ValueError: If the bill id is unknown or the status is invalid.
    """
    if not isinstance(status, BillStatus):
        try:
            status = BillStatus(status)
        except ValueError:
            raise ValueError(
                f"Bill status must be one of due/paid/skipped, got {status!r}."
            ) from None
    return _set_status(registry, bill_id, status)


class BillsOps:
    """Binds registry read + calendar projection to a Workspace.

    The mediated tools read only the allowlisted ``bills/registry.json`` path,
    parse and validate it, and project the agenda. Validation and projection are
    pure and deterministic; any disallowed path or malformed registry is a
    fail-closed error.
    """

    def __init__(self, workspace: Workspace) -> None:
        if not workspace.layout.allows_file(BILLS_REGISTRY_PATH):
            raise ValueError(
                f"Workspace layout must allow {BILLS_REGISTRY_PATH!r}; "
                "use ensure_bills_layout()."
            )
        self._workspace = workspace

    def _read_registry_content(self) -> str:
        try:
            content = self._workspace.read_workspace_file(BILLS_REGISTRY_PATH).content
        except WorkspaceError as exc:
            raise ToolExecutionError(str(exc)) from exc
        assert content is not None
        return content

    def load(self) -> dict[str, Any]:
        """Read + validate the registry and return it as a mapping."""
        try:
            registry = load_registry(self._read_registry_content())
        except ValueError as exc:
            raise ToolExecutionError(f"Invalid bills registry: {exc}") from exc
        return registry.as_mapping()

    def calendar(
        self, from_date: str, to_date: str, include_paid: bool = False
    ) -> dict[str, Any]:
        """Read the registry and project the calendar for a window."""
        try:
            registry = load_registry(self._read_registry_content())
            projection = project_calendar(
                registry, from_date, to_date, include_paid=include_paid
            )
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return projection.as_mapping()

    def _persist(self, registry: BillsRegistry) -> dict[str, Any]:
        """Write a merged registry back through the allowlisted path."""
        mapping = registry.as_mapping()
        try:
            self._workspace.write_workspace_file(
                BILLS_REGISTRY_PATH,
                json.dumps(mapping, sort_keys=True),
            )
        except WorkspaceError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return mapping

    def upsert(self, bill: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace a bill by id (explicit, grant-gated).

        Reads + validates the current registry, upserts the bill (pure), writes
        the merged registry back through the allowlisted path, and returns the
        ``MaintainResult``. Any invalid bill, malformed registry, or registry
        write failure fails closed (no partial mutation).
        """
        try:
            registry = load_registry(self._read_registry_content())
            merged, result = upsert_bill(registry, RegistryBill.from_mapping(bill))
            self._persist(merged)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return result.as_mapping()

    def mark_paid(self, bill_id: str) -> dict[str, Any]:
        """Set a bill's status to ``paid`` for a given id (fail closed if missing)."""
        try:
            registry = load_registry(self._read_registry_content())
            merged, result = update_bill_status(registry, bill_id, BillStatus.PAID)
            self._persist(merged)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return result.as_mapping()

    def mark_status(self, bill_id: str, status: str) -> dict[str, Any]:
        """Set a bill's status to ``due``/``paid``/``skipped`` (shared path)."""
        try:
            registry = load_registry(self._read_registry_content())
            merged, result = update_bill_status(registry, bill_id, BillStatus(status))
            self._persist(merged)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return result.as_mapping()


def bills_tool_impls(ops: BillsOps) -> dict[str, Callable[..., Any]]:
    """Return the mediated bills-registry tool implementations bound to ``ops``."""
    return {
        "bills_registry_read": ops.load,
        "bills_calendar": ops.calendar,
        "bills_registry_upsert": ops.upsert,
        "bills_registry_mark_paid": ops.mark_paid,
        "bills_registry_mark_status": ops.mark_status,
    }