"""n8n authoring adapter — ``design.v1`` ⇄ n8n workflow (SPEC-0008 Phase 2).

n8n is a **design-time authoring surface**; it never sits on the execution path
(SPEC-0008 §3). This adapter is a pure, offline translation between a
``design.v1`` document and a canonical n8n workflow projection:

- an n8n **node** ⇄ a network **component** (the node's ``parameters.cbp`` carries
  the component's declaration);
- an n8n **connection** ⇄ a network **edge** (the visual data-flow graph); each
  edge's ``source_field`` / ``target_arg`` / ``capacity`` is carried on the
  target node's ``parameters.cbp.inputs``;
- a network **IIP** is carried on the target node's ``parameters.cbp.iips``;
- the design's ``id`` / ``title`` / ``components`` / ``metadata`` / ``envelopes``
  / ``schema`` ride in ``meta.cbp``.

The round-trip is **lossless and stable**: ``from_n8n(to_n8n(design))`` has the
same ``design_hash``, and ``to_n8n(from_n8n(workflow))`` reproduces the same
canonical bytes. The adapter is **strict and fail-closed**: a non-projection
workflow, an unknown node type or component/edge/IIP key, an unsupported nested
``subnet``, an inconsistent connection graph, or a broken edge/IIP ordering is an
explicit error, never a partial import.

n8n is not imported here (no dependency, no network): the projection is data.
"""

from __future__ import annotations

from typing import Any

from ..contracts.design import Design, EnvelopeGrant, RequestedComponent

PROJECTION = "cbp.n8n.projection/v1"
NODE_TYPE = "cbp.component"
_SCHEMA_KEY = "cbp"

_NETWORK_KEYS = ("components", "edges", "iips")
_COMPONENT_REQUIRED = ("id", "task")
_COMPONENT_OPTIONAL = ("args", "verifier", "child", "ports", "port_types", "stream", "external")
_EDGE_REQUIRED = ("source", "source_field", "target", "target_arg")
_EDGE_OPTIONAL = ("capacity",)
_IIP_REQUIRED = ("component", "port", "value")


class N8nAdapterError(ValueError):
    """A workflow is not a valid CBP projection (fail-closed)."""


def _as_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise N8nAdapterError(f"{what} must be an object")
    return value


def _known_keys(
    obj: dict[str, Any], required: tuple[str, ...], optional: tuple[str, ...], what: str
) -> None:
    for key in required:
        if key not in obj:
            raise N8nAdapterError(f"{what} is missing required key {key!r}")
    unknown = sorted(set(obj) - set(required) - set(optional))
    if unknown:
        raise N8nAdapterError(f"{what} has unsupported key(s) {unknown}")


def _edge_order(edge: dict[str, Any], index: int) -> int:
    order = edge.get("cbp_order", index)
    if not isinstance(order, int) or isinstance(order, bool) or order < 0:
        raise N8nAdapterError("edge cbp_order must be a non-negative integer")
    return order


def _target_of(edge: dict[str, Any]) -> str:
    target = edge.get("target")
    if not isinstance(target, str):
        raise N8nAdapterError("edge target must be a string")
    return target


