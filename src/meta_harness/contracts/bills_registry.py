"""Bills Registry + Calendar Agenda contracts (versioned).

This module defines the structured contracts for the bills-registry and
calendar-projection specialty agent (Volley 025). The design is deliberately
narrow and deterministic — a canonical local bills registry lives in the
allowlisted workspace, and a calendar/agenda projection answers "what is due
when" purely from registry data, with no model involved.

- ``RegistryBill`` — a single validated bill record: id, vendor, amount
  (integer cents), due date (ISO date), and optional status/category.
- ``BillsRegistry`` — a versioned list of bills plus basic metadata.
- ``AgendaEntry`` — one calendar entry: date, bill id, vendor, amount, status.
- ``CalendarProjection`` — an ordered agenda for a date window.

All amounts are integer minor units (cents) and all dates are ISO ``YYYY-MM-DD``
strings. Every contract validates at construction and rejects bad or missing
data (fail-closed). Recurrence is intentionally omitted in v1 and documented as
a future extension.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BillsRegistryVersion(StrEnum):
    """Version of the bills-registry contract."""

    V1 = "bills_registry.v1"


class BillStatus(StrEnum):
    """Status of a registry bill."""

    DUE = "due"
    PAID = "paid"
    SKIPPED = "skipped"


def _require_iso_date(value: Any, name: str) -> str:
    """Coerce ``value`` to a valid ``YYYY-MM-DD`` ISO date string.

    Raises:
        ValueError: If ``value`` is not a valid ISO date string.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty ISO date string, got {value!r}.")
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date, got {value!r}.") from None
    return value


