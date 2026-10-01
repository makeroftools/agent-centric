"""Tests for repeated-activation streaming (SPEC-0009 slice 3).

A component may declare a streaming out-port; each element of its verified
output becomes an IP. A component re-activates once per input IP, so a downstream
process consumes a long/unbounded stream. A full bounded connection
deterministically suspends the producer until a consumer drains it. Deadlocks and
runaway sources fail closed; determinism is preserved.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.driver import CbpDriver
from agent_centric.cbp.network import (
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
    network_from_dict,
    run_network,
)
from agent_centric.cbp.stream import run_stream


@pytest.fixture
def driver() -> object:
    d = CbpDriver()
    d.register("gen", lambda count: list(range(count)), source_url="file:///tasks/gen")
    d.register("inc", lambda value: value + 1, source_url="file:///tasks/inc")
    d.register("sum2", lambda a, b: a + b, source_url="file:///tasks/sum2")
    d.register("nothing", lambda: [], source_url="file:///tasks/nothing")
    d.register("scalar", lambda: 5, source_url="file:///tasks/scalar")
    d.configure(tasks=("gen", "inc", "sum2", "nothing", "scalar"))
    try:
        yield d
    finally:
        d.close()


def _stream_chain(count: int = 5, capacity: int = 1) -> ComponentNetwork:
    net = ComponentNetwork()
    net.add_component(
        Component(
            id="src", task="gen", args={"count": count},
            ports={"out": ("value",)}, stream=("value",),
        )
    )
    net.add_component(
        Component(id="inc", task="inc", ports={"in": ("value",), "out": ("value",)})
    )
    net.add_edge(
        Edge(
            source="src", source_field="value", target="inc", target_arg="value",
            capacity=capacity,
        )
    )
    return net


def _values(result: dict[str, object], cid: str) -> list[object]:
    return [r["value"] for r in result["results"] if r["id"] == cid]  # type: ignore[index]


class TestStreamingExecution:
    def test_source_streams_and_consumer_repeats(self, driver: CbpDriver) -> None:
        result = run_network(driver, _stream_chain(5))
        assert result["ok"] is True
        assert _values(result, "src") == [[0, 1, 2, 3, 4]]
        assert _values(result, "inc") == [1, 2, 3, 4, 5]

    def test_back_pressure_with_capacity_one(self, driver: CbpDriver) -> None:
        # capacity 1 forces the producer to suspend after every single packet.
        result = run_network(driver, _stream_chain(4, capacity=1))
        assert result["ok"] is True
        assert _values(result, "inc") == [1, 2, 3, 4]

    def test_capacity_does_not_change_the_result(self, driver: CbpDriver) -> None:
        one = run_network(driver, _stream_chain(6, capacity=1))
        two = run_network(driver, _stream_chain(6, capacity=2))
        assert one["ok"] and two["ok"]
        assert _values(one, "inc") == _values(two, "inc")

    def test_streaming_is_deterministic(self, driver: CbpDriver) -> None:
        assert run_stream(driver, _stream_chain(5)) == run_stream(
            driver, _stream_chain(5)
        )

    def test_fan_out_broadcasts_every_packet(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="src", task="gen", args={"count": 3},
                ports={"out": ("value",)}, stream=("value",),
            )
        )
        for cid in ("a", "b"):
            net.add_component(
                Component(id=cid, task="inc", ports={"in": ("value",), "out": ("value",)})
            )
            net.add_edge(
                Edge(source="src", source_field="value", target=cid, target_arg="value")
            )
        result = run_network(driver, net)
        assert result["ok"] is True
        assert _values(result, "a") == [1, 2, 3]
        assert _values(result, "b") == [1, 2, 3]

    def test_non_streaming_chain_still_repeats(self, driver: CbpDriver) -> None:
        # A non-streaming out-port carries one scalar IP per activation, so a
        # downstream consumer still activates once per upstream packet.
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="src", task="gen", args={"count": 2},
                ports={"out": ("value",)}, stream=("value",),
            )
        )
        net.add_component(
            Component(id="inc", task="inc", ports={"in": ("value",), "out": ("value",)})
        )
        net.add_component(
            Component(id="tail", task="inc", ports={"in": ("value",), "out": ("value",)})
        )
        net.add_edge(Edge(source="src", source_field="value", target="inc", target_arg="value"))
        net.add_edge(Edge(source="inc", source_field="value", target="tail", target_arg="value"))
        result = run_network(driver, net)
        assert result["ok"] is True
        assert _values(result, "tail") == [2, 3]

    def test_empty_stream_activates_only_the_source(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="src", task="nothing", ports={"out": ("value",)}, stream=("value",))
        )
        net.add_component(
            Component(id="sink", task="inc", ports={"in": ("value",), "out": ("value",)})
        )
        net.add_edge(Edge(source="src", source_field="value", target="sink", target_arg="value"))
        result = run_network(driver, net)
        assert result["ok"] is True
        assert [r["id"] for r in result["results"]] == ["src"]


class TestStreamingFailClosed:
    def test_join_starvation_is_a_deadlock(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="src", task="gen", args={"count": 3},
                ports={"out": ("value",)}, stream=("value",),
            )
        )
        net.add_component(
            Component(id="empty", task="nothing", ports={"out": ("value",)}, stream=("value",))
        )
        net.add_component(
            Component(id="add", task="sum2", ports={"in": ("a", "b"), "out": ("value",)})
        )
        net.add_edge(Edge(source="src", source_field="value", target="add", target_arg="a"))
        net.add_edge(Edge(source="empty", source_field="value", target="add", target_arg="b"))
        result = run_network(driver, net)
        assert result["ok"] is False
        assert "deadlock" in result["error"]

    def test_runaway_source_exceeds_the_activation_limit(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(
                id="src", task="gen", args={"count": 1000},
                ports={"out": ("value",)}, stream=("value",),
            )
        )
        net.add_component(
            Component(id="sink", task="inc", ports={"in": ("value",), "out": ("value",)})
        )
        net.add_edge(Edge(source="src", source_field="value", target="sink", target_arg="value"))
        result = run_stream(driver, net, step_limit=5)
        assert result["ok"] is False
        assert "activation limit" in result["error"]

    def test_non_iterable_stream_fails_closed(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="src", task="scalar", ports={"out": ("value",)}, stream=("value",))
        )
        net.add_component(
            Component(id="sink", task="inc", ports={"in": ("value",), "out": ("value",)})
        )
        net.add_edge(Edge(source="src", source_field="value", target="sink", target_arg="value"))
        result = run_network(driver, net)
        assert result["ok"] is False
        assert "not iterable" in result["error"]

    def test_two_edges_into_one_inport_fails_closed(self, driver: CbpDriver) -> None:
        net = ComponentNetwork()
        for cid in ("a", "b"):
            net.add_component(
                Component(
                    id=cid, task="gen", args={"count": 1},
                    ports={"out": ("value",)}, stream=("value",),
                )
            )
            net.add_edge(
                Edge(source=cid, source_field="value", target="sink", target_arg="value")
            )
        net.add_component(
            Component(id="sink", task="inc", ports={"in": ("value",), "out": ("value",)})
        )
        result = run_stream(driver, net)
        assert result["ok"] is False
        assert "more than one incoming connection" in result["error"]


class TestStreamValidation:
    def test_stream_port_must_be_declared(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="a", task="t", ports={"out": ("other",)}, stream=("value",))
        )
        with pytest.raises(NetworkError, match="not a declared out-port"):
            net.validate()

    def test_stream_roundtrips_in_the_document(self) -> None:
        payload = _stream_chain(3).to_dict()
        assert payload["components"][1]["stream"] == ["value"]
        assert network_from_dict(payload).to_dict() == payload
