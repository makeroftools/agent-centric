"""Tests for composites and the external-ports boundary guard (SPEC-0009 slice 3).

A composite is a network used as a component: its body is a nested ``network.v1``
and it exposes **only** its declared external ports. An external-port map binds a
composite port to an interior ``component.port`` reference. Cross-boundary flow
that would reach past those ports (a dotted reference, an undeclared port, a
mismatched interior port, an over-deep nesting) fails closed.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.network import (
    Component,
    ComponentNetwork,
    Edge,
    NetworkError,
    network_from_dict,
)


def _inner() -> ComponentNetwork:
    inner = ComponentNetwork()
    inner.add_component(
        Component(id="src", task="double", args={"value": 21}, ports={"out": ("value",)})
    )
    inner.add_component(Component(id="sink", task="even", ports={"in": ("value",)}))
    inner.add_edge(Edge(source="src", source_field="value", target="sink", target_arg="value"))
    return inner


def _composite(**overrides: object) -> Component:
    fields: dict[str, object] = {
        "id": "c",
        "task": "",
        "ports": {"in": ("in",), "out": ("out",)},
        "external": {"in": "sink.value", "out": "src.value"},
        "subnet": _inner(),
    }
    fields.update(overrides)
    return Component(**fields)  # type: ignore[arg-type]


class TestCompositeValidation:
    def test_composite_with_external_ports_validates(self) -> None:
        net = ComponentNetwork()
        net.add_component(_composite())
        net.validate()  # must not raise

    def test_outer_edges_use_only_the_external_ports(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="feed", task="double", args={"value": 1}, ports={"out": ("value",)})
        )
        net.add_component(_composite())
        net.add_component(Component(id="take", task="even", ports={"in": ("value",)}))
        net.add_edge(Edge(source="feed", source_field="value", target="c", target_arg="in"))
        net.add_edge(Edge(source="c", source_field="out", target="take", target_arg="value"))
        net.validate()  # must not raise

    def test_undeclared_boundary_port_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="feed", task="double", args={"value": 1}, ports={"out": ("value",)})
        )
        net.add_component(_composite())
        # "value" is an interior name, not one of the composite's external ports.
        net.add_edge(Edge(source="feed", source_field="value", target="c", target_arg="value"))
        with pytest.raises(NetworkError, match="no in-port"):
            net.validate()

    def test_external_ports_must_match_declared_ports(self) -> None:
        net = ComponentNetwork()
        net.add_component(_composite(external={"in": "sink.value"}))
        with pytest.raises(NetworkError, match="exactly match"):
            net.validate()

    def test_external_without_subnet_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            Component(id="c", task="", ports={"in": ("in",)}, external={"in": "sink.value"})
        )
        with pytest.raises(NetworkError, match="without a subnet body"):
            net.validate()

    def test_external_unknown_interior_component_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            _composite(external={"in": "ghost.value", "out": "src.value"})
        )
        with pytest.raises(NetworkError, match="unknown interior component"):
            net.validate()

    def test_external_wrong_direction_fails_closed(self) -> None:
        net = ComponentNetwork()
        # "out" must map to an interior *out*-port, but sink only has an in-port.
        net.add_component(
            _composite(external={"in": "sink.value", "out": "sink.value"})
        )
        with pytest.raises(NetworkError, match="out-port"):
            net.validate()

    def test_bad_reference_shape_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(
            _composite(external={"in": "sink", "out": "src.value"})
        )
        with pytest.raises(NetworkError, match="interior 'component.port'"):
            net.validate()


class TestBoundaryGuard:
    def test_dotted_component_id_fails_closed(self) -> None:
        with pytest.raises(NetworkError, match="must not contain"):
            ComponentNetwork().add_component(Component(id="c.inner", task="t"))

    def test_dotted_edge_reference_fails_closed(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="t", ports={"out": ("v",)}))
        net.add_component(Component(id="b", task="u", ports={"in": ("v",)}))
        net.add_edge(Edge(source="a", source_field="v", target="b.inner", target_arg="v"))
        with pytest.raises(NetworkError, match="dotted component references"):
            net.validate()

    def test_nested_subnet_is_validated_recursively(self) -> None:
        bad_inner = ComponentNetwork()
        bad_inner.add_component(
            Component(id="src", task="t", ports={"out": ("value",)})
        )
        bad_inner.add_component(Component(id="sink", task="u", ports={"in": ("value",)}))
        bad_inner.add_edge(
            Edge(source="src", source_field="value", target="ghost", target_arg="value")
        )
        net = ComponentNetwork()
        net.add_component(_composite(subnet=bad_inner))
        with pytest.raises(NetworkError, match="not a component"):
            net.validate()

    def test_nesting_depth_is_bounded(self) -> None:
        node: dict[str, object] = {"components": [{"id": "leaf", "task": "t"}]}
        for _ in range(12):
            node = {
                "components": [
                    {
                        "id": "c",
                        "task": "",
                        "ports": {"in": ["in"], "out": ["out"]},
                        "external": {"in": "c.in", "out": "c.out"},
                        "subnet": node,
                    }
                ]
            }
        with pytest.raises(NetworkError, match="depth limit"):
            network_from_dict(node)


class TestCompositeDocument:
    def test_roundtrip_preserves_subnet_and_external(self) -> None:
        data = ComponentNetwork()
        data.add_component(_composite())
        payload = data.to_dict()
        restored = network_from_dict(payload)
        assert restored.to_dict() == payload

    def test_content_hash_covers_the_subnet_body(self) -> None:
        first = ComponentNetwork()
        first.add_component(_composite())
        changed_inner = _inner()
        changed_inner.add_component(
            Component(id="src", task="double", args={"value": 99}, ports={"out": ("value",)})
        )
        second = ComponentNetwork()
        second.add_component(_composite(subnet=changed_inner))
        assert first.content_hash() != second.content_hash()

    def test_content_hash_is_insertion_independent(self) -> None:
        first = ComponentNetwork()
        first.add_component(_composite())
        second = ComponentNetwork()
        second.add_component(_composite())
        assert first.content_hash() == second.content_hash()

    def test_non_mapping_subnet_fails_closed(self) -> None:
        with pytest.raises(NetworkError, match="'subnet' must be a mapping"):
            network_from_dict(
                {"components": [{"id": "c", "subnet": "nope"}]}
            )

    def test_non_mapping_external_fails_closed(self) -> None:
        with pytest.raises(NetworkError, match="'external' must be a mapping"):
            network_from_dict(
                {"components": [{"id": "c", "external": "nope"}]}
            )
