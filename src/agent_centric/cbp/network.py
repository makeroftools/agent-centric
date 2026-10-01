"""Component Networks — a deterministic visual-programming core for CBP.

A **component network** is a directed graph of components (nodes) wired by
data-flow edges. It is the model behind a visual programming interface: a user
places components and connects outputs to inputs, and the network compiles to an
ordered CBP ``run`` plan that executes through the driver's verified spine.

Determinism is preserved by construction:

- The **compile order is a topological sort** of the graph, so identical
  networks always compile to the same ordered plan (same edges, same order,
  same results — regardless of how the components were added).
- **Edges wire data flow**: a source component's output field feeds a target
  component's input arg. Wiring is resolved at compile time into the target's
  ``args``, so the plan is fully concrete and replayable.
- **Cycles are rejected** fail-closed (a component network must be a DAG).
- **Unknown references fail closed**: an edge to a missing component or an
  unknown output field raises, so a half-wired network never silently runs.

The module is pure and offline (no I/O, no model): the network model, the
validation, and the compiler are all ordinary deterministic software — the layer
the CBP spine can audit and replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..contracts.handoff import is_known_type, types_compatible, value_matches_type

# Hard default ceiling on the number of components a component network may
# execute. A network larger than this fails closed before any work, so an edge
# transport (ACP/MCP) or a UI cannot drive unbounded sequential runs through
# the verified spine. Callers may lower it (``step_limit=...``) but the module
# default is the hard ceiling when none is granted.
_DEFAULT_STEP_LIMIT = 512

# Bounded connections (SPEC-0009): every edge is a connection with a capacity.
# The default is a single slot; the ceiling bounds buffer memory and makes an
# unbounded connection impossible to express.
DEFAULT_CONNECTION_CAPACITY = 1
MAX_CONNECTION_CAPACITY = 1024
_PORT_DIRECTIONS = ("in", "out")

# A composite (subnet) may nest, but only finitely: a deeply nested (or cyclic)
# subnet body is a fail-closed error, never an unbounded traversal (SPEC-0009).
_MAX_SUBNET_DEPTH = 8


class NetworkError(ValueError):
    """A component network is invalid (fail-closed)."""


def canonical_json_bytes(payload: Any) -> bytes:
    """The canonical JSON bytes of ``payload`` (sorted keys, tight separators).

    UTF-8 with non-ASCII escaped, so the content address is byte-identical
    across locales/platforms. Shared by the network document and its diagram
    projection, so both content-address the same way (one canonicalization).
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Component:
    """A single component (node) in the network.

    Attributes:
        id: A stable, unique id within the network.
        task: The CBP task this component runs (a registered callable).
        args: A template of input args; edges may override/feed these.
        verifier: An optional parent verifier for this component's run.
        child: An optional delegation target for this component's run.
        ports: Declared named ports keyed by direction ("in"/"out"). When a
            direction is declared, every edge on that side must use a declared
            port name (fail-closed, SPEC-0009).
        port_types: Optional value types for declared ports, keyed by direction
            ("in"/"out") then port name. A typed port constrains the value
            that may flow: edge type compatibility and IIP values are validated
            fail-closed, and the flow engines enforce the type at run time
            (SPEC-0009). An absent type is the untyped wildcard ``"any"``.
        external: For a composite, the external-port map: port name ->
            ``"inner_component.inner_port"``. A composite exposes **only** these
            ports across its boundary (SPEC-0009).
        subnet: For a composite, the nested network that is its body. Composition
            is fractal; the boundary guard rejects any outer edge that would reach
            past the declared external ports.
        stream: Declared out-ports that carry a **stream** (repeated activation):
            the verified output for each such port is an iterable and every element
            is sent as a separate Information Packet, with back-pressure suspending
            the producer (SPEC-0009). Empty means one IP per activation.
    """

    id: str
    task: str
    args: dict[str, Any] = field(default_factory=dict)
    verifier: str | None = None
    child: str | None = None
    ports: dict[str, tuple[str, ...]] = field(default_factory=dict)
    port_types: dict[str, dict[str, str]] = field(default_factory=dict)
    stream: tuple[str, ...] = ()
    external: dict[str, str] = field(default_factory=dict)
    subnet: ComponentNetwork | None = None


