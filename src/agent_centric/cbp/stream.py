"""Repeated-activation streaming over bounded connections (SPEC-0009 slice 3).

The single-shot engine (``cbp/flow.py``) activates each component exactly once.
This engine adds **repeated activation**: a component may declare an out-port as
a **stream**, whose verified output is an iterable; every element becomes its own
Information Packet. A component whose wired in-ports have data is **activated**
again — once per input packet — so a downstream process can consume an unbounded
(or long) stream.

**Back-pressure suspends the producer, deterministically.** A component that
sends to a full :class:`BoundedConnection` is *suspended* mid-stream (its emitter
keeps the unsent packet); the scheduler then activates a ready consumer to drain
the buffer, and resumes the producer when space frees. There are no threads and
no wall-clock: the schedule is a fixed function of the document, so identical
input yields an identical trajectory.

**Liveness is enforced, never assumed.** The scheduler distinguishes three
outcomes:

- **success** — no component is ready, no producer is suspended, and every
  connection is empty;
- **deadlock** — no component is ready and every suspended producer is blocked
  (or stranded packets remain), which is an explicit, audited failure — never a
  hang;
- **budget exhaustion** — activation or send limits are exceeded, which is a
  fail-closed error (a runaway source can never drive unbounded work).

Only networks that declare a ``stream`` out-port are routed here; the single-shot
path is unchanged.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from typing import Any

from .flow import BoundedConnection, FlowError, _extract_output
from .network import (
    _DEFAULT_STEP_LIMIT,
    MAX_CONNECTION_CAPACITY,
    Component,
    ComponentNetwork,
    NetworkError,
)


class _Emitter:
    """Drains one activation's output to bounded connections, suspendably.

    Each stream is ``(connections, iterator)``: every element yielded by the
    iterator is sent to **all** of that stream's connections (FBP fan-out) before
    the next element is pulled. A full connection suspends the emitter with the
    in-flight element retained; :meth:`step` never loses or duplicates a packet.
    """

    def __init__(
        self, streams: list[tuple[list[BoundedConnection], Iterator[Any]]]
    ) -> None:
        self._streams: deque[tuple[list[BoundedConnection], Iterator[Any]]] = deque(
            streams
        )
        self._pending: tuple[Any, deque[BoundedConnection]] | None = None

    @property
    def is_empty(self) -> bool:
        return not self._streams and self._pending is None

    def step(self) -> str:
        """Push work forward by one packet.

        Returns ``"progress"`` (a packet was sent), ``"blocked"`` (a connection is
        full — back-pressure), or ``"done"`` (nothing left to send).
        """
        while True:
            if self._pending is not None:
                value, pending = self._pending
                while pending:
                    connection = pending[0]
                    if connection.is_full:
                        return "blocked"
                    connection.send(value)
                    pending.popleft()
                self._pending = None
                return "progress"
            if not self._streams:
                return "done"
            connections, iterator = self._streams[0]
            try:
                value = next(iterator)
            except StopIteration:
                self._streams.popleft()
                continue
            self._pending = (value, deque(connections))


def _build_emitter(
    cid: str,
    output: Any,
    component: Component,
    outgoing: dict[str, dict[str, list[BoundedConnection]]],
) -> _Emitter:
    """Build the emitter for one activation's verified output (fail-closed)."""
    stream_ports = set(component.stream)
    streams: list[tuple[list[BoundedConnection], Iterator[Any]]] = []
    for port in sorted(outgoing.get(cid, {})):
        connections = sorted(
            outgoing[cid][port], key=lambda c: (c.target, c.target_port)
        )
        value = _extract_output(output, port, component.ports.get("out", ()), cid)
        if port in stream_ports:
            try:
                iterator: Iterator[Any] = iter(value)
            except TypeError as exc:
                raise FlowError(
                    f"component {cid}: streaming out-port {port!r} value is not "
                    f"iterable: {value!r}"
                ) from exc
        else:
            iterator = iter((value,))
        streams.append((connections, iterator))
    return _Emitter(streams)


