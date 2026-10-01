"""Deterministic, read-only network diagrams (Layer 3, static fallback).

A component network (``network.v1``) is pinned and verified; this module renders
it as a **read-only diagram** for humans. The projection is a pure, offline,
deterministic function of the network:

- **Deterministic.** Nodes are ranked by longest path from the roots and ordered
  by id within a rank; edges are sorted by (source, source_field, target,
  target_arg). Identical networks always produce an identical diagram, whatever
  the order components were added.
- **Content-addressed.** The diagram carries the network's content hash and its
  own ``content_hash`` (canonical-JSON sha256), so a rendered image can be tied
  to the exact wiring it depicts.
- **Read-only.** Rendering never mutates the network and never decides anything.
  Labels are XML-escaped, so a rendered SVG/HTML is safe to embed.

The primary web surface may be interactive; this static JSON/SVG form is the
fallback required by the plan (Layer 3). It is the layer an operator can audit
and a report can attach.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from .network import ComponentNetwork

# Layout constants (deterministic; changing them changes every diagram, so they
# are part of the projection and are recorded in the diagram's content hash).
NODE_WIDTH = 168
NODE_HEIGHT = 58
RANK_GAP = 232
ROW_GAP = 100
MARGIN = 40


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _ranks(network: ComponentNetwork) -> dict[str, int]:
    """Longest-path rank per component (roots are rank 0). Deterministic."""
    order = network.topological_order()
    rank: dict[str, int] = {cid: 0 for cid in order}
    for cid in order:
        for edge in network.edges():
            if edge.source == cid:
                rank[edge.target] = max(rank[edge.target], rank[cid] + 1)
    return rank


def _layout(network: ComponentNetwork) -> tuple[list[dict[str, Any]], int, int]:
    """Deterministic node layout; returns (nodes, columns, rows)."""
    rank = _ranks(network)
    by_rank: dict[int, list[str]] = {}
    for component in network.components():
        by_rank.setdefault(rank[component.id], []).append(component.id)
    components = {c.id: c for c in network.components()}
    rows = max((len(ids) for ids in by_rank.values()), default=0)
    nodes: list[dict[str, Any]] = []
    for r in sorted(by_rank):
        ids = by_rank[r]
        offset = ((rows - len(ids)) * ROW_GAP) // 2
        for row, cid in enumerate(ids):
            component = components[cid]
            nodes.append(
                {
                    "id": cid,
                    "task": component.task,
                    "composite": component.subnet is not None,
                    "rank": r,
                    "row": row,
                    "x": MARGIN + r * RANK_GAP,
                    "y": MARGIN + offset + row * ROW_GAP,
                }
            )
    return nodes, max((n["rank"] for n in nodes), default=0), rows


def _routed_edges(
    network: ComponentNetwork, positions: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Edges with a deterministic orthogonal route between node centers."""
    edges: list[dict[str, Any]] = []
    for edge in sorted(
        network.edges(),
        key=lambda e: (e.source, e.source_field, e.target, e.target_arg, e.capacity),
    ):
        src = positions[edge.source]
        dst = positions[edge.target]
        sx = int(src["x"]) + NODE_WIDTH
        sy = int(src["y"]) + NODE_HEIGHT // 2
        tx = int(dst["x"])
        ty = int(dst["y"]) + NODE_HEIGHT // 2
        mid = (sx + tx) // 2
        edges.append(
            {
                "from": edge.source,
                "from_port": edge.source_field,
                "to": edge.target,
                "to_port": edge.target_arg,
                "capacity": edge.capacity,
                "points": [[sx, sy], [mid, sy], [mid, ty], [tx, ty]],
            }
        )
    return edges


