"""Dump Intake: inbox inventory + unverified draft proposals + human accept (Volley 026 / 029).

This module provides the deterministic intake pipeline for a local workspace:

- ``ensure_intake_layout`` extends the allowlist with an ``inbox/`` drop zone
  (and keeps the bills registry layout).
- ``inventory_inbox`` lists only the allowlisted inbox files.
- ``extract_drafts`` turns supported structured sources (``.json``,
  ``.csv``, and simple ``.txt`` mappings) into ``BillDraft`` proposals that are
  **always** ``unverified``.
- ``draft_from_email`` turns a fetched email message (subject + body) into
  ``BillDraft`` proposals that are **always** ``unverified``; weak/absent
  content fails closed to no draft.
- ``accept_drafts`` is a pure upsert that merges only the explicitly provided
  draft rows into the registry; it never auto-accepts anything.

``IntakeOps`` binds these to a ``Workspace`` and exposes them as mediated tools
(``inbox_inventory``, ``intake_drafts`` (read-only), ``intake_email_draft``
(read-only) and the least-privilege ``intake_accept`` which requires the
accept-drafts tool grant). Calendar remains driven only by the accepted
registry.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable
from typing import Any

from ..contracts.bills_registry import BillsRegistry
from ..contracts.email import EmailMessage
from ..contracts.intake import AcceptResult, BillDraft, DraftProposals, InboxEntry, InboxInventory
from ..contracts.workspace import WorkspaceLayout
from .bills_registry import BILLS_REGISTRY_PATH, ensure_bills_layout, load_registry
from .pdf_text import extract_text as extract_pdf_text
from .tools import ToolExecutionError
from .workspace import Workspace, WorkspaceError

# The inbox drop zone prefix.
INBOX_PREFIX = "inbox/"
# The drafts file (single JSON), if a proposal record is kept.
# v1 keeps proposals in-memory; this path documents the convention for a future
# persisted drafts ledger and is validated on the allowlist for readiness.
DRAFTS_PATH = "bills/drafts.json"

# Supported structured intake source suffixes (minimal, testable, offline).
_SUPPORTED_SUFFIXES = (".json", ".csv", ".txt", ".pdf")


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


_AMOUNT_RE = re.compile(
    r"\b(?:total|amount)\s*[:$]?\s*\$?([0-9]+(?:\.[0-9]{1,2})?)\b",
    re.IGNORECASE,
)
_DUE_RE = re.compile(
    r"\b(?:due date|due|pay by)\s*[:]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\b",
    re.IGNORECASE,
)


def _parse_money(value: str) -> int | None:
    """Parse an inline ``12.34`` or ``1234`` figure into integer cents.

    Returns None if the value cannot be converted cleanly (do not invent).
    """
    v = value.strip()
    if v.count(".") == 1:
        whole, _, frac = v.partition(".")
        if frac and len(frac) <= 2 and whole.isdigit() and frac.isdigit():
            return int(whole) * 100 + int(frac.ljust(2, "0"))
        return None
    if v.isdigit():
        return int(v)
    return None


def _extract_from_pdf(source_path: str, content: bytes) -> BillDraft:
    """Extract a draft from PDF embedded text (conservative, fail-closed).

    We parse simple heuristics (vendor / amount / due date) from the embedded
    text only **into an unverified BillDraft**. If no usable text or a weak
    parse leaves any required field missing, we fail closed (raise ValueError)
    rather than inventing amounts or due dates; the caller surfaces this as a
    ``ToolExecutionError`` and no draft is produced.
    """
    text = extract_pdf_text(content)
    notes: list[str] = []
    if not text:
        notes.append("no embedded text found; no fields extracted")
    vendor = ""
    amount_cents: int | None = None
    due_date = ""
    if text:
        m = _AMOUNT_RE.search(text)
        if m:
            amount_cents = _parse_money(m.group(1))
            if amount_cents is None:
                notes.append("amount present but unparseable; left empty")
        dm = _DUE_RE.search(text)
        if dm:
            due_date = dm.group(1)
        # A very light vendor heuristic: a short phrase after "vendor" /
        # "from" / "billed to", bounded to a few words and stopping at a comma
        # or line end. Conservative; may be empty (then we fail closed).
        vm = re.search(
            r"\b(vendor|from|billed to)\s*[:\-]?\s*"
            r"([A-Za-z0-9&.'\-]+(?:\s+(?!total|amount|due|date)[A-Za-z0-9&.'\-]+){0,3})",
            text,
            re.IGNORECASE,
        )
        if vm:
            vendor = vm.group(2).strip()
        if amount_cents is None and not due_date:
            notes.append("no trustworthy amount/date; partial draft")
    return BillDraft.from_mapping(
        {
            "draft_id": source_path,
            "vendor": vendor,
            "amount_cents": amount_cents,
            "due_date": due_date,
            "source_path": source_path,
            "notes": (
                "extracted from PDF text; " + "; ".join(notes)
                if notes
                else "extracted from PDF text"
            ),
        }
    )


_VENDOR_EMAIL_RE = re.compile(
    r"\b(?:from|vendor|billed by)\s*[:.]?\s*"
    r"([A-Za-z0-9&.'\-]+(?:\s+(?!total|amount|due|date)[A-Za-z0-9&.'\-]+){0,2})",
    re.IGNORECASE,
)


def draft_from_email(message: dict[str, Any] | EmailMessage) -> dict[str, Any]:
    """Produce unverified bill drafts from a fetched email message (Volley 029).

    Heuristics parse vendor / amount / due date from the email subject and body
    (local only, fixture-tested). The result is a ``DraftProposals`` mapping
    with **unverified: true** and the source pointing at the message
    (``email://folder/id``). If the body/subject cannot be parsed into a
    complete, non-empty draft, we **fail closed** and return **no drafts**
    (``count == 0``) rather than invent facts.

    No send/delete is ever performed. This never writes to the registry;
    persisting requires the separate ``intake_accept`` gate.
    """
    msg = message.as_mapping() if isinstance(message, EmailMessage) else message
    if not isinstance(msg, dict):
        raise ValueError("email draft source must be a message mapping.")
    folder = msg.get("folder")
    message_id = msg.get("id")
    if not isinstance(folder, str) or not folder:
        raise ValueError("email message is missing a non-empty 'folder'.")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("email message is missing a non-empty 'id'.")
    subject = msg.get("subject") or ""
    body = msg.get("body") or ""
    source_path = f"email://{folder}/{message_id}"
    text = f"{subject} {body}"

    amount_cents: int | None = None
    due_date = ""
    vendor = ""
    m = _AMOUNT_RE.search(text)
    if m:
        amount_cents = _parse_money(m.group(1))
    dm = _DUE_RE.search(text)
    if dm:
        due_date = dm.group(1)
    vm = _VENDOR_EMAIL_RE.search(text)
    if vm:
        vendor = vm.group(1).strip()

    # Fail closed: a complete draft needs a vendor, a parseable amount, and a
    # due date. Any missing field -> no draft (we do not invent facts).
    if not vendor or amount_cents is None or not due_date:
        return DraftProposals(drafts=()).as_mapping()
    return DraftProposals(
        drafts=(
            BillDraft(
                draft_id=f"{message_id}:{due_date}",
                vendor=vendor,
                amount_cents=amount_cents,
                due_date=due_date,
                source_path=source_path,
                notes="extracted from email (unverified); human accept required",
                confidence="low",
            ),
        )
    ).as_mapping()


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
        if rel.endswith(".pdf"):
            try:
                pdf_bytes = workspace.read_bytes(rel)
            except WorkspaceError as exc:
                raise ToolExecutionError(str(exc)) from exc
            try:
                drafts.append(_extract_from_pdf(rel, pdf_bytes))
            except ValueError as exc:
                raise ToolExecutionError(str(exc)) from exc
            continue
        try:
            entry = workspace.read_workspace_file(rel)
        except WorkspaceError as exc:
            raise ToolExecutionError(str(exc)) from exc
        text = entry.content
        assert text is not None
        try:
            if rel.endswith(".json"):
                drafts.append(_extract_from_json(rel, text))
            elif rel.endswith(".csv"):
                drafts.append(_extract_from_csv(rel, text))
            else:
                drafts.append(_extract_from_txt(rel, text))
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

    def email_draft(self, message: dict[str, Any]) -> dict[str, Any]:
        """Turn a fetched email message into unverified bill drafts (read-only).

        This is a least-privilege, read-only operation: it never sends, deletes,
        or mutates mail, and it never writes to the registry. Weak/unparseable
        content fails closed to an empty draft set (no invented facts).
        Persisting any produced draft still requires the separate ``intake_accept``
        gate.
        """
        if not isinstance(message, dict):
            raise ToolExecutionError("email_draft requires a message mapping.")
        try:
            return draft_from_email(message)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc

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
        "intake_email_draft": ops.email_draft,
    }