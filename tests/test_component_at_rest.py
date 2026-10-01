"""Tests for "a component at rest has no edges" (SPEC-0009 §2).

Dependency is an edge, and edges exist ONLY in a network document. A
``component.v1`` manifest is a self-contained package with no edges: it owns its
state and declares its ports, but never wires itself to anything. A manifest
that tries to carry an edge is rejected fail-closed.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_centric.cbp.network import Component, ComponentNetwork, Edge
from agent_centric.contracts.component import ComponentManifest

_FORBIDDEN = ("edges", "wires", "connections")


def _manifest_dict(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": "component.v1",
        "name": "c",
        "kind": "atomic",
        "implements": ["cbp.runtime.v1"],
        "entry": {"runtime": "python", "entrypoint": "m:f"},
    }
    base.update(extra)
    return base


class TestComponentAtRest:
    def test_manifest_has_no_edge_fields(self) -> None:
        manifest = ComponentManifest.from_dict(_manifest_dict())
        serialized = manifest.to_dict()
        assert not any(key in serialized for key in _FORBIDDEN)

    @pytest.mark.parametrize("key", _FORBIDDEN)
    def test_manifest_carrying_an_edge_fails_closed(self, key: str) -> None:
        with pytest.raises(ValueError) as excinfo:
            ComponentManifest.from_dict(_manifest_dict(**{key: [{"source": "a"}]}))
        assert "no edges" in str(excinfo.value)

    def test_dependency_appears_only_in_a_network(self) -> None:
        net = ComponentNetwork()
        net.add_component(Component(id="a", task="t"))
        net.add_component(Component(id="b", task="u"))
        # Two components at rest: no dependency exists until a network wires them.
        assert net.edges() == ()
        net.add_edge(Edge(source="a", source_field="v", target="b", target_arg="v"))
        assert len(net.edges()) == 1