def _require_cents(value: Any, name: str) -> int:
    """Coerce ``value`` to a non-negative integer amount in cents.

    Raises:
        ValueError: If ``value`` is not a non-negative int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}.")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}.")
    return value


@dataclass(frozen=True)
class RegistryBill:
    """A single validated bill record in the registry.

    Attributes:
        id: Stable, unique identifier for the bill.
        vendor: The vendor the bill is owed to. Non-empty.
        amount_cents: The amount owed, in integer minor units (cents).
        due_date: The due date as an ISO ``YYYY-MM-DD`` string.
        status: ``due`` (default), ``paid``, or ``skipped``.
        category: Optional free-text category (may be empty).
    """

    id: str
    vendor: str
    amount_cents: int
    due_date: str
    status: BillStatus = BillStatus.DUE
    category: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Bill id must be a non-empty string.")
        if not isinstance(self.vendor, str) or not self.vendor:
            raise ValueError("Bill vendor must be a non-empty string.")
        object.__setattr__(self, "amount_cents", _require_cents(self.amount_cents, "amount_cents"))
        object.__setattr__(self, "due_date", _require_iso_date(self.due_date, "due_date"))
        if not isinstance(self.category, str):
            raise ValueError("Bill category must be a string.")
        if not isinstance(self.status, BillStatus):
            try:
                object.__setattr__(self, "status", BillStatus(self.status))
            except ValueError:
                raise ValueError(
                    f"Bill status must be one of due/paid/skipped, got {self.status!r}."
                ) from None

    @classmethod
    def from_mapping(cls, data: Any) -> RegistryBill:
        """Build a ``RegistryBill`` from a mapping, rejecting bad/missing data.

        Raises:
            ValueError: If ``data`` is not a mapping or any field is bad/missing.
        """
        if not isinstance(data, dict):
            raise ValueError(f"Bill record must be a mapping, got {data!r}.")
        amount_cents = _require_cents(data.get("amount_cents"), "amount_cents")
        due_date = _require_iso_date(data.get("due_date"), "due_date")
        return cls(
            id=data.get("id", ""),
            vendor=data.get("vendor", ""),
            amount_cents=amount_cents,
            due_date=due_date,
            status=data.get("status", "due"),
            category=data.get("category", ""),
        )

    def as_mapping(self) -> dict[str, Any]:
        """Return the record as a plain mapping (JSON-serialisable)."""
        return {
            "id": self.id,
            "vendor": self.vendor,
            "amount_cents": self.amount_cents,
            "due_date": self.due_date,
            "status": self.status.value,
            "category": self.category,
        }


@dataclass(frozen=True)
class BillsRegistry:
    """A versioned list of bills plus basic metadata.

    Attributes:
        version: The bills-registry contract version.
        bills: The ordered list of bill records. Must be non-empty.
        description: Optional human-readable description of the registry.
    """

    version: BillsRegistryVersion
    bills: tuple[RegistryBill, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if self.version is not BillsRegistryVersion.V1:
            raise ValueError(f"Unsupported bills-registry version: {self.version!r}.")
        if not self.bills:
            raise ValueError("BillsRegistry must contain at least one bill.")
        ids = [b.id for b in self.bills]
        if len(set(ids)) != len(ids):
            raise ValueError("BillsRegistry bill ids must be unique.")
        if not isinstance(self.description, str):
            raise ValueError("description must be a string.")

    @classmethod
    def from_mapping(cls, data: Any) -> BillsRegistry:
        """Build a ``BillsRegistry`` from a mapping, rejecting bad/missing data.

        Raises:
            ValueError: If ``data`` is not a mapping, ``bills`` is missing/empty,
                or any record is bad/missing.
        """
        if not isinstance(data, dict):
            raise ValueError(f"BillsRegistry must be a mapping, got {data!r}.")
        raw_bills = data.get("bills")
        if not isinstance(raw_bills, (list, tuple)) or not raw_bills:
            raise ValueError("BillsRegistry 'bills' must be a non-empty list.")
        bills = tuple(RegistryBill.from_mapping(b) for b in raw_bills)
        return cls(
            version=BillsRegistryVersion.V1,
            bills=bills,
            description=data.get("description", ""),
        )

    def as_mapping(self) -> dict[str, Any]:
        """Return the registry as a plain mapping (JSON-serialisable)."""
        return {
            "version": self.version.value,
            "bills": [b.as_mapping() for b in self.bills],
            "description": self.description,
        }


@dataclass(frozen=True)
class AgendaEntry:
    """A single deterministic calendar/agenda entry.

    Attributes:
        due_date: The due date as an ISO ``YYYY-MM-DD`` string.
        bill_id: The id of the bill this entry is for.
        vendor: The vendor of the bill.
        amount_cents: The amount owed, in integer minor units (cents).
        status: The bill's status.
    """

    due_date: str
    bill_id: str
    vendor: str
    amount_cents: int
    status: BillStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "due_date", _require_iso_date(self.due_date, "due_date"))
        if not isinstance(self.bill_id, str) or not self.bill_id:
            raise ValueError("Agenda bill_id must be a non-empty string.")
        if not isinstance(self.vendor, str) or not self.vendor:
            raise ValueError("Agenda vendor must be a non-empty string.")
        object.__setattr__(
            self, "amount_cents", _require_cents(self.amount_cents, "amount_cents")
        )
        if not isinstance(self.status, BillStatus):
            object.__setattr__(self, "status", BillStatus(self.status))

    def as_mapping(self) -> dict[str, Any]:
        """Return the entry as a plain mapping (JSON-serialisable)."""
        return {
            "due_date": self.due_date,
            "bill_id": self.bill_id,
            "vendor": self.vendor,
            "amount_cents": self.amount_cents,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class CalendarProjection:
    """A deterministic, ordered agenda for a date window.

    Attributes:
        from_date: Inclusive window start (ISO date).
        to_date: Inclusive window end (ISO date).
        include_paid: Whether paid bills are included.
        entries: The ordered agenda entries (by due date, then bill id).
        total_outstanding_cents: Sum of amount for included entries.
    """

    from_date: str
    to_date: str
    include_paid: bool
    entries: tuple[AgendaEntry, ...]
    total_outstanding_cents: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "from_date", _require_iso_date(self.from_date, "from_date"))
        object.__setattr__(self, "to_date", _require_iso_date(self.to_date, "to_date"))
        object.__setattr__(
            self,
            "total_outstanding_cents",
            _require_cents(self.total_outstanding_cents, "total_outstanding_cents"),
        )

    def as_mapping(self) -> dict[str, Any]:
        """Return the projection as a plain mapping (JSON-serialisable)."""
        return {
            "from_date": self.from_date,
            "to_date": self.to_date,
            "include_paid": self.include_paid,
            "count": len(self.entries),
            "entries": [e.as_mapping() for e in self.entries],
            "total_outstanding_cents": self.total_outstanding_cents,
        }