"""Tests for design validation and compilation (SPEC-0008 Phase 1).

Deterministic and offline: a design is untrusted input, validated fail-closed and
compiled to a canonical, content-hashed network.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_centric.cbp.design import (
    DesignError,
    compile_design,
    validate_design,
)
from agent_centric.contracts.design import Design, RequestedComponent


def _network(
    components: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"components": components, "edges": edges or []}


_TWO_NODES = _network(
    [
        {"id": "a", "task": "double", "args": {"n": 2}},
        {"id": "b", "task": "negate", "args": {}},
    ],
    [{"source": "a", "source_field": "n", "target": "b", "target_arg": "m"}],
)


class TestValidateAndCompile:
    def test_valid_design_compiles(self) -> None:
        design = Design(id="d1", network=_TWO_NODES)
        report = validate_design(design)
        assert report.ok
        assert report.errors == ()
        assert report.component_ids == ("a", "b")

        compiled = compile_design(design)
        assert compiled.design_hash == design.design_hash()
        assert compiled.component_ids == ("a", "b")
        assert [step["task"] for step in compiled.plan] == ["double", "negate"]

    def test_design_hash_is_key_order_independent(self) -> None:
        reordered = _network(
            [
                {"task": "double", "args": {"n": 2}, "id": "a"},
                {"args": {}, "task": "negate", "id": "b"},
            ],
            [{"target_arg": "m", "target": "b", "source_field": "n", "source": "a"}],
        )
        assert Design(id="d1", network=_TWO_NODES).design_hash() == (
            Design(id="d1", network=reordered).design_hash()
        )

    def test_cycle_fails_closed(self) -> None:
        cyclic = _network(
            [
                {"id": "a", "task": "t", "args": {"x": 1}},
                {"id": "b", "task": "t", "args": {"y": 1}},
            ],
            [
                {"source": "a", "source_field": "x", "target": "b", "target_arg": "y"},
                {"source": "b", "source_field": "y", "target": "a", "target_arg": "x"},
            ],
        )
        design = Design(id="cyclic", network=cyclic)
        report = validate_design(design)
        assert not report.ok
        assert any("cycle" in error for error in report.errors)
        with pytest.raises(DesignError):
            compile_design(design)

    def test_unknown_output_field_fails_closed(self) -> None:
        broken = _network(
            [{"id": "a", "task": "t", "args": {"n": 1}}, {"id": "b", "task": "u", "args": {}}],
            [{"source": "a", "source_field": "missing", "target": "b", "target_arg": "m"}],
        )
        report = validate_design(Design(id="broken", network=broken))
        assert not report.ok
        assert any("output field" in error for error in report.errors)

    def test_empty_network_fails_closed(self) -> None:
        report = validate_design(Design(id="empty", network={"components": [], "edges": []}))
        assert not report.ok
        assert any("no components" in error for error in report.errors)

    def test_malformed_network_fails_closed(self) -> None:
        report = validate_design(Design(id="bad", network={}))
        assert not report.ok

    def test_component_limit_fails_closed(self) -> None:
        design = Design(id="wide", network=_TWO_NODES)
        report = validate_design(design, max_components=1)
        assert not report.ok
        assert any("exceeding" in error for error in report.errors)

    def test_dangling_edge_fails_closed(self) -> None:
        dangling = _network(
            [{"id": "a", "task": "t", "args": {"n": 1}}],
            [{"source": "a", "source_field": "n", "target": "ghost", "target_arg": "m"}],
        )
        report = validate_design(Design(id="dangling", network=dangling))
        assert not report.ok


class TestDesignContract:
    def test_from_dict_round_trips(self) -> None:
        design = Design(
            id="d1",
            network=_TWO_NODES,
            title="demo",
            components=(RequestedComponent("counter", "v1"),),
            metadata={"author": "operator"},
        )
        assert Design.from_dict(design.to_dict()) == design

    def test_schema_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Design(id="d1", network=_TWO_NODES, schema="design.v2")

    def test_duplicate_requested_names_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            Design(
                id="d1",
                network=_TWO_NODES,
                components=(
                    RequestedComponent("counter", "v1"),
                    RequestedComponent("counter", "v2"),
                ),
            )

    def test_missing_fields_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            Design.from_dict({"schema": "design.v1"})


class TestDemo:
    def test_demo_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        import examples.design_compile as demo

        demo.main()
        out = capsys.readouterr().out
        assert "valid=True" in out
        assert "cycle_valid=False" in out
