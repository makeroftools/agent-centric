"""Dump Intake: inbox inventory + unverified draft proposals + human accept (Volley 026).

This module provides the deterministic intake pipeline for a local workspace:

- ``ensure_intake_layout`` extends the allowlist with an ``inbox/`` drop zone
  (and keeps the bills registry layout).
- ``inventory_inbox`` lists only the allowlisted inbox files.
- ``extract_drafts`` turns supported structured sources (``.json``,
  ``.csv``, and simple ``.txt`` mappings) into ``BillDraft`` proposals that are
  **always** ``unverified``.
- ``accept_drafts`` is a pure upsert that merges only the explicitly provided
  draft rows into the registry; it never auto-accepts anything.

``IntakeOps`` binds these to a ``Workspace`` and exposes them as mediated tools
(``inbox_inventory``, ``intake_drafts`` (read-only) and the least-privilege
``intake_accept`` which requires the accept-drafts tool grant). Calendar remains
driven only by the accepted registry.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from typing import Any

from ..contracts.bills_registry import BillsRegistry
from ..contracts.intake import AcceptResult, BillDraft, DraftProposals, InboxEntry, InboxInventory
from ..contracts.workspace import WorkspaceLayout
from .bills_registry import BILLS_REGISTRY_PATH, ensure_bills_layout, load_registry
from .tools import ToolExecutionError
from .workspace import Workspace, WorkspaceError

# The inbox drop zone prefix.
INBOX_PREFIX = "inbox/"
# The drafts file (single JSON), if a proposal record is kept.
# v1 keeps proposals in-memory; this path documents the convention for a future
# persisted drafts ledger and is validated on the allowlist for readiness.
DRAFTS_PATH = "bills/drafts.json"

# Supported structured intake source suffixes (minimal, testable, offline).
_SUPPORTED_SUFFIXES = (".json", ".csv", ".txt")


def ensure_intake_layout(layout: WorkspaceLayout) -> WorkspaceLayout:
    """Return a layout that also allowlists the inbox drop zone and drafts file.

    Adds ``inbox/`` as an allowlisted prefix and ``bills/drafts.json`` as an
    allowed file (for a future persisted drafts ledger), on top of the bills
    registry layout. Reuses the existing workspace allowlist conventions.
    """
    bills = ensure_bills_layout(layout)
    prefixes = tuple(bills.prefixes)
    directories = tuple(bills.directories)
    files = tuple(bills.files)
    if INBOX_PREFIX not in prefixes:
        prefixes = prefixes + (INBOX_PREFIX,)
    if "inbox" not in directories:
        directories = directories + ("inbox",)
    if DRAFTS_PATH not in files:
        files = files + (DRAFTS_PATH,)
    return WorkspaceLayout(
        files=files,
        directories=directories,
        prefixes=prefixes,
    )


def inventory_inbox(workspace: Workspace, prefix: str = INBOX_PREFIX) -> dict[str, Any]:
    """List the allowlisted inbox files deterministically.

    Only the allowlisted prefix is scanned; anything else is rejected by the
    workspace's allowlist (fail-closed). Returns an ``InboxInventory`` mapping.
    """
    try:
        listing = workspace.list_prefix(prefix)
    except WorkspaceError as exc:
        raise ToolExecutionError(str(exc)) from exc
    entries = tuple(InboxEntry(relative_path=rel) for rel in sorted(listing))
    return InboxInventory(entries=entries).as_mapping()


def _extract_from_json(source_path: str, content: str) -> BillDraft:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{source_path}: JSON must be an object.")
    return BillDraft.from_mapping(
        {
            "draft_id": data.get("draft_id") or source_path,
            "vendor": data.get("vendor", ""),
            "amount_cents": data.get("amount_cents"),
            "due_date": data.get("due_date", ""),
            "source_path": source_path,
            "notes": data.get("notes", ""),
        }
    )


def _extract_from_csv(source_path: str, content: str) -> BillDraft:
    try:
        reader = list(csv.DictReader(io.StringIO(content)))
    except Exception as exc:  # noqa: BLE001 - mapped below
        raise ValueError(f"{source_path}: could not parse CSV ({exc})") from exc
    if not reader:
        raise ValueError(f"{source_path}: CSV has no data rows.")
    row = reader[0]
    amount_raw = row.get("amount_cents", "")
    try:
        amount_cents = int(amount_raw) if amount_raw not in ("", None) else None
    except ValueError:
        amount_cents = None
    return BillDraft.from_mapping(
        {
            "draft_id": row.get("draft_id") or source_path,
            "vendor": row.get("vendor", ""),
            "amount_cents": amount_cents,
            "due_date": row.get("due_date", ""),
            "source_path": source_path,
            "notes": row.get("notes", ""),
        }
    )


def _extract_from_txt(source_path: str, content: str) -> BillDraft:
    """Extract a draft from a simple key: value text file.

    This is a minimal, deterministic heuristic (no model): lines of
    ``vendor: X``, ``amount_cents: N``, ``due_date: YYYY-MM-DD``. The result is
    still an unverified draft for human review.
    """
    fields: dict[str, str] = {}
    for line in content.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    amount_raw = fields.get("amount_cents", "")
    try:
        amount_cents = int(amount_raw) if amount_raw else None
    except ValueError:
        amount_cents = None
    return BillDraft.from_mapping(
        {
            "draft_id": source_path,
            "vendor": fields.get("vendor", ""),
            "amount_cents": amount_cents,
            "due_date": fields.get("due_date", ""),
            "source_path": source_path,
            "notes": "extracted from plain text",
        }
    )


def extract_drafts(workspace: Workspace, prefix: str = INBOX_PREFIX) -> dict[str, Any]:
    """Extract unverified draft proposals from the allowlisted inbox.

    Only supported suffixes are read; unsupported files are skipped (not
    errors). Every produced draft is ``unverified: True``. A malformed supported
    file fails closed (no silent partial draft).
    """
    try:
        listing = workspace.list_prefix(prefix)
    except WorkspaceError as exc:
        raise ToolExecutionError(str(exc)) from exc
    drafts: list[BillDraft] = []
    for rel in sorted(listing):
        if not any(rel.endswith(suffix) for suffix in _SUPPORTED_SUFFIXES):
            continue
        try:
            entry = workspace.read_workspace_file(rel)
        except WorkspaceError as exc:
            raise ToolExecutionError(str(exc)) from exc
        assert entry.content is not None
        try:
            if rel.endswith(".json"):
                drafts.append(_extract_from_json(rel, entry.content))
            elif rel.endswith(".csv"):
                drafts.append(_extract_from_csv(rel, entry.content))
            else:
                drafts.append(_extract_from_txt(rel, entry.content))
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
    return DraftProposals(drafts=tuple(drafts)).as_mapping()


def accept_drafts(
    registry_content: str,
    drafts_mapping: dict[str, Any],
    accept_ids: list[str],
) -> tuple[BillsRegistry, AcceptResult]:
    """Merge only the explicitly provided draft rows into the registry (pure).

    This is the single accept path. It validates the current registry and the
    draft collection, rejects unknown/malformed draft ids, generates stable new
    bill ids for the accepted drafts, and returns the merged registry plus the
    ``AcceptResult``. It never auto-accepts: only ``accept_ids`` are merged, and
    the merged registry must still validate.

    Raises:
        ValueError: If the registry/drafts are malformed, or an accept id is
            unknown, or the merged registry would be invalid (e.g. duplicate id).
    """
    registry = load_registry(registry_content)
    if not accept_ids:
        raise ValueError("accept requires at least one draft id.")
    drafts_mapping_obj = DraftProposals(
        drafts=tuple(
            BillDraft.from_mapping(d) for d in drafts_mapping.get("drafts", [])
        )
    )
    by_id: dict[str, BillDraft] = {d.draft_id: d for d in drafts_mapping_obj.drafts}
    if len(by_id) != len(drafts_mapping_obj.drafts):
        raise ValueError("Draft ids must be unique.")
    existing = {b.id for b in registry.bills}

    accepted_draft_ids: list[str] = []
    accepted_bill_ids: list[str] = []
    new_bills = list(registry.bills)
    for draft_id in accept_ids:
        draft = by_id.get(draft_id)
        if draft is None:
            raise ValueError(f"Unknown draft id {draft_id!r}.")
        # Derive a stable, collision-free registry bill id from the draft id.
        bill_id = draft_id
        counter = 1
        base = draft_id
        while bill_id in existing:
            bill_id = f"{base}#{counter}"
            counter += 1
        existing.add(bill_id)
        new_bills.append(draft.to_registry_bill(bill_id))
        accepted_draft_ids.append(draft_id)
        accepted_bill_ids.append(bill_id)

    merged = BillsRegistry(
        version=registry.version,
        bills=tuple(new_bills),
        description=registry.description,
    )
    return merged, AcceptResult(
        accepted_ids=tuple(accepted_draft_ids),
        registry_bill_ids=tuple(accepted_bill_ids),
    )


class IntakeOps:
    """Binds intake operations to a Workspace with least-privilege tool grants."""

    def __init__(
        self, workspace: Workspace, *, default_folders: tuple[str, ...] = ()
    ) -> None:
        self._workspace = workspace

    def inventory(self) -> dict[str, Any]:
        return inventory_inbox(self._workspace)

    def drafts(self) -> dict[str, Any]:
        return extract_drafts(self._workspace)

    def accept(self, drafts: dict[str, Any], accept_ids: list[str]) -> dict[str, Any]:
        """Accept only the given draft ids into the registry and persist.

        Reads the current registry, accepts only the requested rows, writes the
        merged registry back through the allowlisted workspace write path, and
        returns the ``AcceptResult``. This is the only path that mutates the
        registry, and it requires the accept-drafts grant.
        """
        if not isinstance(drafts, dict):
            raise ToolExecutionError("accept requires a drafts mapping.")
        if not isinstance(accept_ids, list):
            raise ToolExecutionError("accept requires a list of draft ids.")
        if not accept_ids:
            raise ToolExecutionError("accept requires at least one draft id.")
        try:
            registry_content = self._workspace.read_workspace_file(
                BILLS_REGISTRY_PATH
            ).content
        except WorkspaceError as exc:
            raise ToolExecutionError(str(exc)) from exc
        assert registry_content is not None
        try:
            merged, result = accept_drafts(registry_content, drafts, accept_ids)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        try:
            self._workspace.write_workspace_file(
                BILLS_REGISTRY_PATH,
                json.dumps(merged.as_mapping(), sort_keys=True),
            )
        except WorkspaceError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return result.as_mapping()


def intake_tool_impls(ops: IntakeOps) -> dict[str, Callable[..., Any]]:
    """Return the mediated intake tool implementations bound to ``ops``."""
    return {
        "inbox_inventory": ops.inventory,
        "intake_drafts": ops.drafts,
        "intake_accept": ops.accept,
    }