def run_stream(
    driver: Any, network: ComponentNetwork, *, step_limit: int | None = None
) -> dict[str, Any]:
    """Execute a streaming network as deterministic activation-based dataflow.

    ``step_limit`` bounds **activations** (default the module ceiling); a separate
    send budget (activations × max connection capacity) bounds emitted packets, so
    a runaway source fails closed rather than looping forever.

    Returns the same shape as ``run_network``: ``{"ok", "results", "completed"}``
    plus ``"failed"`` / ``"error"`` on failure. ``results`` records every
    activation, in order.
    """
    results: list[dict[str, Any]] = []

    def fail(message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "results": results,
            "completed": len(results),
            "error": message,
        }

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
    activation_limit = limit
    send_limit = limit * MAX_CONNECTION_CAPACITY

    components = {component.id: component for component in network.components()}
    for edge in network.edges():
        if edge.source_field not in components[edge.source].ports.get("out", ()):
            return fail(
                f"edge {edge.source}.{edge.source_field} -> {edge.target}."
                f"{edge.target_arg}: source {edge.source!r} declares no out-port "
                f"{edge.source_field!r} (streaming mode)"
            )
        if edge.target_arg not in components[edge.target].ports.get("in", ()):
            return fail(
                f"edge {edge.source}.{edge.source_field} -> {edge.target}."
                f"{edge.target_arg}: target {edge.target!r} declares no in-port "
                f"{edge.target_arg!r} (streaming mode)"
            )

    iips_by_component: dict[str, dict[str, Any]] = {}
    for iip in network.iips():
        iips_by_component.setdefault(iip.component, {})[iip.port] = iip.value

    incoming: dict[str, dict[str, BoundedConnection]] = {}
    outgoing: dict[str, dict[str, list[BoundedConnection]]] = {}
    all_connections: list[BoundedConnection] = []
    for edge in sorted(
        network.edges(),
        key=lambda e: (e.source, e.source_field, e.target, e.target_arg),
    ):
        connection = BoundedConnection(
            edge.source, edge.source_field, edge.target, edge.target_arg, edge.capacity
        )
        target_ports = incoming.setdefault(edge.target, {})
        if edge.target_arg in target_ports:
            return fail(
                f"inport {edge.target}.{edge.target_arg} has more than one incoming "
                "connection (fail-closed)"
            )
        target_ports[edge.target_arg] = connection
        outgoing.setdefault(edge.source, {}).setdefault(edge.source_field, []).append(
            connection
        )
        all_connections.append(connection)

    topo_index = {cid: index for index, cid in enumerate(order)}
    spent_sources: set[str] = set()
    emitters: dict[str, _Emitter] = {}
    activations = 0
    sends = 0

    def ports_ready(cid: str) -> bool:
        return all(
            not connection.is_empty for connection in incoming.get(cid, {}).values()
        )

    while True:
        ready = [
            cid
            for cid in order
            if cid not in emitters
            and (
                (not incoming.get(cid) and cid not in spent_sources)
                or (bool(incoming.get(cid)) and ports_ready(cid))
            )
        ]

        if ready:
            cid = ready[0]
            component = components[cid]
            args = dict(component.args)
            args.update(iips_by_component.get(cid, {}))
            wired = incoming.get(cid) or {}
            if wired:
                for port in sorted(wired):
                    args[port] = wired[port].receive()
            else:
                spent_sources.add(cid)
            activations += 1
            if activations > activation_limit:
                return fail(
                    f"activation limit {activation_limit} exceeded (fail-closed)"
                )
            try:
                response = driver.run(
                    component.task, args,
                    verifier=component.verifier, child=component.child,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the operator
                return fail(f"component {cid} ({component.task}) raised: {exc}")
            result = {
                "step": len(results), "id": cid, "task": component.task,
                "verified": response.verified, "value": response.value,
                "error": response.error,
            }
            results.append(result)
            if not response.verified:
                return {
                    "ok": False, "results": results, "completed": len(results),
                    "failed": result,
                }
            try:
                emitter = _build_emitter(cid, response.value, component, outgoing)
            except FlowError as exc:
                return fail(str(exc))
            if not emitter.is_empty:
                emitters[cid] = emitter
            continue

        if not emitters:
            stranded = [
                connection for connection in all_connections if not connection.is_empty
            ]
            if stranded:
                return fail(
                    "deadlock: no component is ready and packets remain undelivered "
                    "(fail-closed)"
                )
            return {
                "ok": True, "results": results, "completed": len(results),
                "failed": None,
            }

        stepped = False
        for cid in sorted(emitters, key=lambda c: (topo_index[c], c)):
            status = emitters[cid].step()
            if status == "blocked":
                continue
            if status == "done":
                del emitters[cid]
            else:
                sends += 1
                if sends > send_limit:
                    return fail(f"send limit {send_limit} exceeded (fail-closed)")
            stepped = True
            break
        if not stepped:
            return fail(
                "deadlock: every producer is blocked and no component is ready "
                "(fail-closed)"
            )
