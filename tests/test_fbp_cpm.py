"""Tests for the deterministic, read-only FBP Critical Path Method."""

from __future__ import annotations

import pytest

from agent_centric.fbp.critical_path import CpmError, CpmNode, analyse_cpm


class TestDiamondNetwork:
    def test_critical_path_and_slack(self) -> None:
        nodes = [
            CpmNode("a", 3),
            CpmNode("b", 2, depends_on=("a",)),
            CpmNode("c", 1, depends_on=("a",)),
            CpmNode("d", 2, depends_on=("b", "c")),
        ]
        r = analyse_cpm(nodes)
        assert r.duration == 7
        assert set(r.critical_path) == {"a", "b", "d"}
        assert r.slack["a"] == 0 and r.slack["b"] == 0 and r.slack["d"] == 0
        assert r.slack["c"] == 1
        assert r.on_critical["c"] is False

    def test_deterministic_across_runs(self) -> None:
        nodes = [
            CpmNode("a", 3),
            CpmNode("b", 2, depends_on=("a",)),
            CpmNode("c", 1, depends_on=("a",)),
            CpmNode("d", 2, depends_on=("b", "c")),
        ]
        r1 = analyse_cpm(nodes)
        r2 = analyse_cpm(nodes)
        assert r1.to_dict() == r2.to_dict()


class TestLinearChain:
    def test_single_chain(self) -> None:
        nodes = [CpmNode("x", 2), CpmNode("y", 3, depends_on=("x",))]
        r = analyse_cpm(nodes)
        assert r.duration == 5
        assert list(r.critical_path) == ["x", "y"]
        assert r.slack == {"x": 0, "y": 0}


class TestFailClosed:
    def test_cycle_fails_closed(self) -> None:
        nodes = [
            CpmNode("a", 1, depends_on=("b",)),
            CpmNode("b", 1, depends_on=("a",)),
        ]
        with pytest.raises(CpmError, match="cycle"):
            analyse_cpm(nodes)

    def test_unknown_dependency_fails_closed(self) -> None:
        with pytest.raises(CpmError, match="unknown"):
            analyse_cpm([CpmNode("a", 1, depends_on=("ghost",))])

    def test_self_dependency_fails_closed(self) -> None:
        with pytest.raises(CpmError, match="itself"):
            analyse_cpm([CpmNode("a", 1, depends_on=("a",))])

    def test_duplicate_id_fails_closed(self) -> None:
        with pytest.raises(CpmError, match="duplicate"):
            analyse_cpm([CpmNode("a", 1), CpmNode("a", 2)])

    def test_non_positive_duration_fails_closed(self) -> None:
        with pytest.raises(CpmError, match="positive"):
            analyse_cpm([CpmNode("a", 0)])