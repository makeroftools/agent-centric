"""Tests for deterministic network diagrams (``cbp/diagram.py``, Layer 3).

The read-only, static-fallback projection of a pinned component network: a pure,
offline function that is deterministic and content-addressed, validates
fail-closed, and renders to XML-escaped SVG/HTML.
"""

from __future__ import annotations

import json

import pytest

from agent_centric.cbp.diagram import (
    MAX_DIAGRAM_NODES,
    build_diagram,
    diagram_content_hash,
    render_html,
    render_svg,
)
from agent_centric.cbp.network import (
    IIP,
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
)


def _stage(cid: str, task: str) -> Component:
    return Component(
        id=cid,
        task=task,
        ports={"in": ("x",), "out": ("y",)},
        port_types={"in": {"x": "int"}, "out": {"y": "int"}},
    )


def _chain(order: tuple[str, ...] = ("a", "b", "c"), task: str = "double") -> ComponentNetwork:
    net = ComponentNetwork()
    stages = {cid: _stage(cid, task) for cid in ("a", "b", "c")}
    for cid in order:
        net.add_component(stages[cid])
    net.add_edge(Edge(source="a", source_field="y", target="b", target_arg="x"))
    net.add_edge(Edge(source="b", source_field="y", target="c", target_arg="x"))
    net.add_iip(IIP(component="a", port="x", value=1))
    return net


class TestDeterminism:
    def test_same_network_same_diagram(self) -> None:
        first = build_diagram(_chain())
        second = build_diagram(_chain())
        assert first == second
        assert first["content_hash"] == diagram_content_hash(first)

    def test_insertion_order_independent(self) -> None:
        assert build_diagram(_chain(("c", "b", "a"))) == build_diagram(_chain(("a", "b", "c")))

    def test_content_hash_tracks_network(self) -> None:
        base = build_diagram(_chain())
        other = ComponentNetwork()
        other.add_component(_stage("solo", "double"))
        assert build_diagram(other)["content_hash"] != base["content_hash"]


class TestLayout:
    def test_ranks_follow_edges(self) -> None:
        nodes = {n["id"]: n for n in build_diagram(_chain())["nodes"]}
        assert nodes["a"]["rank"] < nodes["b"]["rank"] < nodes["c"]["rank"]

    def test_columns_and_rows_recorded(self) -> None:
        diagram = build_diagram(_chain())
        assert diagram["columns"] == 3
        assert diagram["rows"] == 1

    def test_nodes_do_not_overlap(self) -> None:
        nodes = build_diagram(_chain())["nodes"]
        cells = {(n["rank"], n["row"]) for n in nodes}
        positions = {(n["x"], n["y"]) for n in nodes}
        assert len(cells) == len(nodes)
        assert len(positions) == len(nodes)

    def test_iips_are_recorded(self) -> None:
        diagram = build_diagram(_chain())
        assert diagram["iips"] == [{"component": "a", "port": "x", "value": 1}]

    def test_empty_network_is_valid(self) -> None:
        diagram = build_diagram(ComponentNetwork())
        assert diagram["nodes"] == []
        assert diagram["edges"] == []


class TestFailClosed:
    def test_invalid_network_is_rejected(self) -> None:
        net = ComponentNetwork()
        net.add_component(_stage("b", "double"))
        net.add_edge(Edge(source="ghost", source_field="y", target="b", target_arg="x"))
        with pytest.raises(NetworkError):
            build_diagram(net)


    def test_oversized_network_is_rejected(self) -> None:
        net = ComponentNetwork()
        for i in range(MAX_DIAGRAM_NODES + 1):
            net.add_component(Component(id=f"n{i:05d}", task="double"))
        with pytest.raises(NetworkError):
            build_diagram(net)


class TestRendering:
    def test_svg_is_deterministic(self) -> None:
        diagram = build_diagram(_chain())
        svg = render_svg(diagram)
        assert svg == render_svg(diagram)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        for cid in ("a", "b", "c"):
            assert f">{cid}<" in svg

    def test_svg_escapes_labels(self) -> None:
        net = _chain(task="a<b&c")
        svg = render_svg(build_diagram(net))
        assert "a&lt;b&amp;c" in svg
        assert "<b&c" not in svg

    def test_html_wraps_svg_without_scripts(self) -> None:
        html = render_html(build_diagram(_chain()), title="Net")
        assert html.startswith("<!doctype html>")
        assert "<script" not in html
        assert "<svg" in html
        assert "Net" in html


class TestWebRoute:
    def test_saved_network_diagram_is_read_only(self) -> None:
        from agent_centric.cbp.web import CbpLandingServer

        net = _chain()
        server = CbpLandingServer()
        try:
            saved = server._network_save(json.dumps({"name": "chain", "network": net.to_dict()}))
            assert saved["ok"] is True
            result = server._network_diagram("chain")
            assert result["ok"] is True
            assert result["diagram"]["network_sha256"] == net.content_hash()
            assert result["svg"].startswith("<svg")
        finally:
            server._driver.close()

    def test_unknown_network_diagram_fails_closed(self) -> None:
        from agent_centric.cbp.web import CbpLandingServer

        server = CbpLandingServer()
        try:
            result = server._network_diagram("ghost")
            assert result["ok"] is False
            assert "ghost" in result["error"]
        finally:
            server._driver.close()
