"""Deterministic Information-Packet flow over bounded connections (SPEC-0009).

The first-class execution engine for port-declared ``network.v1`` documents. It
runs components in the deterministic topological schedule and moves **Information
Packets** along **bounded connections**:

- an **outport** of a component's verified output is delivered to the **inport**
  of its consumers;
- a connection is a **bounded buffer**: a send to a full buffer is
  **back-pressure**, and a receive from an empty buffer is a deterministic
  **deadlock** — both are explicit, fail-closed outcomes, never a hang;
- an inport with no incoming wire takes its value from a first-class, per-port
  **IIP** bound in the network document, or from the component's ``args``
  template as a fallback.

Determinism: the schedule is fixed by the document, packets are FIFO, and there is
no wall-clock concurrency, so identical input yields an identical trajectory.
Repeated activation / streaming — the regime where back-pressure suspends a
producer — is a later slice; the bounded-connection primitive is in place and
enforced here.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts.handoff import value_matches_type
from .network import (
    _DEFAULT_STEP_LIMIT,
    MAX_CONNECTION_CAPACITY,
    ComponentNetwork,
    NetworkError,
)


class FlowError(NetworkError):
    """Information-Packet flow failed (fail-closed: overflow, deadlock, wiring)."""


def effective_port_type(source_type: str, target_type: str) -> str:
    """The concrete type a connection enforces: the specific side of ``"any"``."""
    return target_type if source_type == "any" else source_type


def check_ip(
    value: Any, source_type: str, target_type: str, where: str
) -> None:
    """Fail closed if an IP violates the connection's declared port type."""
    required = effective_port_type(source_type, target_type)
    if not value_matches_type(value, required):
        raise FlowError(
            f"{where}: value does not match declared port type {required!r} "
            "(fail-closed)"
        )


@dataclass(frozen=True)
class InformationPacket:
    """A value travelling between an outport and an inport (an FBP IP)."""

    port: str
    value: Any


class BoundedConnection:
    """A bounded buffer between one source outport and one target inport.

    FIFO, no concurrency. ``send`` on a full buffer raises (back-pressure);
    ``receive`` on an empty buffer raises (deadlock). Neither ever blocks.
    """

    def __init__(
        self,
        source: str,
        source_port: str,
        target: str,
        target_port: str,
        capacity: int,
    ) -> None:
        if not 1 <= capacity <= MAX_CONNECTION_CAPACITY:
            raise FlowError(
                f"connection {source}.{source_port} -> {target}.{target_port}: "
                f"capacity {capacity} must be within 1..{MAX_CONNECTION_CAPACITY}"
            )
        self.source = source
        self.source_port = source_port
        self.target = target
        self.target_port = target_port
        self.capacity = capacity
        self._buffer: deque[Any] = deque()

    def __len__(self) -> int:
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        return len(self._buffer) >= self.capacity

    @property
    def is_empty(self) -> bool:
        return not self._buffer

    def send(self, value: Any) -> None:
        """Enqueue an IP; raise :class:`FlowError` when the buffer is full."""
        if self.is_full:
            raise FlowError(
                f"connection {self.source}.{self.source_port} -> "
                f"{self.target}.{self.target_port} is full "
                f"(capacity {self.capacity}); back-pressure"
            )
        self._buffer.append(value)

    def receive(self) -> Any:
        """Dequeue the oldest IP; raise :class:`FlowError` when empty."""
        if self.is_empty:
            raise FlowError(
                f"connection {self.source}.{self.source_port} -> "
                f"{self.target}.{self.target_port} is empty; "
                f"inport {self.target_port!r} has no IP"
            )
        return self._buffer.popleft()


def _extract_output(output: Any, port: str, out_ports: tuple[str, ...], cid: str) -> Any:
    """Select an outport's value from a component's verified output (fail-closed)."""
    if isinstance(output, Mapping):
        if port not in output:
            raise FlowError(
                f"component {cid}: output has no field {port!r} for out-port"
            )
        return output[port]
    if len(out_ports) == 1:
        return output
    raise FlowError(f"component {cid}: output is not a mapping for out-port {port!r}")