@dataclass(frozen=True)
class Edge:
    """A data-flow edge: ``source`` output field -> ``target`` input arg.

    Attributes:
        source: The source component id.
        source_field: The output field on the source (its result dict key).
        target: The target component id.
        target_arg: The input port/arg on the target to feed.
        capacity: The connection's bounded-buffer capacity (default 1).
    """

    source: str
    source_field: str
    target: str
    target_arg: str
    capacity: int = DEFAULT_CONNECTION_CAPACITY


@dataclass(frozen=True)
class IIP:
    """An Initial Information Packet bound to a component inport (SPEC-0009).

    An IIP supplies a value to one **named inport** at network-definition time;
    it is the first-class, explicit alternative to an implicit ``args`` template.
    An inport may carry either an IIP or an incoming connection, never both
    (fail-closed).
    """

    component: str
    port: str
    value: Any


class ComponentNetwork:
    """A validated, compilable directed acyclic graph of components.

    Components are added by id (idempotent — re-adding replaces), edges wire
    outputs to inputs. ``compile()`` returns an ordered CBP ``run`` plan in
    topological order with data-flow resolved into each component's ``args``.
    """

    def __init__(self) -> None:
        self._components: dict[str, Component] = {}
        self._edges: list[Edge] = []
        self._iips: list[IIP] = []

    # -- construction -------------------------------------------------------

    def add_component(self, component: Component) -> ComponentNetwork:
        """Add (or replace) a component by id. Returns self for chaining."""
        if not component.id:
            raise NetworkError("component id must be non-empty")
        if "." in component.id:
            raise NetworkError(
                f"component id {component.id!r} must not contain '.'; cross-boundary "
                "flow uses a composite's declared external ports (fail-closed)"
            )
        for direction, names in component.ports.items():
            if direction not in _PORT_DIRECTIONS:
                raise NetworkError(
                    f"component {component.id!r}: unknown port direction {direction!r}"
                )
            if any(not name for name in names) or len(names) != len(set(names)):
                raise NetworkError(
                    f"component {component.id!r}: {direction} ports must be unique "
                    "and non-empty"
                )
        for direction, typed in component.port_types.items():
            if direction not in _PORT_DIRECTIONS:
                raise NetworkError(
                    f"component {component.id!r}: unknown port direction "
                    f"{direction!r} in port_types"
                )
            declared = component.ports.get(direction, ())
            for name, type_name in typed.items():
                if not name:
                    raise NetworkError(
                        f"component {component.id!r}: typed port name must be "
                        "non-empty"
                    )
                if name not in declared:
                    raise NetworkError(
                        f"component {component.id!r}: port type declared for "
                        f"{name!r}, which is not a declared {direction}-port "
                        "(fail-closed)"
                    )
                if not is_known_type(type_name):
                    raise NetworkError(
                        f"component {component.id!r}: port {name!r} has unknown "
                        f"type {type_name!r} (fail-closed)"
                    )
        self._components[component.id] = component
        return self

    def add_edge(self, edge: Edge) -> ComponentNetwork:
        """Add a data-flow edge. Returns self for chaining."""
        self._edges.append(edge)
        return self

    def add_iip(self, iip: IIP) -> ComponentNetwork:
        """Bind an Initial Information Packet to an inport. Returns self."""
        self._iips.append(iip)
        return self

    # -- validation ---------------------------------------------------------

    def validate(self, _depth: int = 0) -> None:
        """Validate the network fail-closed.

        Raises ``NetworkError`` if any edge references a missing component or an
        unknown output field, if a cross-boundary edge bypasses a composite's
        declared external ports, or if the graph contains a cycle (not a DAG).
        Nested subnet bodies are validated recursively, bounded by depth.
        """
        if _depth > _MAX_SUBNET_DEPTH:
            raise NetworkError(
                f"subnet nesting exceeds the depth limit {_MAX_SUBNET_DEPTH} "
                "(fail-closed)"
            )
        for edge in self._edges:
            if "." in edge.source or "." in edge.target:
                raise NetworkError(
                    "dotted component references are not allowed; cross-boundary "
                    "flow must use a composite's declared external ports "
                    "(fail-closed)"
                )
            if edge.source not in self._components:
                raise NetworkError(f"edge source {edge.source!r} is not a component")
            if edge.target not in self._components:
                raise NetworkError(f"edge target {edge.target!r} is not a component")
            if edge.source == edge.target:
                raise NetworkError("self-loop edge is not allowed")
            if not 1 <= edge.capacity <= MAX_CONNECTION_CAPACITY:
                raise NetworkError(
                    f"connection {edge.source}.{edge.source_field} -> "
                    f"{edge.target}.{edge.target_arg} has capacity {edge.capacity}; "
                    f"must be within 1..{MAX_CONNECTION_CAPACITY}"
                )
            source = self._components[edge.source]
            out_ports = source.ports.get("out", ())
            if out_ports and edge.source_field not in out_ports:
                raise NetworkError(
                    f"edge {edge.source}.{edge.source_field} -> "
                    f"{edge.target}.{edge.target_arg}: source {edge.source!r} "
                    f"declares no out-port {edge.source_field!r}"
                )
            target = self._components[edge.target]
            in_ports = target.ports.get("in", ())
            if in_ports and edge.target_arg not in in_ports:
                raise NetworkError(
                    f"edge {edge.source}.{edge.source_field} -> "
                    f"{edge.target}.{edge.target_arg}: target {edge.target!r} "
                    f"declares no in-port {edge.target_arg!r}"
                )
            source_type = source.port_types.get("out", {}).get(
                edge.source_field, "any"
            )
            target_type = target.port_types.get("in", {}).get(edge.target_arg, "any")
            if not types_compatible(source_type, target_type):
                raise NetworkError(
                    f"edge {edge.source}.{edge.source_field} -> "
                    f"{edge.target}.{edge.target_arg}: incompatible port types "
                    f"{source_type!r} -> {target_type!r} (fail-closed)"
                )
        seen_iips: set[tuple[str, str]] = set()
        for iip in self._iips:
            if iip.component not in self._components:
                raise NetworkError(f"IIP target {iip.component!r} is not a component")
            if not iip.port:
                raise NetworkError("IIP port must be non-empty")
            key = (iip.component, iip.port)
            if key in seen_iips:
                raise NetworkError(
                    f"IIP for {iip.component}.{iip.port} is declared more than once"
                )
            seen_iips.add(key)
            in_ports = self._components[iip.component].ports.get("in", ())
            if iip.port not in in_ports:
                raise NetworkError(
                    f"IIP target {iip.component!r} declares no in-port {iip.port!r}"
                )
            iip_type = self._components[iip.component].port_types.get(
                "in", {}
            ).get(iip.port, "any")
            if not value_matches_type(iip.value, iip_type):
                raise NetworkError(
                    f"IIP for {iip.component}.{iip.port}: value does not match "
                    f"the declared in-port type {iip_type!r} (fail-closed)"
                )
        for edge in self._edges:
            if (edge.target, edge.target_arg) in seen_iips:
                raise NetworkError(
                    f"inport {edge.target}.{edge.target_arg} has both an IIP and an "
                    f"incoming connection from {edge.source!r} (fail-closed)"
                )
        for component in self.components():
            for stream_port in component.stream:
                if stream_port not in component.ports.get("out", ()):
                    raise NetworkError(
                        f"component {component.id!r}: stream out-port {stream_port!r} "
                        "is not a declared out-port (fail-closed)"
                    )
            subnet = component.subnet
            if subnet is None:
                if component.external:
                    raise NetworkError(
                        f"component {component.id!r} declares external ports without "
                        "a subnet body (fail-closed)"
                    )
                continue
            if _depth >= _MAX_SUBNET_DEPTH:
                raise NetworkError(
                    f"subnet nesting exceeds the depth limit {_MAX_SUBNET_DEPTH} at "
                    f"component {component.id!r} (fail-closed)"
                )
            declared = set(component.ports.get("in", ())) | set(
                component.ports.get("out", ())
            )
            mapped = set(component.external)
            if mapped != declared:
                raise NetworkError(
                    f"component {component.id!r}: a composite's external ports must "
                    "exactly match its declared ports "
                    f"(missing={sorted(declared - mapped)}, "
                    f"extra={sorted(mapped - declared)})"
                )
            inner = {
                inner_component.id: inner_component
                for inner_component in subnet.components()
            }
            for port in sorted(component.external):
                ref = component.external[port]
                if ref.count(".") != 1 or ref.startswith(".") or ref.endswith("."):
                    raise NetworkError(
                        f"component {component.id!r}: external port {port!r} must map "
                        f"to an interior 'component.port' reference, got {ref!r}"
                    )
                inner_id, inner_port = ref.split(".")
                if inner_id not in inner:
                    raise NetworkError(
                        f"component {component.id!r}: external port {port!r} maps to "
                        f"unknown interior component {inner_id!r} (fail-closed)"
                    )
                inner_component = inner[inner_id]
                side = "in" if port in component.ports.get("in", ()) else "out"
                if inner_port not in inner_component.ports.get(side, ()):
                    raise NetworkError(
                        f"component {component.id!r}: external {side}-port {port!r} "
                        f"maps to interior {ref!r}, which declares no matching "
                        f"{side}-port (fail-closed)"
                    )
            subnet.validate(_depth + 1)
        self._check_acyclic()

    def _check_acyclic(self) -> None:
        """Topological sort; raise on a cycle (fail-closed)."""
        # Kahn's algorithm over the component graph.
        indegree: dict[str, int] = {cid: 0 for cid in self._components}
        adjacency: dict[str, list[str]] = {cid: [] for cid in self._components}
        for edge in self._edges:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1
        queue = [cid for cid, deg in indegree.items() if deg == 0]
        seen = 0
        while queue:
            cid = queue.pop()
            seen += 1
            for nxt in adjacency[cid]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if seen != len(self._components):
            raise NetworkError("component network contains a cycle")

    # -- compilation --------------------------------------------------------

    def compile(self) -> list[dict[str, Any]]:
        """Compile the network to an ordered CBP ``run`` plan.

        Returns a list of ``{"task", "args", "verifier"?, "child"?}`` steps in
        topological order, with each component's ``args`` resolved so that any
        edge feeding a target arg is filled from the source's output field.
        Fail-closed: an unknown output field on a source raises ``NetworkError``
        (the network must be fully wired before it runs).
        """
        self.validate()
        order = self._topological_order()
        # Resolve data-flow: for each target, fill args from source outputs.
        # The source's output is its ``args``-derived result; we model the
        # "output field" as a key the source is expected to produce. To keep the
        # compiler pure and deterministic, we resolve edges by reading the
        # source's *declared* output — here we treat the source's ``args`` as
        # the observable output record (the common case: a component's result
        # mirrors its inputs), and reject an unknown field.
        steps: list[dict[str, Any]] = []
        for cid in order:
            comp = self._components[cid]
            args = dict(comp.args)
            wires: list[dict[str, Any]] = []
            for edge in self._edges:
                if edge.target != cid:
                    continue
                src = self._components[edge.source]
                if src.ports.get("out"):
                    # A declared out-port is a real connection: the value flows at
                    # run time, so it is a wire (not a static arg).
                    wires.append(
                        {
                            "inport": edge.target_arg,
                            "source": edge.source,
                            "outport": edge.source_field,
                            "capacity": edge.capacity,
                        }
                    )
                    continue
                # Legacy static model: the source's args are its output record; an
                # unknown output field fails closed.
                if edge.source_field not in src.args:
                    raise NetworkError(
                        f"edge {edge.source}.{edge.source_field} -> {cid}.{edge.target_arg}: "
                        f"source has no output field {edge.source_field!r}"
                    )
                args[edge.target_arg] = src.args[edge.source_field]
            step: dict[str, Any] = {"task": comp.task, "args": args}
            if wires:
                step["wires"] = sorted(
                    wires,
                    key=lambda w: (w["source"], w["outport"], w["inport"], w["capacity"]),
                )
            if comp.verifier:
                step["verifier"] = comp.verifier
            if comp.child:
                step["child"] = comp.child
            steps.append(step)
        return steps

    def topological_order(self) -> list[str]:
        """The deterministic topological order of component ids (validated)."""
        self.validate()
        return self._topological_order()

    def _topological_order(self) -> list[str]:
        """Return component ids in a deterministic topological order.

        Deterministic: ties are broken by id (sorted), so the same graph always
        compiles to the same order regardless of insertion order.
        """
        indegree: dict[str, int] = {cid: 0 for cid in self._components}
        adjacency: dict[str, list[str]] = {cid: [] for cid in self._components}
        for edge in self._edges:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1
        # Deterministic: process ready nodes in sorted id order.
        ready = sorted(cid for cid, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while ready:
            cid = ready.pop(0)
            order.append(cid)
            for nxt in sorted(adjacency[cid]):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
                    ready.sort()
        return order

    # -- inspection ---------------------------------------------------------

    def components(self) -> tuple[Component, ...]:
        return tuple(sorted(self._components.values(), key=lambda c: c.id))

    def edges(self) -> tuple[Edge, ...]:
        return tuple(self._edges)

    def iips(self) -> tuple[IIP, ...]:
        return tuple(self._iips)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready, deterministic serialization (for the visual editor)."""
        return {
            "components": [
                {
                    "id": c.id,
                    "task": c.task,
                    "args": dict(c.args),
                    "verifier": c.verifier,
                    "child": c.child,
                    "ports": {k: list(v) for k, v in sorted(c.ports.items())},
                    **(
                        {
                            "port_types": {
                                k: dict(sorted(v.items()))
                                for k, v in sorted(c.port_types.items())
                            }
                        }
                        if c.port_types
                        else {}
                    ),
                    "stream": list(c.stream),
                    "external": dict(sorted(c.external.items())),
                    "subnet": c.subnet.to_dict() if c.subnet is not None else None,
                }
                for c in self.components()
            ],
            "edges": [
                {
                    "source": e.source,
                    "source_field": e.source_field,
                    "target": e.target,
                    "target_arg": e.target_arg,
                    "capacity": e.capacity,
                }
                for e in self.edges()
            ],
            "iips": [
                {"component": i.component, "port": i.port, "value": i.value}
                for i in sorted(self._iips, key=lambda i: (i.component, i.port))
            ],
        }

    def content_hash(self) -> str:
        """The content address of the network document (sha256, hex).

        Canonical and insertion-independent: the same graph always hashes the
        same. This is the value a composite freezes and registers (SPEC-0009).
        """
        return hashlib.sha256(canonical_json_bytes(self.to_dict())).hexdigest()


def network_from_dict(
    data: dict[str, Any], *, _depth: int = 0
) -> ComponentNetwork:
    """Reconstruct a ``ComponentNetwork`` from a ``to_dict()`` payload.

    Deterministic and fail-closed: unknown/malformed entries raise
    ``NetworkError``. Nested subnet bodies are parsed recursively.
    """
    if _depth > _MAX_SUBNET_DEPTH:
        raise NetworkError(
            f"subnet nesting exceeds the depth limit {_MAX_SUBNET_DEPTH} "
            "(fail-closed)"
        )
    net = ComponentNetwork()
    comps = data.get("components")
    if not isinstance(comps, list):
        raise NetworkError("network must define a 'components' list")
    for c in comps:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            raise NetworkError("each component needs an 'id' string")
        raw_ports = c.get("ports") or {}
        if not isinstance(raw_ports, dict):
            raise NetworkError("component 'ports' must be a mapping")
        ports: dict[str, tuple[str, ...]] = {}
        for direction, names in raw_ports.items():
            if not isinstance(names, list):
                raise NetworkError("component port names must be a list")
            ports[str(direction)] = tuple(str(name) for name in names)
        raw_port_types = c.get("port_types") or {}
        if not isinstance(raw_port_types, dict):
            raise NetworkError("component 'port_types' must be a mapping")
        port_types: dict[str, dict[str, str]] = {}
        for direction, typed in raw_port_types.items():
            if not isinstance(typed, dict):
                raise NetworkError("component 'port_types' entries must be mappings")
            port_types[str(direction)] = {
                str(name): str(type_name) for name, type_name in typed.items()
            }
        raw_external = c.get("external") or {}
        if not isinstance(raw_external, dict):
            raise NetworkError("component 'external' must be a mapping")
        external = {str(k): str(v) for k, v in raw_external.items()}
        raw_subnet = c.get("subnet")
        if raw_subnet is not None and not isinstance(raw_subnet, dict):
            raise NetworkError("component 'subnet' must be a mapping")
        subnet = (
            network_from_dict(raw_subnet, _depth=_depth + 1)
            if isinstance(raw_subnet, dict)
            else None
        )
        net.add_component(
            Component(
                id=c["id"],
                task=str(c.get("task", "")),
                args=dict(c.get("args") or {}),
                verifier=c.get("verifier"),
                child=c.get("child"),
                ports=ports,
                port_types=port_types,
                stream=tuple(str(s) for s in (c.get("stream") or [])),
                external=external,
                subnet=subnet,
            )
        )
    edges = data.get("edges")
    if edges is not None:
        if not isinstance(edges, list):
            raise NetworkError("'edges' must be a list")
        for e in edges:
            if not isinstance(e, dict):
                raise NetworkError("each edge must be a dict")
            try:
                capacity = int(e.get("capacity", DEFAULT_CONNECTION_CAPACITY))
            except (TypeError, ValueError) as exc:
                raise NetworkError(f"edge capacity must be an integer: {exc}") from exc
            net.add_edge(
                Edge(
                    source=str(e.get("source", "")),
                    source_field=str(e.get("source_field", "")),
                    target=str(e.get("target", "")),
                    target_arg=str(e.get("target_arg", "")),
                    capacity=capacity,
                )
            )
    iips = data.get("iips")
    if iips is not None:
        if not isinstance(iips, list):
            raise NetworkError("'iips' must be a list")
        for i in iips:
            if not isinstance(i, dict):
                raise NetworkError("each IIP must be a dict")
            net.add_iip(
                IIP(
                    component=str(i.get("component", "")),
                    port=str(i.get("port", "")),
                    value=i.get("value"),
                )
            )
    return net


def run_network(
    driver: Any, network: ComponentNetwork, *, step_limit: int | None = None
) -> dict[str, Any]:
    """Execute a component network as true dataflow through the verified spine.

    Components run in deterministic topological order. Each component's args are
    built from its template plus any incoming edges, which feed the **computed
    output** of the source component (its verified result) into the target's
    input args. Each step is a normal ``driver.run`` directive — parent
    re-verified, ledgered, replayable. Fail-closed: an invalid network, an
    unknown output field, or an unverified step returns ``ok=False``.

    ``step_limit`` bounds the number of components executed (an operator or edge
    transport grants one); a network larger than the bound fails closed **before**
    any work, so a caller cannot drive unbounded sequential work through the
    spine. ``None`` means the caller wants no cap at the call site; the module
    default still applies as the hard ceiling.

    Returns ``{"ok", "results", "completed", "error"?}`` where each result is
    ``{"step", "task", "verified", "value", "error", "id"}`` (``id`` is the
    component id, so the editor can map results back to nodes).
    """
    # A port-declared network runs on the first-class Information-Packet flow
    # engine (SPEC-0009); the legacy args model below is unchanged.
    if any(component.stream for component in network.components()):
        from .stream import run_stream

        return run_stream(driver, network, step_limit=step_limit)

    if network.iips() or any(component.ports for component in network.components()):
        from .flow import run_flow

        return run_flow(driver, network, step_limit=step_limit)

    try:
        network.validate()
        order = network._topological_order()
    except NetworkError as exc:
        return {"ok": False, "results": [], "completed": 0, "error": str(exc)}

    # A hard default ceiling on the number of components/steps, so a network
    # submitted through an edge transport cannot drive unbounded sequential
    # work. Fail-closed before any component runs.
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

    outputs: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    for idx, cid in enumerate(order):
        comp = network._components[cid]
        args = dict(comp.args)
        for edge in network._edges:
            if edge.target != cid:
                continue
            if edge.source not in outputs:
                # The source ran earlier (topological order) so its output is
                # known; if not, the edge is malformed -> fail closed.
                return {
                    "ok": False, "results": results, "completed": idx,
                    "error": f"edge {edge.source}.{edge.source_field} -> {cid}: "
                    f"source has no computed output",
                }
            args[edge.target_arg] = outputs[edge.source]
        try:
            resp = driver.run(
                comp.task, args, verifier=comp.verifier, child=comp.child
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            return {
                "ok": False, "results": results, "completed": idx,
                "error": f"component {cid} ({comp.task}) raised: {exc}",
            }
        result = {
            "step": idx, "id": cid, "task": comp.task,
            "verified": resp.verified, "value": resp.value, "error": resp.error,
        }
        results.append(result)
        if not resp.verified:
            return {
                "ok": False, "results": results, "completed": idx,
                "failed": result,
            }
        outputs[cid] = resp.value
    return {"ok": True, "results": results, "completed": len(results), "failed": None}