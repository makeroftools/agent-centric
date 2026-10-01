"""Tests for first-class ports and bounded connections (SPEC-0009 slice 1).

Additive to ``cbp/network.py``: named ports, a connection capacity, fail-closed
port validation, deterministic content hashing, and port-aware compilation. The
legacy args-based model stays intact when no ports are declared.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.design import validate_design
from agent_centric.cbp.network import (
    DEFAULT_CONNECTION_CAPACITY,
    MAX_CONNECTION_CAPACITY,
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
    network_from_dict,
)
from agent_centric.contracts.design import Design


def _port_network(*, capacity: int = 2) -> ComponentNetwork:
    net = ComponentNetwork()
    net.add_component(
        Component(id="a", task="double", args={"value": 21}, ports={"out": ("value",)})
    )
    net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
    net.add_edge(
        Edge(source="a", source_field="value", target="b", target_arg="value", capacity=capacity)
    )
    return net


class TestPortValidation:
    def test_declared_ports_validate(self) -> None:
        _port_network().validate()  # must not raise

    def test_undeclared_out_port_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="double", ports={"out": ("value",)}))
        net.add_component(Component(id="b", task="even"))
        net.add_edge(Edge(source="a", source_field="nope", target="b", target_arg="value"))
        with pytest.raises(NetworkError):
            net.validate()

    def test_undeclared_in_port_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="double", args={"value": 1}))
        net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="nope"))
        with pytest.raises(NetworkError):
            net.validate()

    def test_unknown_port_direction_fails_closed(self) -> None:
        net = ComponentNetwork()
        with pytest.raises(NetworkError):
            net.add_component(Component(id="a", task="t", ports={"sideways": ("x",)}))

    def test_duplicate_port_names_fail_closed(self) -> None:
        net = ComponentNetwork()
        with pytest.raises(NetworkError):
            net.add_component(Component(id="a", task="t", ports={"in": ("x", "x")}))

    def test_blank_port_name_fails_closed(self) -> None:
        net = ComponentNetwork()
        with pytest.raises(NetworkError):
            net.add_component(Component(id="a", task="t", ports={"in": ("",)}))


class TestBoundedCapacity:
    def test_default_capacity_is_one(self) -> None:
        edge = Edge(source="a", source_field="x", target="b", target_arg="y")
        assert edge.capacity == DEFAULT_CONNECTION_CAPACITY

    def test_zero_capacity_fails_closed(self) -> None:
        net = _port_network(capacity=0)
        with pytest.raises(NetworkError):
            net.validate()

    def test_oversized_capacity_fails_closed(self) -> None:
        net = _port_network(capacity=MAX_CONNECTION_CAPACITY + 1)
        with pytest.raises(NetworkError):
            net.validate()


class TestCompilePorts:
    def test_port_edge_compiles_to_a_wire(self) -> None:
        steps = _port_network().compile()
        assert [s["task"] for s in steps] == ["double", "even"]
        assert steps[1]["args"] == {}
        assert steps[1]["wires"] == [
            {"inport": "value", "source": "a", "outport": "value", "capacity": 2}
        ]

    def test_wires_are_deterministically_sorted(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="s", task="sum", ports={"in": ("a", "b")}))
        net.add_component(Component(id="z", task="src", args={"v": 1}, ports={"out": ("v",)}))
        net.add_component(Component(id="a", task="src", args={"v": 2}, ports={"out": ("v",)}))
        net.add_edge(Edge(source="z", source_field="v", target="s", target_arg="b"))
        net.add_edge(Edge(source="a", source_field="v", target="s", target_arg="a"))
        wires = net.compile()[2]["wires"]
        assert [w["source"] for w in wires] == ["a", "z"]

    def test_legacy_network_is_unchanged(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="double", args={"value": 21}))
        net.add_component(Component(id="b", task="even"))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        steps = net.compile()
        assert steps[1]["args"]["value"] == 21
        assert "wires" not in steps[1]


class TestContentHash:
    def test_hash_is_insertion_independent(self) -> None:
        first = _port_network()
        second = ComponentNetwork()
        second.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
        second.add_component(
            Component(id="a", task="double", args={"value": 21}, ports={"out": ("value",)})
        )
        second.add_edge(
            Edge(source="a", source_field="value", target="b", target_arg="value", capacity=2)
        )
        assert first.content_hash() == second.content_hash()

    def test_hash_is_key_order_independent(self) -> None:
        data = _port_network().to_dict()
        reordered = {
            "edges": data["edges"],
            "components": [
                {"ports": c["ports"], "id": c["id"], "task": c["task"], "args": c["args"],
                 "child": c["child"], "verifier": c["verifier"]}
                for c in data["components"]
            ],
        }
        assert (
            network_from_dict(data).content_hash()
            == network_from_dict(reordered).content_hash()
        )

    def test_roundtrip_preserves_ports_and_capacity(self) -> None:
        data = _port_network().to_dict()
        assert network_from_dict(data).to_dict() == data


class TestFromDictValidation:
    def test_non_list_ports_fail_closed(self) -> None:
        with pytest.raises(NetworkError):
            network_from_dict({"components": [{"id": "a", "ports": {"out": "nope"}}]})

    def test_non_integer_capacity_fail_closed(self) -> None:
        with pytest.raises(NetworkError):
            network_from_dict(
                {
                    "components": [{"id": "a"}, {"id": "b"}],
                    "edges": [{"source": "a", "target": "b", "capacity": "lots"}],
                }
            )


class TestDesignIntegration:
    def test_design_rejects_undeclared_port(self) -> None:
        design = Design(
            id="bad-ports",
            network={
                "components": [
                    {"id": "a", "task": "t", "args": {"v": 1}, "ports": {"out": ["v"]}},
                    {"id": "b", "task": "u", "args": {}, "ports": {"in": ["w"]}},
                ],
                "edges": [
                    {"source": "a", "source_field": "v", "target": "b", "target_arg": "z"}
                ],
            },
        )
        report = validate_design(design)
        assert not report.ok
        assert any("in-port" in error for error in report.errors)