def run_flow(
    driver: Any, network: ComponentNetwork, *, step_limit: int | None = None
) -> dict[str, Any]:
    """Execute a port-declared network as deterministic Information-Packet flow.

    Each component runs once, in topological order. Its inports are filled from
    incoming connections; an unwired inport takes its first-class **IIP** (or its
    ``args`` template as a fallback). Its verified output is routed to
    consumers' inports over bounded connections. Any overflow, deadlock, missing
    outport field, or unverified step fails closed with ``ok=False`` — never a
    partial or silent success.

    Returns the same shape as ``run_network``:
    ``{"ok", "results", "completed", "failed"?}`` plus ``"error"`` on failure.
    """
    try:
        network.validate()
        order = network.topological_order()
    except NetworkError as exc:
        return {"ok": False, "results": [], "completed": 0, "error": str(exc)}

    limit = step_limit if step_limit is not None else _DEFAULT_STEP_LIMIT
    if limit <= 0:
        return {
            "ok": False, "results": [], "completed": 0,
            "error": f"step_limit must be positive, got {limit}",
        }
    if len(order) > limit:
        return {
            "ok": False, "results": [], "completed": 0,
            "error": (
                f"network has {len(order)} components, exceeding the "
                f"{limit}-component step limit (fail-closed)"
            ),
        }

    components = {component.id: component for component in network.components()}
    # Port mode requires every edge endpoint to declare the port it uses.
    for edge in network.edges():
        src_ports = components[edge.source].ports
        dst_ports = components[edge.target].ports
        if edge.source_field not in src_ports.get("out", ()):
            return {
                "ok": False, "results": [], "completed": 0,
                "error": (
                    f"edge {edge.source}.{edge.source_field} -> "
                    f"{edge.target}.{edge.target_arg}: source {edge.source!r} does "
                    f"not declare out-port {edge.source_field!r} (port mode)"
                ),
            }
        if edge.target_arg not in dst_ports.get("in", ()):
            return {
                "ok": False, "results": [], "completed": 0,
                "error": (
                    f"edge {edge.source}.{edge.source_field} -> "
                    f"{edge.target}.{edge.target_arg}: target {edge.target!r} does "
                    f"not declare in-port {edge.target_arg!r} (port mode)"
                ),
            }

    incoming: dict[str, list[BoundedConnection]] = {}
    outgoing: dict[str, list[BoundedConnection]] = {}
    for edge in network.edges():
        connection = BoundedConnection(
            edge.source, edge.source_field, edge.target, edge.target_arg, edge.capacity
        )
        incoming.setdefault(edge.target, []).append(connection)
        outgoing.setdefault(edge.source, []).append(connection)

    # First-class per-port IIPs (SPEC-0009): an explicit value bound to an inport.
    # Validation forbids an IIP on a port that also has an incoming connection.
    iips_by_component: dict[str, dict[str, Any]] = {}
    for iip in network.iips():
        iips_by_component.setdefault(iip.component, {})[iip.port] = iip.value

    results: list[dict[str, Any]] = []
    for idx, cid in enumerate(order):
        component = components[cid]
        args = dict(component.args)  # template
        args.update(iips_by_component.get(cid, {}))  # explicit per-port IIPs
        for connection in sorted(incoming.get(cid, []), key=lambda c: c.target_port):
            if connection.is_empty:
                return {
                    "ok": False, "results": results, "completed": idx,
                    "error": (
                        f"component {cid}: inport {connection.target_port!r} has no "
                        f"IP (deadlock; fail-closed)"
                    ),
                }
            args[connection.target_port] = connection.receive()
        try:
            response = driver.run(
                component.task, args,
                verifier=component.verifier, child=component.child,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            return {
                "ok": False, "results": results, "completed": idx,
                "error": f"component {cid} ({component.task}) raised: {exc}",
            }
        result = {
            "step": idx, "id": cid, "task": component.task,
            "verified": response.verified, "value": response.value,
            "error": response.error,
        }
        results.append(result)
        if not response.verified:
            return {"ok": False, "results": results, "completed": idx, "failed": result}

        out_ports = component.ports.get("out", ())
        for connection in sorted(outgoing.get(cid, []), key=lambda c: c.source_port):
            try:
                value = _extract_output(response.value, connection.source_port, out_ports, cid)
                source_type = component.port_types.get("out", {}).get(
                    connection.source_port, "any"
                )
                target_type = components[connection.target].port_types.get(
                    "in", {}
                ).get(connection.target_port, "any")
                check_ip(
                    value, source_type, target_type,
                    f"connection {cid}.{connection.source_port} -> "
                    f"{connection.target}.{connection.target_port}",
                )
                connection.send(value)
            except FlowError as exc:
                return {
                    "ok": False, "results": results, "completed": idx,
                    "error": str(exc),
                }
    return {"ok": True, "results": results, "completed": len(results), "failed": None}
