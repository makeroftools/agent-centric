"""Tests for the n8n authoring adapter (SPEC-0008 Phase 2).

Offline and fixture-only: a ``design.v1`` ⇄ an n8n workflow projection round-trips
losslessly and stably, and every unsupported/inconsistent input is refused
fail-closed. n8n is never imported.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agent_centric.cbp.n8n_adapter import (
    N8nAdapterError,
    from_n8n,
    to_n8n,
)
from agent_centric.contracts.design import Design, RequestedComponent


def _network() -> dict[str, Any]:
    return {
        "components": [
            {"id": "a", "task": "double", "args": {"n": 2}},
            {"id": "b", "task": "negate", "args": {}, "verifier": "v"},
            {"id": "c", "task": "sum", "args": {}},
        ],
        "edges": [
            {"source": "a", "source_field": "n", "target": "c", "target_arg": "a"},
            {"source": "b", "source_field": "n", "target": "c", "target_arg": "b", "capacity": 2},
        ],
        "iips": [{"component": "a", "port": "n", "value": 7}],
    }


def _design() -> Design:
    return Design(
        id="d1",
        title="double then sum",
        network=_network(),
        components=(RequestedComponent("counter", "v1"),),
        metadata={"author": "operator"},
    )


class TestRoundTrip:
    def test_round_trip_preserves_design_hash(self) -> None:
        design = _design()
        assert from_n8n(to_n8n(design)).design_hash() == design.design_hash()

    def test_round_trip_preserves_every_field(self) -> None:
        design = _design()
        assert from_n8n(to_n8n(design)).to_dict() == design.to_dict()

    def test_export_is_deterministic(self) -> None:
        design = _design()
        assert to_n8n(design) == to_n8n(design)

    def test_re_export_is_stable(self) -> None:
        workflow = to_n8n(_design())
        assert to_n8n(from_n8n(workflow)) == workflow

    def test_connection_topology_mirrors_edges(self) -> None:
        workflow = to_n8n(_design())
        assert workflow["connections"] == {
            "a": {"main": [[{"node": "c", "type": "main", "index": 0}]]},
            "b": {"main": [[{"node": "c", "type": "main", "index": 0}]]},
        }

    def test_edge_and_iip_order_is_preserved(self) -> None:
        # Interleave edges by source so a naive grouping would reorder them.
        network = {
            "components": [
                {"id": "a", "task": "t", "args": {}},
                {"id": "b", "task": "t", "args": {}},
                {"id": "c", "task": "t", "args": {}},
                {"id": "d", "task": "t", "args": {}},
            ],
            "edges": [
                {"source": "a", "source_field": "o", "target": "c", "target_arg": "i"},
                {"source": "b", "source_field": "o", "target": "d", "target_arg": "i"},
                {"source": "a", "source_field": "o", "target": "d", "target_arg": "j"},
            ],
        }
        design = Design(id="order", network=network)
        assert from_n8n(to_n8n(design)).to_dict() == design.to_dict()


class TestRefusals:
    def test_unsupported_node_type_is_refused(self) -> None:
        workflow = to_n8n(_design())
        workflow["nodes"][0]["type"] = "n8n-nodes-base.noOp"
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)

    def test_connection_mismatch_is_refused(self) -> None:
        workflow = to_n8n(_design())
        workflow["connections"] = {}
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)

    def test_missing_meta_is_refused(self) -> None:
        workflow = to_n8n(_design())
        del workflow["meta"]
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)

    def test_unknown_projection_is_refused(self) -> None:
        workflow = to_n8n(_design())
        workflow["meta"]["cbp"]["projection"] = "something/else"
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)

    def test_unknown_component_key_is_refused(self) -> None:
        workflow = to_n8n(_design())
        workflow["nodes"][0]["parameters"]["cbp"]["bogus"] = 1
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)

    def test_duplicate_node_name_is_refused(self) -> None:
        workflow = to_n8n(_design())
        workflow["nodes"][1]["name"] = workflow["nodes"][0]["name"]
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)

    def test_non_permutation_edge_order_is_refused(self) -> None:
        workflow = to_n8n(_design())
        first = workflow["nodes"][2]["parameters"]["cbp"]["inputs"][0]
        first["cbp_order"] = 5
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)

    def test_subnet_is_refused(self) -> None:
        network = {
            "components": [
                {"id": "a", "task": "t", "args": {}, "subnet": {"components": [], "edges": []}}
            ],
            "edges": [],
        }
        with pytest.raises(N8nAdapterError):
            to_n8n(Design(id="d", network=network))

    def test_unknown_network_key_is_refused(self) -> None:
        network = {
            "components": [{"id": "a", "task": "t", "args": {}}],
            "edges": [],
            "extra": 1,
        }
        with pytest.raises(N8nAdapterError):
            to_n8n(Design(id="d", network=network))

    def test_invalid_imported_design_is_refused(self) -> None:
        workflow = to_n8n(_design())
        workflow["meta"]["cbp"]["id"] = ""  # Design requires a non-empty id
        with pytest.raises(N8nAdapterError):
            from_n8n(workflow)


class TestNoN8nDependency:
    def test_adapter_module_does_not_import_n8n(self) -> None:
        import agent_centric.cbp.n8n_adapter as adapter

        source = adapter.__doc__ or ""
        assert isinstance(source, str)
        import inspect

        module_source = inspect.getsource(adapter)
        assert "import n8n" not in module_source


class TestDemo:
    def test_demo_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        import examples.n8n_roundtrip as demo

        demo.main()
        out = capsys.readouterr().out
        assert "nodes=3 connections=2 edges=2 iips=1" in out
        assert "round_trip_stable=True" in out


def test_copy_is_not_mutated() -> None:
    design = _design()
    snapshot = copy.deepcopy(design.to_dict())
    to_n8n(design)
    from_n8n(to_n8n(design))
    assert design.to_dict() == snapshot
