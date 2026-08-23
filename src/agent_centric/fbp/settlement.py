"""Micro-payment / settlement adapter (the paid tier of Network of Experts).

This is the opt-in, external seam that settles the per-run cost of a domain
expert — the "paid" tier that ``CostLedger`` accounts for. It is deliberately
**not** built into the deterministic core: charging real money is an external,
fail-closed provider. The core *accounts* cost (``CostAccount``/``CostLedger``);
the provider *settles* it.

Key rules (deterministic, fail-closed):

- ``SettlementProvider`` is a protocol: ``charge(account, grant) -> Settlement``.
  A provider may be a stub (offline, deterministic) or a real X402-style
  micro-payment service.
- ``SettlementGrant`` is the operator's explicit authorization to charge for a
  domain. **Without a grant, no charge ever happens** — a settlement attempt on
  an ungranted domain fails closed.
- ``Settlement`` carries the outcome: a settlement id, the amount charged, and
  a receipt reference. It lands in the Artifact Vault as the accounting evidence.
- ``settle_run`` is the entry point: given a ``CostAccount``, a grant, and a
  provider, it produces a verified settlement (or fails closed). It never
  bypasses the operator's grant and never auto-charges.
- The module is pure and offline by default; a real provider is opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .experts import CostAccount


class SettlementError(ValueError):
    """A settlement input violated its contract (fail-closed)."""


@dataclass(frozen=True)
class SettlementGrant:
    """The operator's explicit authorization to settle one run's cost.

    Attributes:
        domain: The domain id this grant authorizes.
        max_amount: An upper bound on the amount that may be charged (the
            operator's cap; a charge above it fails closed).
        note: An optional operator note (e.g. budget line / reason).
    """

    domain: str
    max_amount: int
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "max_amount": self.max_amount,
            "note": self.note,
        }


@dataclass(frozen=True)
class Settlement:
    """The recorded outcome of a settled run.

    Attributes:
        domain: The domain id that was settled.
        amount: The amount charged (in the caller's chosen unit).
        settlement_id: A provider-issued settlement id (the evidence).
        status: The settlement status (``settled`` / ``declined`` / ``refunded``).
        via: An optional source reference (e.g. the provider id).
    """

    domain: str
    amount: int
    settlement_id: str
    status: str = "settled"
    via: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "amount": self.amount,
            "settlement_id": self.settlement_id,
            "status": self.status,
            "via": self.via,
        }


class SettlementProvider(Protocol):
    """The external settlement seam: a charge authorized by an explicit grant.

    A provider is opt-in and fail-closed. A stub provider (the default) is
    offline and deterministic; a real provider calls an external micro-payment
    service. ``charge`` raises ``SettlementError`` on any failure.
    """

    def charge(self, account: CostAccount, grant: SettlementGrant) -> Settlement:
        """Settle ``account``'s cost under ``grant`` (never auto-charge)."""
        ...


class StubSettlementProvider:
    """An offline, deterministic settlement provider (tests / no-external-deps).

    It does not charge anything real; it returns a deterministic ``Settlement``
    carrying the amount and a fixed settlement id, so the contract is exercised
    without any external dependency. This is the default and CI-safe path.
    """

    def charge(self, account: CostAccount, grant: SettlementGrant) -> Settlement:
        if account.cost > grant.max_amount:
            raise SettlementError(
                f"settlement for domain {account.domain!r} exceeds grant "
                f"(cost {account.cost} > max {grant.max_amount})"
            )
        return Settlement(
            domain=account.domain,
            amount=account.cost,
            settlement_id=f"stub-settle:{account.domain}:{account.cost}",
            status="settled",
            via="stub",
        )


def settle_run(
    account: CostAccount,
    grant: SettlementGrant,
    provider: SettlementProvider | None = None,
) -> Settlement:
    """Settle ``account``'s cost under ``grant`` via ``provider`` (fail-closed).

    This is the entry point of the paid tier. It is deterministic and
    fail-closed: a mismatched domain, a cost exceeding the grant's cap, or a
    provider failure raises ``SettlementError``. **A settlement never happens
    without an explicit grant** — the grant is the operator's authorization.
    A real provider is opt-in; the default stub is offline and CI-safe.
    """
    if account.domain != grant.domain:
        raise SettlementError(
            f"settlement domain mismatch: account {account.domain!r} vs "
            f"grant {grant.domain!r}"
        )
    if account.cost < 0:
        raise SettlementError(f"settlement for {account.domain!r} has a negative cost")
    if account.cost > grant.max_amount:
        raise SettlementError(
            f"settlement for {account.domain!r} exceeds the grant: "
            f"cost {account.cost} > max {grant.max_amount}"
        )
    provider = provider or StubSettlementProvider()
    try:
        return provider.charge(account, grant)
    except SettlementError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        raise SettlementError(f"settlement failed: {exc}") from exc