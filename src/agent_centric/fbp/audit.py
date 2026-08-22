"""Read-only tree-audit reconstruction: audit as computational proof.

This is the observer that turns the chain-audit machinery into something you
can *prove* with. Each agent records its local activity (configure, run
outcomes, state ops) into its own trajectory store, and each parent records a
``relay`` hop when it accepts a child's verified response. This module
reconstructs the **full causal chain per correlation id** from those stores —
the directive's path down the tree and the verified result (or audited
failure) that bubbled up.

It is a pure, deterministic, read-only **capability** (not an agent — same
category as CPM): it never mutates anything and needs no state grant. Given the
tree's trajectory stores (a mapping of node identity -> TrajectoryStore), it
returns, for each correlation id, the ordered chain of audit events and whether
the whole chain is consistent (every hop verified, no gaps).

Determinism: events are ordered by correlation id then by the chain order
reconstructed from ``parent`` links; identical stores yield identical output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .store import TrajectoryStore


@dataclass(frozen=True)
class ChainEvent:
    """One hop in a reconstructed audit chain.

    Attributes:
        node: The agent that recorded this event.
        kind: The event kind (result, error, relay, ok, ...).
        verified: Whether this hop passed verification.
        value: The (verified) value, if any.
        error: The explicit failure message, if any.
        parent: The node this event was relayed from (for relay hops).
    """

    node: str
    kind: str
    verified: bool
    value: Any = None
    error: str | None = None
    parent: str = ""


@dataclass(frozen=True)
class AuditChain:
    """A reconstructed causal chain for one correlation id.

    Attributes:
        correlation_id: The directive's correlation id.
        events: The ordered chain of audit events (down the tree, then up).
        verified: True if every hop is verified (a fully consistent chain).
        terminal: The final outcome kind (result / error / ok).
        terminal_value: The final verified value, if any.
        terminal_error: The final failure message, if any.
    """

    correlation_id: str
    events: tuple[ChainEvent, ...]
    verified: bool
    terminal: str
    terminal_value: Any = None
    terminal_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready rendering (for the wire)."""
        return {
            "correlation_id": self.correlation_id,
            "verified": self.verified,
            "terminal": self.terminal,
            "terminal_value": self.terminal_value,
            "terminal_error": self.terminal_error,
            "events": [
                {
                    "node": e.node,
                    "kind": e.kind,
                    "verified": e.verified,
                    "value": e.value,
                    "error": e.error,
                    "parent": e.parent,
                }
                for e in self.events
            ],
        }


def reconstruct_chains(
    stores: dict[str, TrajectoryStore],
) -> tuple[AuditChain, ...]:
    """Reconstruct the full audit chain per correlation id from the tree's stores.

    Args:
        stores: Mapping of node identity -> that node's trajectory store.

    Returns:
        A tuple of ``AuditChain`` (one per correlation id), ordered by
        correlation id. Deterministic and read-only.
    """
    # Collect every event, tagged with the node that recorded it.
    events_by_cid: dict[str, list[ChainEvent]] = {}
    for _node, store in stores.items():
        for row in store.all():
            cid = row["correlation_id"]
            events_by_cid.setdefault(cid, []).append(
                ChainEvent(
                    node=row["node"],
                    kind=row["kind"],
                    verified=row["verified"],
                    value=row["value"],
                    error=row["error"],
                    parent=row["parent"],
                )
            )

    chains: list[AuditChain] = []
    for cid in sorted(events_by_cid):
        events = events_by_cid[cid]
        # Order deterministically: the chain is the sequence of events linked by
        # parent pointers (a relay's parent names the child that produced it).
        ordered = _order_chain(events)
        verified = all(e.verified for e in ordered)
        # Terminal = the last event in the chain (the final outcome).
        terminal = ordered[-1] if ordered else None
        chains.append(
            AuditChain(
                correlation_id=cid,
                events=tuple(ordered),
                verified=verified,
                terminal=terminal.kind if terminal else "unknown",
                terminal_value=terminal.value if terminal else None,
                terminal_error=terminal.error if terminal else None,
            )
        )
    return tuple(chains)


def _order_chain(events: list[ChainEvent]) -> list[ChainEvent]:
    """Order a correlation id's events into a deterministic chain.

    The chain is: the local record(s) of the directive's execution, followed by
    the relay hops as the verified response bubbles up. We order by: first the
    non-relay events (the origin), then relay events ordered by their ``parent``
    depth. Deterministic because node ids are stable.
    """
    non_relay = [e for e in events if e.kind != "relay"]
    relays = [e for e in events if e.kind == "relay"]
    # Sort relays by the child they came from (stable, deterministic).
    relays_sorted = sorted(relays, key=lambda e: e.parent)
    return non_relay + relays_sorted