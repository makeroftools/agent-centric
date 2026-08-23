"""Tests for Component Networks (``fbp/network.py``).

The deterministic visual-programming core: a directed graph of components wired
by data-flow edges, compiled to an ordered FBP ``run`` plan. These tests prove
the DAG validation, the deterministic topological order, the data-flow wiring,
and the fail-closed behavior — all pure and offline.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.network import (
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
    network_from_dict,
    run_network,
)


def _two_stage_network() -> ComponentNetwork:
    """double(21)=42 -> even(42) verifies; a linear two-component net."""
    net = ComponentNetwork()
    net.add_component(Component(id="a", task="double", args={"value": 21}))
    net.add_component(Component(id="b", task="even", args={}))
    net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
    return net


class TestValidation:
    def test_valid_network_passes(self) -> None:
        _two_stage_network().validate()  # must not raise

    def test_missing_source_fails(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="b", task="even"))
        net.add_edge(Edge(source="ghost", source_field="x", target="b", target_arg="value"))
        with pytest.raises(NetworkError):
            net.validate()

    def test_missing_target_fails(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="double", args={"value": 1}))
        net.add_edge(Edge(source="a", source_field="value", target="ghost", target_arg="x"))
        with pytest.raises(NetworkError):
            net.validate()

    def test_self_loop_fails(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="double", args={"value": 1}))
        net.add_edge(Edge(source="a", source_field="value", target="a", target_arg="value"))
        with pytest.raises(NetworkError):
            net.validate()

    def test_cycle_fails(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="t", args={"x": 1}))
        net.add_component(Component(id="b", task="t", args={"x": 1}))
        net.add_edge(Edge(source="a", source_field="x", target="b", target_arg="x"))
        net.add_edge(Edge(source="b", source_field="x", target="a", target_arg="x"))
        with pytest.raises(NetworkError):
            net.validate()


class TestCompile:
    def test_compile_is_topological(self) -> None:
        steps = _two_stage_network().compile()
        assert [s["task"] for s in steps] == ["double", "even"]

    def test_data_flow_wired_into_args(self) -> None:
        steps = _two_stage_network().compile()
        # The edge feeds b.value from a.value (21).
        assert steps[1]["args"]["value"] == 21

    def test_compile_order_deterministic_regardless_of_insertion(self) -> None:
        # Add the same components in a different order; the compile order is
        # still a then b (topological, ties broken by id).
        net = ComponentNetwork()
        net.add_component(Component(id="b", task="even"))
        net.add_component(Component(id="a", task="double", args={"value": 21}))
        net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
        assert [s["task"] for s in net.compile()] == ["double", "even"]

    def test_unknown_source_field_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="double", args={"value": 21}))
        net.add_component(Component(id="b", task="even"))
        net.add_edge(Edge(source="a", source_field="nope", target="b", target_arg="value"))
        with pytest.raises(NetworkError):
            net.compile()

    def test_verifier_and_child_carried(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="a", task="double", args={"value": 21}, verifier="even")
        )
        steps = net.compile()
        assert steps[0]["verifier"] == "even"


class TestSerialization:
    def test_roundtrip(self) -> None:
        net = _two_stage_network()
        data = net.to_dict()
        rebuilt = network_from_dict(data)
        assert rebuilt.to_dict() == data

    def test_from_dict_rejects_malformed(self) -> None:
        with pytest.raises(NetworkError):
            network_from_dict({"components": "nope"})


class TestRunNetwork:
    def test_runs_through_driver(self) -> None:
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        driver.configure(tasks=("double", "even"), verifiers=("even",))
        try:
            result = run_network(driver, _two_stage_network())
            assert result["ok"] is True
            assert result["results"][0]["verified"] is True
            assert result["results"][0]["value"] == 42
        finally:
            driver.close()

    def test_invalid_network_fails_closed(self) -> None:
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        try:
            net = ComponentNetwork()
            net.add_component(Component(id="a", task="double", args={"value": 1}))
            net.add_edge(Edge(source="a", source_field="value", target="ghost", target_arg="x"))
            result = run_network(driver, net)
            assert result["ok"] is False
            assert "error" in result
        finally:
            driver.close()

    def test_true_dataflow_sum_of_two_doubles(self) -> None:
        """A downstream component consumes the *computed output* of its
        upstreams (not their args): double(21)=42 and double(10)=20 feed
        sum(a,b)=62. This is real dataflow, not constant propagation."""
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("sum", lambda a, b: a + b, source_url="file:///tasks/sum")
        driver.configure(tasks=("double", "sum"))
        net = ComponentNetwork()
        net.add_component(Component(id="d1", task="double", args={"value": 21}))
        net.add_component(Component(id="d2", task="double", args={"value": 10}))
        net.add_component(Component(id="s", task="sum", args={}))
        net.add_edge(Edge(source="d1", source_field="value", target="s", target_arg="a"))
        net.add_edge(Edge(source="d2", source_field="value", target="s", target_arg="b"))
        try:
            result = run_network(driver, net)
            assert result["ok"] is True
            by_id = {r["id"]: r for r in result["results"]}
            assert by_id["d1"]["value"] == 42
            assert by_id["d2"]["value"] == 20
            assert by_id["s"]["value"] == 62
        finally:
            driver.close()

    def test_unverified_step_fails_closed(self) -> None:
        """A component that fails verification stops the network."""
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("odd", lambda value: isinstance(value, int) and value % 2 == 1)
        driver.configure(tasks=("double",))
        net = ComponentNetwork()
        # double(21)=42 is even; the odd verifier on the component demotes it.
        net.add_component(Component(id="a", task="double", args={"value": 21}, verifier="odd"))
        try:
            result = run_network(driver, net)
            assert result["ok"] is False
            assert result["completed"] == 0
        finally:
            driver.close()