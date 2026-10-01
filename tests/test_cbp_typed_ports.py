"""Tests for named typed ports (SPEC-0009 slice 4).

Additive to ``cbp/network.py``: a component may declare a value type for each
named port. Edge type compatibility and IIP values are validated fail-closed,
and both flow engines enforce the declared type at run time. Untyped ports keep
the wildcard ``"any"`` and the legacy serialization (and content hash) unchanged.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.design import validate_design
from agent_centric.cbp.driver import CbpDriver
from agent_centric.cbp.flow import run_flow
from agent_centric.cbp.network import (
    IIP,
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
    network_from_dict,
    run_network,
)
from agent_centric.cbp.stream import run_stream
from agent_centric.contracts.design import Design
from agent_centric.contracts.handoff import (
    is_known_type,
    types_compatible,
    value_matches_type,
)


@pytest.fixture
def driver() -> object:
    d = CbpDriver()
    d.register("double", lambda value: value * 2, source_url="file:///tasks/double")
    d.register("text", lambda value: "not-an-int", source_url="file:///tasks/text")
    d.register(
        "even",
        lambda value: isinstance(value, int) and value % 2 == 0,
        source_url="file:///tasks/even",
    )
    d.register(
        "gen", lambda count: [str(i) for i in range(count)], source_url="file:///tasks/gen"
    )
    d.register("sink", lambda value: value, source_url="file:///tasks/sink")
    d.configure(
        tasks=("double", "text", "even", "gen", "sink"),
        verifiers=("even",),
    )
    try:
        yield d
    finally:
        d.close()


def _typed_chain(*, out_type: str = "int", in_type: str = "int") -> ComponentNetwork:
    net = ComponentNetwork()
    net.add_component(
        Component(
            id="a", task="double", args={"value": 21},
            ports={"out": ("value",)},
            port_types={"out": {"value": out_type}},
        )
    )
    net.add_component(
        Component(
            id="b", task="even",
            ports={"in": ("value",)},
            port_types={"in": {"value": in_type}},
        )
    )
    net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
    return net


class TestTypeHelpers:
    def test_is_known_type(self) -> None:
        assert is_known_type("int") and is_known_type("any")
        assert not is_known_type("widget")

    def test_types_compatible(self) -> None:
        assert types_compatible("int", "int")
        assert types_compatible("int", "any")
        assert types_compatible("any", "int")
        assert not types_compatible("int", "str")
        assert not types_compatible("int", "widget")

    def test_value_matches_type(self) -> None:
        assert value_matches_type(3, "int")
        assert not value_matches_type(True, "int")
        assert not value_matches_type("x", "int")
        assert value_matches_type("x", "any")
        assert not value_matches_type("x", "widget")


class TestTypedPortValidation:
    def test_typed_ports_validate(self) -> None:
        _typed_chain().validate()  # must not raise

    def test_unknown_type_fails_closed(self) -> None:
        net = ComponentNetwork()
        with pytest.raises(NetworkError):
            net.add_component(
                Component(
                    id="a", task="t",
                    ports={"out": ("value",)},
                    port_types={"out": {"value": "widget"}},
                )
            )

    def test_type_for_undeclared_port_fails_closed(self) -> None:
        net = ComponentNetwork()
        with pytest.raises(NetworkError):
            net.add_component(
                Component(
                    id="a", task="t",
                    ports={"out": ("value",)},
                    port_types={"out": {"other": "int"}},
                )
            )

    def test_unknown_direction_in_port_types_fails_closed(self) -> None:
        net = ComponentNetwork()
        with pytest.raises(NetworkError):
            net.add_component(
                Component(
                    id="a", task="t",
                    ports={"in": ("x",)},
                    port_types={"sideways": {"x": "int"}},
                )
            )

    def test_incompatible_edge_types_fail_closed(self) -> None:
        net = _typed_chain(out_type="int", in_type="str")
        with pytest.raises(NetworkError) as excinfo:
            net.validate()
        assert "incompatible port types" in str(excinfo.value)

    def test_any_wildcard_is_compatible(self) -> None:
        _typed_chain(out_type="any", in_type="int").validate()
        _typed_chain(out_type="int", in_type="any").validate()


class TestTypedIIP:
    def _iip_net(self, value: object, *, in_type: str = "int") -> ComponentNetwork:
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="b", task="even",
                ports={"in": ("value",)},
                port_types={"in": {"value": in_type}},
            )
        )
        net.add_iip(IIP(component="b", port="value", value=value))
        return net

    def test_matching_iip_validates(self) -> None:
        self._iip_net(8).validate()

    def test_mismatched_iip_fails_closed(self) -> None:
        with pytest.raises(NetworkError) as excinfo:
            self._iip_net("nope").validate()
        assert "declared in-port type" in str(excinfo.value)

    def test_any_iip_accepts_anything(self) -> None:
        self._iip_net("nope", in_type="any").validate()


class TestSerialization:
    def test_untyped_serialization_is_unchanged(self) -> None:
        net = _typed_chain()
        # Strip the types to simulate a legacy untyped network.
        untyped = ComponentNetwork()
        untyped.add_component(
            Component(id="a", task="double", args={"value": 21}, ports={"out": ("value",)})
        )
        untyped.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
        untyped.add_edge(
            Edge(source="a", source_field="value", target="b", target_arg="value")
        )
        data = untyped.to_dict()
        assert all("port_types" not in c for c in data["components"])
        assert network_from_dict(data).content_hash() == untyped.content_hash()
        # A typed network hashes differently from the untyped one.
        assert net.content_hash() != untyped.content_hash()

    def test_roundtrip_preserves_port_types(self) -> None:
        data = _typed_chain().to_dict()
        assert network_from_dict(data).to_dict() == data

    def test_hash_is_insertion_independent_with_types(self) -> None:
        first = _typed_chain()
        second = ComponentNetwork()
        second.add_component(
            Component(
                id="b", task="even",
                ports={"in": ("value",)},
                port_types={"in": {"value": "int"}},
            )
        )
        second.add_component(
            Component(
                id="a", task="double", args={"value": 21},
                ports={"out": ("value",)},
                port_types={"out": {"value": "int"}},
            )
        )
        second.add_edge(
            Edge(source="a", source_field="value", target="b", target_arg="value")
        )
        assert first.content_hash() == second.content_hash()

    def test_from_dict_rejects_non_mapping_port_types(self) -> None:
        with pytest.raises(NetworkError):
            network_from_dict({"components": [{"id": "a", "port_types": "nope"}]})


class TestRuntimeEnforcement:
    def test_flow_enforces_out_port_type(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="a", task="text", args={"value": 1},
                ports={"out": ("value",)},
                port_types={"out": {"value": "int"}},
            )
        )
        net.add_component(Component(id="b", task="sink", ports={"in": ("value",)}))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        result = run_flow(driver, net)
        assert result["ok"] is False
        assert "declared port type" in result["error"]

    def test_flow_enforces_in_port_type(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="a", task="text", args={"value": 1}, ports={"out": ("value",)})
        )
        net.add_component(
            Component(
                id="b", task="sink",
                ports={"in": ("value",)},
                port_types={"in": {"value": "int"}},
            )
        )
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        result = run_flow(driver, net)
        assert result["ok"] is False
        assert "declared port type" in result["error"]

    def test_flow_passes_when_type_matches(self, driver: CbpDriver) -> None:
        result = run_network(driver, _typed_chain())
        assert result["ok"] is True

    def test_stream_enforces_element_type(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="src", task="gen", args={"count": 3},
                ports={"out": ("value",)},
                port_types={"out": {"value": "int"}},
                stream=("value",),
            )
        )
        net.add_component(Component(id="sink", task="sink", ports={"in": ("value",)}))
        net.add_edge(
            Edge(source="src", source_field="value", target="sink", target_arg="value")
        )
        result = run_stream(driver, net)
        assert result["ok"] is False
        assert "declared port type" in result["error"]


class TestDesignIntegration:
    def test_design_rejects_incompatible_typed_edge(self) -> None:
        design = Design(
            id="typed-ports",
            network={
                "components": [
                    {
                        "id": "a", "task": "t", "args": {"v": 1},
                        "ports": {"out": ["v"]},
                        "port_types": {"out": {"v": "int"}},
                    },
                    {
                        "id": "b", "task": "u",
                        "ports": {"in": ["v"]},
                        "port_types": {"in": {"v": "str"}},
                    },
                ],
                "edges": [
                    {"source": "a", "source_field": "v", "target": "b", "target_arg": "v"}
                ],
            },
        )
        report = validate_design(design)
        assert not report.ok
        assert any("incompatible port types" in error for error in report.errors)