def _position(index: int) -> list[int]:
    return [(index % 6) * 220, (index // 6) * 140]


def to_n8n(design: Design) -> dict[str, Any]:
    """Project a ``design.v1`` into a canonical n8n workflow (deterministic)."""
    network = _as_dict(design.network, "design network")
    unknown = sorted(set(network) - set(_NETWORK_KEYS))
    if unknown:
        raise N8nAdapterError(f"design network has unsupported key(s) {unknown}")
    components = network.get("components")
    if not isinstance(components, list):
        raise N8nAdapterError("design network must declare a components list")
    edges = network.get("edges", [])
    iips = network.get("iips", [])
    if not isinstance(edges, list) or not isinstance(iips, list):
        raise N8nAdapterError("design network edges/iips must be lists")

    for component in components:
        component = _as_dict(component, "network component")
        if "subnet" in component:
            raise N8nAdapterError("nested subnets are not supported by the n8n adapter")
        _known_keys(component, _COMPONENT_REQUIRED, _COMPONENT_OPTIONAL, "network component")

    inputs_by_target: dict[str, list[dict[str, Any]]] = {}
    connections: dict[str, Any] = {}
    for order, edge in enumerate(edges):
        edge = _as_dict(edge, "network edge")
        _known_keys(edge, _EDGE_REQUIRED, _EDGE_OPTIONAL, "network edge")
        target = _target_of(edge)
        entry: dict[str, Any] = {
            "from": edge["source"],
            "field": edge["source_field"],
            "arg": edge["target_arg"],
        }
        if "capacity" in edge:
            entry["capacity"] = edge["capacity"]
        entry["cbp_order"] = order
        inputs_by_target.setdefault(target, []).append(entry)
        slot = connections.setdefault(str(edge["source"]), {"main": [[]]})
        slot["main"][0].append(
            {"node": target, "type": "main", "index": len(slot["main"][0])}
        )

    iips_by_target: dict[str, list[dict[str, Any]]] = {}
    for order, iip in enumerate(iips):
        iip = _as_dict(iip, "network IIP")
        _known_keys(iip, _IIP_REQUIRED, (), "network IIP")
        iips_by_target.setdefault(str(iip["component"]), []).append(
            {"port": iip["port"], "value": iip["value"], "cbp_order": order}
        )

    nodes: list[dict[str, Any]] = []
    for index, component in enumerate(components):
        component = _as_dict(component, "network component")
        cid = str(component["id"])
        cbp: dict[str, Any] = {"task": component["task"]}
        for key in _COMPONENT_OPTIONAL:
            if key in component:
                cbp[key] = component[key]
        cbp["inputs"] = inputs_by_target.get(cid, [])
        cbp["iips"] = iips_by_target.get(cid, [])
        nodes.append(
            {
                "id": f"cbp-{cid}",
                "name": cid,
                "type": NODE_TYPE,
                "typeVersion": 1,
                "position": _position(index),
                "parameters": {_SCHEMA_KEY: cbp},
            }
        )

    meta_cbp: dict[str, Any] = {
        "projection": PROJECTION,
        "schema": design.schema,
        "id": design.id,
        "title": design.title,
        "components": [c.to_dict() for c in design.components],
        "metadata": dict(design.metadata),
        "network_keys": [key for key in _NETWORK_KEYS if key in network],
    }
    # Additive: only present when the design declares envelope limits, so a
    # design without envelopes keeps its exact prior workflow projection.
    if design.envelopes:
        meta_cbp["envelopes"] = [e.to_dict() for e in design.envelopes]
    return {
        "name": design.title or design.id,
        "nodes": nodes,
        "connections": connections,
        "settings": {},
        "meta": {_SCHEMA_KEY: meta_cbp},
    }


def from_n8n(workflow: dict[str, Any]) -> Design:
    """Import a CBP n8n projection back into a ``design.v1`` (strict)."""
    workflow = _as_dict(workflow, "workflow")
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, dict):
        raise N8nAdapterError("workflow must declare nodes (list) and connections (object)")
    meta = _as_dict(workflow.get("meta"), "workflow meta")
    cbp = _as_dict(meta.get(_SCHEMA_KEY), "workflow meta.cbp")
    if cbp.get("projection") != PROJECTION:
        raise N8nAdapterError(f"not a {PROJECTION} workflow")
    network_keys = cbp.get("network_keys")
    if not isinstance(network_keys, list) or "components" not in network_keys:
        raise N8nAdapterError("meta.cbp.network_keys must include 'components'")
    if sorted(network_keys) != sorted(set(network_keys)):
        raise N8nAdapterError("meta.cbp.network_keys must be unique")

    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_edges: list[tuple[int, dict[str, Any]]] = []
    raw_iips: list[tuple[int, dict[str, Any]]] = []
    for node in nodes:
        node = _as_dict(node, "workflow node")
        if node.get("type") != NODE_TYPE:
            raise N8nAdapterError(f"unsupported node type {node.get('type')!r}")
        name = node.get("name")
        if not isinstance(name, str) or not name:
            raise N8nAdapterError("workflow node name must be a non-empty string")
        if name in seen:
            raise N8nAdapterError(f"duplicate node name {name!r}")
        seen.add(name)
        params = _as_dict(node.get("parameters"), "node parameters")
        body = _as_dict(params.get(_SCHEMA_KEY), "node parameters.cbp")
        allowed = {"task", "inputs", "iips", *_COMPONENT_OPTIONAL}
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise N8nAdapterError(f"node {name!r} has unsupported cbp key(s) {unknown}")
        if "task" not in body:
            raise N8nAdapterError(f"node {name!r} is missing a task")
        component: dict[str, Any] = {"id": name, "task": body["task"]}
        for key in _COMPONENT_OPTIONAL:
            if key in body:
                component[key] = body[key]
        components.append(component)

        for index, entry in enumerate(body.get("inputs", [])):
            entry = _as_dict(entry, "node input")
            _known_keys(entry, ("from", "field", "arg"), ("capacity", "cbp_order"), "node input")
            edge: dict[str, Any] = {
                "source": entry["from"],
                "source_field": entry["field"],
                "target": name,
                "target_arg": entry["arg"],
            }
            if "capacity" in entry:
                edge["capacity"] = entry["capacity"]
            raw_edges.append((_edge_order(entry, index), edge))

        for index, entry in enumerate(body.get("iips", [])):
            entry = _as_dict(entry, "node iip")
            _known_keys(entry, ("port", "value"), ("cbp_order",), "node iip")
            raw_iips.append(
                (
                    _edge_order(entry, index),
                    {"component": name, "port": entry["port"], "value": entry["value"]},
                )
            )

    raw_edges.sort(key=lambda item: item[0])
    raw_iips.sort(key=lambda item: item[0])
    if [order for order, _ in raw_edges] != list(range(len(raw_edges))):
        raise N8nAdapterError("edge cbp_order must be a permutation of 0..n-1")
    if [order for order, _ in raw_iips] != list(range(len(raw_iips))):
        raise N8nAdapterError("IIP cbp_order must be a permutation of 0..n-1")
    edges = [edge for _order, edge in raw_edges]
    iips = [iip for _order, iip in raw_iips]

    for edge in edges:
        if edge["source"] not in seen or edge["target"] not in seen:
            raise N8nAdapterError("edge references an unknown component")

    expected: dict[str, Any] = {}
    for edge in edges:
        slot = expected.setdefault(str(edge["source"]), {"main": [[]]})
        slot["main"][0].append(
            {"node": edge["target"], "type": "main", "index": len(slot["main"][0])}
        )
    if connections != expected:
        raise N8nAdapterError("connections do not match the component edge topology")

    network: dict[str, Any] = {"components": components}
    if "edges" in network_keys:
        network["edges"] = edges
    if "iips" in network_keys:
        network["iips"] = iips

    raw_components = cbp.get("components", [])
    if not isinstance(raw_components, list):
        raise N8nAdapterError("meta.cbp.components must be a list")
    metadata = cbp.get("metadata", {})
    if not isinstance(metadata, dict):
        raise N8nAdapterError("meta.cbp.metadata must be an object")
    raw_envelopes = cbp.get("envelopes", [])
    if not isinstance(raw_envelopes, list):
        raise N8nAdapterError("meta.cbp.envelopes must be a list")
    try:
        return Design(
            id=str(cbp.get("id", "")),
            network=network,
            title=str(cbp.get("title", "")),
            components=tuple(
                RequestedComponent.from_dict(_as_dict(c, "requested component"))
                for c in raw_components
            ),
            metadata=dict(metadata),
            envelopes=tuple(
                EnvelopeGrant.from_dict(_as_dict(e, "envelope grant"))
                for e in raw_envelopes
            ),
            schema=str(cbp.get("schema", "")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise N8nAdapterError(f"imported design is invalid: {exc}") from exc
