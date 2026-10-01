"""Tests for Information-Packet flow over bounded connections (SPEC-0009 slice 2).

Deterministic and offline: port-declared networks run through the verified spine,
IPs flow from outports to inports, IIPs fill unwired inports, and overflow /
deadlock / missing fields / unverified steps all fail closed.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.driver import CbpDriver
from agent_centric.cbp.flow import BoundedConnection, FlowError, run_flow
from agent_centric.cbp.network import Component, ComponentNetwork, Edge, run_network


@pytest.fixture
def driver() -> object:
    d = CbpDriver()
    d.register("double", lambda value: value * 2, source_url="file:///tasks/double")
    d.register(
        "even",
        lambda value: isinstance(value, int) and value % 2 == 0,
        source_url="file:///tasks/even",
    )
    d.register("dict_out", lambda value: {"other": value}, source_url="file:///tasks/dict_out")
    d.configure(tasks=("double", "even", "dict_out"), verifiers=("even", "odd"))
    try:
        yield d
    finally:
        d.close()


def _chain() -> ComponentNetwork:
    net = ComponentNetwork()
    net.add_component(
        Component(id="a", task="double", args={"value": 21}, ports={"out": ("value",)})
    )
    net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
    net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
    return net


class TestFlowExecution:
    def test_ip_flows_from_outport_to_inport(self, driver: CbpDriver) -> None:
        result = run_network(driver, _chain())
        assert result["ok"] is True
        by_id = {r["id"]: r for r in result["results"]}
        assert by_id["a"]["value"] == 42
        assert by_id["a"]["verified"] is True
        assert by_id["b"]["value"] is True

    def test_is_deterministic(self, driver: CbpDriver) -> None:
        assert run_flow(driver, _chain()) == run_flow(driver, _chain())

    def test_iip_fills_an_unwired_inport(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="b", task="even", args={"value": 8}, ports={"in": ("value",)})
        )
        result = run_flow(driver, net)
        assert result["ok"] is True
        assert result["results"][0]["value"] is True

    def test_missing_outport_field_fails_closed(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="a", task="dict_out", args={"value": 1}, ports={"out": ("value",)})
        )
        net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        result = run_flow(driver, net)
        assert result["ok"] is False
        assert "no field" in result["error"]

    def test_unverified_step_fails_closed(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="a", task="double", args={"value": 21},
                verifier="odd", ports={"out": ("value",)},
            )
        )
        result = run_flow(driver, net)
        assert result["ok"] is False
        assert result["completed"] == 0

    def test_port_mode_requires_a_declared_source_outport(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="double", args={"value": 1}))
        net.add_component(Component(id="b", task="even", ports={"in": ("value",)}))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        result = run_flow(driver, net)
        assert result["ok"] is False
        assert "does not declare out-port" in result["error"]

    def test_step_limit_fails_closed(self, driver: CbpDriver) -> None:
        result = run_flow(driver, _chain(), step_limit=1)
        assert result["ok"] is False
        assert "step limit" in result["error"]


class TestBoundedConnection:
    def test_fifo_send_and_receive(self) -> None:
        connection = BoundedConnection("a", "out", "b", "in", 2)
        connection.send(1)
        connection.send(2)
        assert len(connection) == 2 and connection.is_full
        assert connection.receive() == 1
        assert connection.receive() == 2
        assert connection.is_empty

    def test_send_to_full_is_back_pressure(self) -> None:
        connection = BoundedConnection("a", "out", "b", "in", 1)
        connection.send("x")
        with pytest.raises(FlowError) as excinfo:
            connection.send("y")
        assert "back-pressure" in str(excinfo.value)

    def test_receive_from_empty_is_deadlock(self) -> None:
        connection = BoundedConnection("a", "out", "b", "in", 1)
        with pytest.raises(FlowError) as excinfo:
            connection.receive()
        assert "empty" in str(excinfo.value)

    def test_invalid_capacity_fails_closed(self) -> None:
        with pytest.raises(FlowError):
            BoundedConnection("a", "out", "b", "in", 0)
