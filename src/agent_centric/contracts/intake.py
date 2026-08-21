"""Dump Intake contracts: inbox inventory + bill draft proposals (versioned).

This module defines the structured contracts for the dump-intake specialty
agent (Volley 026). The design is deliberately narrow, deterministic, and
human-in-the-loop — nothing is written to the bills registry from a draft
unless an explicit, human-driven accept operation persists only the approved
rows.

- ``InboxEntry`` — an allowlisted file dropped in the inbox (relative path + kind).
- ``InboxInventory`` — a deterministic listing of inbox entries.
- ``BillDraft`` — a *proposed* bill that is always marked ``unverified``; it is
  never a final registry record until accepted.
- ``DraftProposals`` — a set of unverified drafts with an explicit flag.
- ``AcceptResult`` — the outcome of an explicit accept operation (which rows were
  accepted into the registry).

Money/schedule facts in a draft are untrusted until explicit accept. Every
contract validates at construction and rejects bad or missing data (fail-closed).
The calendar remains driven only by the accepted registry, never by raw drafts.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .bills_registry import BillStatus, RegistryBill


class IntakeVersion(StrEnum):
    """Version of the intake contract."""

    V1 = "intake.v1"


def _require_iso_date(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty ISO date string, got {value!r}.")
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date, got {value!r}.") from None
    return value


def _require_nonempty_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}.")
    return value


def _require_cents(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}.")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}.")
    return value


@dataclass(frozen=True)
class InboxEntry:
    """A single allowlisted file dropped in the inbox.

    Attributes:
        relative_path: The relative path of the file within the inbox.
        kind: ``file`` (all inbox entries in v1 are files).
    """

    relative_path: str
    kind: str = "file"

    def __post_init__(self) -> None:
        _require_nonempty_str(self.relative_path, "relative_path")
        if self.kind != "file":
            raise ValueError(f"Inbox entry kind must be 'file', got {self.kind!r}.")

    def as_mapping(self) -> dict[str, str]:
        return {"relative_path": self.relative_path, "kind": self.kind}


@dataclass(frozen=True)
class InboxInventory:
    """A deterministic listing of inbox entries.

    Attributes:
        entries: The allowlisted inbox files found.
    """

    entries: tuple[InboxEntry, ...]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "count": len(self.entries),
            "entries": [e.as_mapping() for e in self.entries],
        }


@dataclass(frozen=True)
class BillDraft:
    """A proposed, explicitly unverified bill.

    Attributes:
        draft_id: Stable identifier for this draft.
        vendor: Proposed vendor.
        amount_cents: Proposed amount in integer cents.
        due_date: Proposed ISO due date.
        source_path: The inbox file this draft was extracted from.
        unverified: Always ``True`` for a draft (money/schedule facts are
            untrusted until explicit accept).
        notes: Optional human/agent notes.
        confidence: Optional confidence hint (informational only).
    """

    draft_id: str
    vendor: str
    amount_cents: int
    due_date: str
    source_path: str
    unverified: bool = True
    notes: str = ""
    confidence: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_id", _require_nonempty_str(self.draft_id, "draft_id"))
        object.__setattr__(self, "vendor", _require_nonempty_str(self.vendor, "vendor"))
        object.__setattr__(self, "amount_cents", _require_cents(self.amount_cents, "amount_cents"))
        object.__setattr__(self, "due_date", _require_iso_date(self.due_date, "due_date"))
        object.__setattr__(
            self, "source_path", _require_nonempty_str(self.source_path, "source_path")
        )
        if not isinstance(self.unverified, bool):
            raise ValueError("unverified must be a bool (drafts are always unverified).")
        if not isinstance(self.notes, str):
            raise ValueError("notes must be a string.")
        if not isinstance(self.confidence, str):
            raise ValueError("confidence must be a string.")
        object.__setattr__(self, "unverified", True)

    @classmethod
    def from_mapping(cls, data: Any) -> BillDraft:
        """Build a ``BillDraft`` from a mapping, rejecting bad/missing data.

        Raises:
            ValueError: If ``data`` is not a mapping or any field is bad/missing.
        """
        if not isinstance(data, dict):
            raise ValueError(f"Draft must be a mapping, got {data!r}.")
        amount_cents = _require_cents(data.get("amount_cents"), "amount_cents")
        return cls(
            draft_id=data.get("draft_id", ""),
            vendor=data.get("vendor", ""),
            amount_cents=amount_cents,
            due_date=data.get("due_date", ""),
            source_path=data.get("source_path", ""),
            notes=data.get("notes", ""),
            confidence=data.get("confidence", ""),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "vendor": self.vendor,
            "amount_cents": self.amount_cents,
            "due_date": self.due_date,
            "source_path": self.source_path,
            "unverified": self.unverified,
            "notes": self.notes,
            "confidence": self.confidence,
        }

    def to_registry_bill(self, bill_id: str) -> RegistryBill:
        """Return the accepted registry record for this draft.

        Only an explicit accept operation may call this; the resulting record
        is a normal (verified) registry bill.
        """
        return RegistryBill(
            id=bill_id,
            vendor=self.vendor,
            amount_cents=self.amount_cents,
            due_date=self.due_date,
            status=BillStatus.DUE,
        )


@dataclass(frozen=True)
class DraftProposals:
    """A set of unverified draft proposals."""

    drafts: tuple[BillDraft, ...]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "count": len(self.drafts),
            "unverified": all(d.unverified for d in self.drafts),
            "drafts": [d.as_mapping() for d in self.drafts],
        }


@dataclass(frozen=True)
class AcceptResult:
    """The outcome of an explicit accept operation.

    Report-only: which draft ids were accepted and added to the registry.
    """

    accepted_ids: tuple[str, ...]
    registry_bill_ids: tuple[str, ...]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "accepted": list(self.accepted_ids),
            "registry_bill_ids": list(self.registry_bill_ids),
        }