def build_diagram(network: ComponentNetwork) -> dict[str, Any]:
    """Project a validated network into a deterministic, content-addressed diagram.

    Fail-closed: an invalid network (a cycle, a missing component, a duplicated
    IIP, …) is rejected by ``validate`` before any diagram is produced.
    """
    network.validate()
    nodes, columns, rows = _layout(network)
    positions = {node["id"]: node for node in nodes}
    body: dict[str, Any] = {
        "schema": "cbp.diagram.v1",
        "network_sha256": network.content_hash(),
        "columns": columns + 1,
        "rows": rows,
        "nodes": nodes,
        "edges": _routed_edges(network, positions),
        "iips": [
            {"component": i.component, "port": i.port, "value": i.value}
            for i in sorted(network.iips(), key=lambda i: (i.component, i.port))
        ],
    }
    diagram = dict(body)
    diagram["content_hash"] = hashlib.sha256(_canonical(body)).hexdigest()
    return diagram


def diagram_content_hash(diagram: dict[str, Any]) -> str:
    """Recompute the diagram's content hash (canonical body, minus ``content_hash``)."""
    body = {k: v for k, v in diagram.items() if k != "content_hash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _svg_size(diagram: dict[str, Any]) -> tuple[int, int]:
    nodes = diagram.get("nodes", [])
    if not nodes:
        return MARGIN * 2 + NODE_WIDTH, MARGIN * 2 + NODE_HEIGHT
    width = max(int(n["x"]) for n in nodes) + NODE_WIDTH + MARGIN
    height = max(int(n["y"]) for n in nodes) + NODE_HEIGHT + MARGIN
    return width, height


def render_svg(diagram: dict[str, Any]) -> str:
    """Render a diagram to a standalone, deterministic SVG string (read-only)."""
    width, height = _svg_size(diagram)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="cbp-diagram" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker></defs>',
        '<g class="edges" fill="none" stroke="#64748b" stroke-width="1.5">',
    ]
    for edge in diagram.get("edges", []):
        points = edge["points"]
        d = "M " + " L ".join(f"{int(x)} {int(y)}" for x, y in points)
        parts.append(f'<path d={quoteattr(d)} marker-end="url(#arrow)"/>')
    parts.append("</g>")
    parts.append('<g class="nodes">')
    for node in diagram.get("nodes", []):
        x = int(node["x"])
        y = int(node["y"])
        label = str(node["id"])
        task = str(node["task"])
        if node.get("composite"):
            task = f"{task} (composite)"
        cx = x + NODE_WIDTH // 2
        parts.append(
            f'<rect x="{x}" y="{y}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}" '
            'rx="10" fill="#f8fafc" stroke="#334155" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{cx}" y="{y + 24}" text-anchor="middle" '
            'font-family="sans-serif" font-size="14" font-weight="600" '
            f'fill="#0f172a">{escape(label)}</text>'
        )
        parts.append(
            f'<text x="{cx}" y="{y + 44}" text-anchor="middle" '
            'font-family="sans-serif" font-size="11" '
            f'fill="#64748b">{escape(task)}</text>'
        )
    parts.append("</g>")
    for edge in diagram.get("edges", []):
        mid = edge["points"][1]
        label = f'{edge["from_port"]}\u2192{edge["to_port"]}'
        parts.append(
            f'<text x="{int(mid[0])}" y="{int(mid[1]) - 4}" text-anchor="middle" '
            'font-family="sans-serif" font-size="10" '
            f'fill="#94a3b8">{escape(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_html(diagram: dict[str, Any], *, title: str = "CBP network") -> str:
    """A minimal read-only HTML wrapper embedding the diagram (no scripts)."""
    network_sha = escape(str(diagram.get("network_sha256", "")))
    diagram_sha = escape(str(diagram.get("content_hash", "")))
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title></head>\n"
        "<body style=\"font-family:sans-serif;background:#ffffff;color:#0f172a\">\n"
        f"<h1>{escape(title)}</h1>\n"
        f'<p>network sha256: <code>{network_sha}</code><br>'
        f"diagram sha256: <code>{diagram_sha}</code></p>\n"
        f"{render_svg(diagram)}\n"
        "</body></html>\n"
    )
