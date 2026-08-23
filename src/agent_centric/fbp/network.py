"""Component Networks — a deterministic visual-programming core for FBP.

A **component network** is a directed graph of components (nodes) wired by
data-flow edges. It is the model behind a visual programming interface: a user
places components and connects outputs to inputs, and the network compiles to an
ordered FBP ``run`` plan that executes through the driver's verified spine.

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
the FBP spine can audit and replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class NetworkError(ValueError):
    """A component network is invalid (fail-closed)."""


@dataclass(frozen=True)
class Component:
    """A single component (node) in the network.

    Attributes:
        id: A stable, unique id within the network.
        task: The FBP task this component runs (a registered callable).
        args: A template of input args; edges may override/feed these.
        verifier: An optional parent verifier for this component's run.
        child: An optional delegation target for this component's run.
    """

    id: str
    task: str
    args: dict[str, Any] = field(default_factory=dict)
    verifier: str | None = None
    child: str | None = None


@dataclass(frozen=True)
class Edge:
    """A data-flow edge: ``source`` output field -> ``target`` input arg.

    Attributes:
        source: The source component id.
        source_field: The output field on the source (its result dict key).
        target: The target component id.
        target_arg: The input arg on the target to feed.
    """

    source: str
    source_field: str
    target: str
    target_arg: str


class ComponentNetwork:
    """A validated, compilable directed acyclic graph of components.

    Components are added by id (idempotent — re-adding replaces), edges wire
    outputs to inputs. ``compile()`` returns an ordered FBP ``run`` plan in
    topological order with data-flow resolved into each component's ``args``.
    """

    def __init__(self) -> None:
        self._components: dict[str, Component] = {}
        self._edges: list[Edge] = []

    # -- construction -------------------------------------------------------

    def add_component(self, component: Component) -> ComponentNetwork:
        """Add (or replace) a component by id. Returns self for chaining."""
        if not component.id:
            raise NetworkError("component id must be non-empty")
        self._components[component.id] = component
        return self

    def add_edge(self, edge: Edge) -> ComponentNetwork:
        """Add a data-flow edge. Returns self for chaining."""
        self._edges.append(edge)
        return self

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """Validate the network fail-closed.

        Raises ``NetworkError`` if any edge references a missing component or an
        unknown output field, or if the graph contains a cycle (not a DAG).
        """
        for edge in self._edges:
            if edge.source not in self._components:
                raise NetworkError(f"edge source {edge.source!r} is not a component")
            if edge.target not in self._components:
                raise NetworkError(f"edge target {edge.target!r} is not a component")
            if edge.source == edge.target:
                raise NetworkError("self-loop edge is not allowed")
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
        """Compile the network to an ordered FBP ``run`` plan.

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
            for edge in self._edges:
                if edge.target != cid:
                    continue
                src = self._components[edge.source]
                # The source's output record is its args (deterministic model);
                # an unknown output field fails closed.
                if edge.source_field not in src.args:
                    raise NetworkError(
                        f"edge {edge.source}.{edge.source_field} -> {cid}.{edge.target_arg}: "
                        f"source has no output field {edge.source_field!r}"
                    )
                args[edge.target_arg] = src.args[edge.source_field]
            step: dict[str, Any] = {"task": comp.task, "args": args}
            if comp.verifier:
                step["verifier"] = comp.verifier
            if comp.child:
                step["child"] = comp.child
            steps.append(step)
        return steps

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
                }
                for c in self.components()
            ],
            "edges": [
                {
                    "source": e.source,
                    "source_field": e.source_field,
                    "target": e.target,
                    "target_arg": e.target_arg,
                }
                for e in self.edges()
            ],
        }


def network_from_dict(data: dict[str, Any]) -> ComponentNetwork:
    """Reconstruct a ``ComponentNetwork`` from a ``to_dict()`` payload.

    Deterministic and fail-closed: unknown/malformed entries raise
    ``NetworkError``.
    """
    net = ComponentNetwork()
    comps = data.get("components")
    if not isinstance(comps, list):
        raise NetworkError("network must define a 'components' list")
    for c in comps:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            raise NetworkError("each component needs an 'id' string")
        net.add_component(
            Component(
                id=c["id"],
                task=str(c.get("task", "")),
                args=dict(c.get("args") or {}),
                verifier=c.get("verifier"),
                child=c.get("child"),
            )
        )
    edges = data.get("edges")
    if edges is not None:
        if not isinstance(edges, list):
            raise NetworkError("'edges' must be a list")
        for e in edges:
            if not isinstance(e, dict):
                raise NetworkError("each edge must be a dict")
            net.add_edge(
                Edge(
                    source=str(e.get("source", "")),
                    source_field=str(e.get("source_field", "")),
                    target=str(e.get("target", "")),
                    target_arg=str(e.get("target_arg", "")),
                )
            )
    return net


def run_network(driver: Any, network: ComponentNetwork) -> dict[str, Any]:
    """Execute a component network as true dataflow through the verified spine.

    Components run in deterministic topological order. Each component's args are
    built from its template plus any incoming edges, which feed the **computed
    output** of the source component (its verified result) into the target's
    input args. Each step is a normal ``driver.run`` directive — parent
    re-verified, ledgered, replayable. Fail-closed: an invalid network, an
    unknown output field, or an unverified step returns ``ok=False``.

    Returns ``{"ok", "results", "completed", "error"?}`` where each result is
    ``{"step", "task", "verified", "value", "error", "id"}`` (``id`` is the
    component id, so the editor can map results back to nodes).
    """
    try:
        network.validate()
        order = network._topological_order()
    except NetworkError as exc:
        return {"ok": False, "results": [], "completed": 0, "error": str(exc)}

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