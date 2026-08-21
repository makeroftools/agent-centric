"""Accounting Bills v1 contracts (versioned).

This module defines the structured input/output contracts for the bills
specialty agent (Volley 022). The design is deliberately narrow and
deterministic:

- ``BillLine`` — a single, validated line item with a positive quantity and a
  non-negative unit price.
- ``Bill`` — an immutable collection of lines plus an optional discount basis
  points (bps) and an optional tax basis points (bps).
- ``BillTotal`` — the deterministic, recomputed totals: line subtotal, discount,
  taxable amount, tax, and grand total, all in integer minor units (cents) so
  money math is exact.

All amounts are integer minor units (cents) to avoid floating-point error in
money math. Every contract validates its inputs at construction time and
rejects bad or missing data (fail-closed), so a malformed bill can never reach
the agent or the verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BillVersion(StrEnum):
    """Version of the accounting bills contract."""

    V1 = "bill.v1"


def _require_int(value: Any, name: str) -> int:
    """Coerce ``value`` to a non-negative int, rejecting bad/missing data.

    Raises:
        ValueError: If ``value`` is not an int (or bool) or is negative.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}.")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}.")
    return value


def _require_positive_int(value: Any, name: str) -> int:
    """Coerce ``value`` to a strictly positive int, rejecting bad/missing data.

    Raises:
        ValueError: If ``value`` is not an int (or bool) or is not positive.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}.")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}.")
    return value


def _require_bps(value: Any, name: str) -> int:
    """Coerce ``value`` to a basis-points int in ``[0, 10000]`` (0%..100%).

    Raises:
        ValueError: If ``value`` is not an int or is outside the valid range.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}.")
    if value < 0 or value > 10000:
        raise ValueError(f"{name} must be between 0 and 10000 (0%..100%), got {value}.")
    return value


@dataclass(frozen=True)
class BillLine:
    """A single validated bill line item.

    Attributes:
        description: Human-readable line description. Must be non-empty.
        quantity: Positive integer quantity.
        unit_price_cents: Non-negative integer unit price in minor units (cents).
    """

    description: str
    quantity: int
    unit_price_cents: int

    def __post_init__(self) -> None:
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("BillLine description must be a non-empty string.")
        object.__setattr__(self, "quantity", _require_positive_int(self.quantity, "quantity"))
        object.__setattr__(
            self, "unit_price_cents", _require_int(self.unit_price_cents, "unit_price_cents")
        )

    @classmethod
    def from_mapping(cls, data: Any) -> BillLine:
        """Build a ``BillLine`` from a mapping, rejecting bad/missing data.

        Raises:
            ValueError: If ``data`` is not a mapping or any field is bad/missing.
        """
        if not isinstance(data, dict):
            raise ValueError(f"Bill line must be a mapping, got {data!r}.")
        description = data.get("description", "")
        if not isinstance(description, str) or not description:
            raise ValueError("BillLine description must be a non-empty string.")
        quantity = _require_positive_int(data.get("quantity"), "quantity")
        unit_price_cents = _require_int(data.get("unit_price_cents"), "unit_price_cents")
        return cls(
            description=description,
            quantity=quantity,
            unit_price_cents=unit_price_cents,
        )


@dataclass(frozen=True)
class Bill:
    """An immutable, validated bill.

    Attributes:
        lines: The ordered line items. Must be non-empty.
        discount_bps: Optional discount in basis points (0..10000). Defaults to 0.
        tax_bps: Optional tax in basis points (0..10000). Defaults to 0.
    """

    lines: tuple[BillLine, ...]
    discount_bps: int = 0
    tax_bps: int = 0

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValueError("Bill must contain at least one line.")
        object.__setattr__(self, "discount_bps", _require_bps(self.discount_bps, "discount_bps"))
        object.__setattr__(self, "tax_bps", _require_bps(self.tax_bps, "tax_bps"))

    @classmethod
    def from_mapping(cls, data: Any) -> Bill:
        """Build a ``Bill`` from a mapping, rejecting bad/missing data.

        Raises:
            ValueError: If ``data`` is not a mapping, ``lines`` is missing/empty,
                or any line or rate is bad/missing.
        """
        if not isinstance(data, dict):
            raise ValueError(f"Bill must be a mapping, got {data!r}.")
        raw_lines = data.get("lines")
        if not isinstance(raw_lines, (list, tuple)) or not raw_lines:
            raise ValueError("Bill 'lines' must be a non-empty list.")
        lines = tuple(BillLine.from_mapping(line) for line in raw_lines)
        return cls(
            lines=lines,
            discount_bps=data.get("discount_bps", 0),
            tax_bps=data.get("tax_bps", 0),
        )

    def as_mapping(self) -> dict[str, Any]:
        """Return the bill as a plain mapping (JSON-serialisable)."""
        return {
            "lines": [
                {
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price_cents": line.unit_price_cents,
                }
                for line in self.lines
            ],
            "discount_bps": self.discount_bps,
            "tax_bps": self.tax_bps,
        }


@dataclass(frozen=True)
class BillTotal:
    """The deterministic, recomputed totals for a bill.

    All amounts are integer minor units (cents). ``grand_total_cents`` is the
    final amount an invoice would carry.

    Attributes:
        line_subtotal_cents: Sum of ``quantity * unit_price_cents`` over all lines.
        discount_cents: ``round(line_subtotal_cents * discount_bps / 10000)``.
        taxable_amount_cents: ``line_subtotal_cents - discount_cents``.
        tax_cents: ``round(taxable_amount_cents * tax_bps / 10000)``.
        grand_total_cents: ``taxable_amount_cents + tax_cents``.
    """

    line_subtotal_cents: int
    discount_cents: int
    taxable_amount_cents: int
    tax_cents: int
    grand_total_cents: int

    def __post_init__(self) -> None:
        for name in (
            "line_subtotal_cents",
            "discount_cents",
            "taxable_amount_cents",
            "tax_cents",
            "grand_total_cents",
        ):
            object.__setattr__(self, name, _require_int(getattr(self, name), name))

    @classmethod
    def compute(cls, bill: Bill) -> BillTotal:
        """Recompute the totals for ``bill`` deterministically.

        This is the single source of truth for money math. Both the agent and
        the verifier call it, so a verified result is by construction the
        recomputed total.
        """
        line_subtotal = sum(line.quantity * line.unit_price_cents for line in bill.lines)
        discount = _round_half_up(line_subtotal * bill.discount_bps, 10000)
        taxable = line_subtotal - discount
        tax = _round_half_up(taxable * bill.tax_bps, 10000)
        return cls(
            line_subtotal_cents=line_subtotal,
            discount_cents=discount,
            taxable_amount_cents=taxable,
            tax_cents=tax,
            grand_total_cents=taxable + tax,
        )

    def as_mapping(self) -> dict[str, int]:
        """Return the totals as a plain mapping (JSON-serialisable)."""
        return {
            "line_subtotal_cents": self.line_subtotal_cents,
            "discount_cents": self.discount_cents,
            "taxable_amount_cents": self.taxable_amount_cents,
            "tax_cents": self.tax_cents,
            "grand_total_cents": self.grand_total_cents,
        }


def _round_half_up(numerator: int, denominator: int) -> int:
    """Round ``numerator / denominator`` half-up to the nearest integer.

    Money rounding is deterministic and explicit: exact halves round away from
    zero (up for the non-negative amounts used here). This is the only rounding
    rule in the bills contract, so both the agent and the verifier agree.
    """
    return (numerator + denominator // 2) // denominator