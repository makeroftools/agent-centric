"""Tests for first-class per-port IIP documents (SPEC-0009 slice 3).

An Initial Information Packet is bound to a named inport in the ``network.v1``
document, is content-hashed with the network, and fills an unwired inport at run
time. Conflicts (duplicate IIP, undeclared inport, IIP + incoming connection) and
malformed documents fail closed.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.design import validate_design
from agent_centric.cbp.driver import CbpDriver
from agent_centric.cbp.network import (
    IIP,
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
    network_from_dict,
    run_network,
)
from agent_centric.contracts.design import Design


@pytest.fixture
def driver() -> object:
    d = CbpDriver()
    d.register("double", lambda value: value * 2, source_url="file:///tasks/double")
    d.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
    d.configure(tasks=("double", "even"), verifiers=("even", "odd"))
    try:
        yield d
    finally:
        d.close()


def _iip_network(value: int = 8) -> ComponentNetwork:
    net = ComponentNetwork()
    net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
    net.add_iip(IIP(component="b", port="value", value=value))
    return net


class TestIIPExecution:
    def test_iip_fills_an_unwired_inport(self, driver: CbpDriver) -> None:
        result = run_network(driver, _iip_network(8))
        assert result["ok"] is True
        assert result["results"][0]["value"] is True

    def test_iip_overrides_the_args_template(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="b", task="double", args={"value": 1}, ports={"in": ("value",)})
        )
        net.add_iip(IIP(component="b", port="value", value=21))
        result = run_network(driver, net)
        assert result["ok"] is True
        assert result["results"][0]["value"] == 42

    def test_iip_flow_is_deterministic(self, driver: CbpDriver) -> None:
        assert run_network(driver, _iip_network(6)) == run_network(driver, _iip_network(6))


class TestIIPValidation:
    def test_duplicate_iip_fails_closed(self) -> None:
        net = _iip_network()
        net.add_iip(IIP(component="b", port="value", value=1))
        with pytest.raises(NetworkError, match="more than once"):
            net.validate()

    def test_iip_on_unknown_component_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_iip(IIP(component="ghost", port="value", value=1))
        with pytest.raises(NetworkError, match="not a component"):
            net.validate()

    def test_iip_on_undeclared_inport_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="b", task="even"))
        net.add_iip(IIP(component="b", port="value", value=1))
        with pytest.raises(NetworkError, match="declares no in-port"):
            net.validate()

    def test_iip_conflicts_with_incoming_connection(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="a", task="double", args={"value": 1}, ports={"out": ("value",)})
        )
        net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        net.add_iip(IIP(component="b", port="value", value=2))
        with pytest.raises(NetworkError, match="both an IIP and an"):
            net.validate()

    def test_conflicting_iip_fails_a_run_closed(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="a", task="double", args={"value": 1}, ports={"out": ("value",)})
        )
        net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        net.add_iip(IIP(component="b", port="value", value=2))
        result = run_network(driver, net)
        assert result["ok"] is False
        assert "both an IIP and an" in result["error"]


class TestIIPDocument:
    def test_roundtrip_preserves_iips(self) -> None:
        data = _iip_network(8).to_dict()
        assert data["iips"] == [{"component": "b", "port": "value", "value": 8}]
        assert network_from_dict(data).to_dict() == data

    def test_content_hash_covers_iip_values(self) -> None:
        assert _iip_network(8).content_hash() != _iip_network(9).content_hash()

    def test_hash_is_independent_of_iip_insertion_order(self) -> None:
        first = ComponentNetwork()
        first.add_component(Component(id="b", task="even", ports={"in": ("x", "y")}))
        first.add_iip(IIP(component="b", port="x", value=1))
        first.add_iip(IIP(component="b", port="y", value=2))
        second = ComponentNetwork()
        second.add_component(Component(id="b", task="even", ports={"in": ("x", "y")}))
        second.add_iip(IIP(component="b", port="y", value=2))
        second.add_iip(IIP(component="b", port="x", value=1))
        assert first.content_hash() == second.content_hash()

    def test_non_list_iips_fail_closed(self) -> None:
        with pytest.raises(NetworkError, match="'iips' must be a list"):
            network_from_dict({"components": [{"id": "b"}], "iips": "nope"})

    def test_non_dict_iip_fails_closed(self) -> None:
        with pytest.raises(NetworkError, match="each IIP must be a dict"):
            network_from_dict({"components": [{"id": "b"}], "iips": ["nope"]})


class TestIIPDesignIntegration:
    def test_design_rejects_iip_on_undeclared_inport(self) -> None:
        design = Design(
            id="bad-iip",
            network={
                "components": [{"id": "b", "task": "u", "ports": {"in": ["value"]}}],
                "iips": [{"component": "b", "port": "other", "value": 1}],
            },
        )
        report = validate_design(design)
        assert not report.ok
        assert any("in-port" in error for error in report.errors)
