"""Tests for read-only tree-audit reconstruction (audit as proof)."""

from __future__ import annotations

from pathlib import Path

from agent_centric.fbp import open_trajectory
from agent_centric.fbp.audit import reconstruct_chains


def _stores(tmp: Path, records: dict[str, list[dict]]) -> dict[str, object]:
    """Build a mapping of node -> TrajectoryStore from record dicts."""
    stores: dict[str, object] = {}
    for node, rows in records.items():
        tr = open_trajectory(tmp / f"{node}.db")
        for row in rows:
            tr.record(**row)
        stores[node] = tr
    return stores


class TestReconstructChains:
    def test_single_local_result(self, tmp_path: Path) -> None:
        stores = _stores(
            tmp_path,
            {
                "root": [
                    {
                        "correlation_id": "r1",
                        "kind": "result",
                        "node": "root",
                        "verified": True,
                        "value": 42,
                        "fingerprint": "fp",
                    }
                ]
            },
        )
        chains = reconstruct_chains(stores)  # type: ignore[arg-type]
        assert len(chains) == 1
        c = chains[0]
        assert c.correlation_id == "r1"
        assert c.verified is True
        assert c.terminal == "result"
        assert c.terminal_value == 42
        assert [e.node for e in c.events] == ["root"]

    def test_parent_child_chain_with_relay(self, tmp_path: Path) -> None:
        # child records a result; parent records a relay hop accepting it.
        stores = _stores(
            tmp_path,
            {
                "child": [
                    {
                        "correlation_id": "r1",
                        "kind": "result",
                        "node": "child",
                        "verified": True,
                        "value": 42,
                        "fingerprint": "fp",
                    }
                ],
                "root": [
                    {
                        "correlation_id": "r1",
                        "kind": "relay",
                        "node": "root",
                        "verified": True,
                        "value": 42,
                        "fingerprint": "relay|root|child",
                        "parent": "child",
                    }
                ],
            },
        )
        chains = reconstruct_chains(stores)  # type: ignore[arg-type]
        assert len(chains) == 1
        c = chains[0]
        assert c.verified is True
        assert c.terminal == "relay"
        assert c.terminal_value == 42
        # The chain: child's result, then the parent's relay.
        assert [(e.node, e.kind) for e in c.events] == [
            ("child", "result"),
            ("root", "relay"),
        ]

    def test_demoted_child_is_not_verified(self, tmp_path: Path) -> None:
        # A child claimed verified but the parent demoted it (correctness spine).
        stores = _stores(
            tmp_path,
            {
                "child": [
                    {
                        "correlation_id": "r1",
                        "kind": "result",
                        "node": "child",
                        "verified": True,
                        "value": 42,
                        "fingerprint": "fp",
                    }
                ],
                "root": [
                    {
                        "correlation_id": "r1",
                        "kind": "error",
                        "node": "root",
                        "verified": False,
                        "error": "child 'child' failed the parent's verifier",
                        "fingerprint": "fp",
                    }
                ],
            },
        )
        chains = reconstruct_chains(stores)  # type: ignore[arg-type]
        c = chains[0]
        assert c.verified is False
        assert c.terminal == "error"
        assert c.terminal_error is not None

    def test_deterministic(self, tmp_path: Path) -> None:
        records = {
            "child": [
                {
                    "correlation_id": "r1",
                    "kind": "result",
                    "node": "child",
                    "verified": True,
                    "value": 42,
                    "fingerprint": "fp",
                }
            ],
            "root": [
                {
                    "correlation_id": "r1",
                    "kind": "relay",
                    "node": "root",
                    "verified": True,
                    "value": 42,
                    "fingerprint": "relay|root|child",
                    "parent": "child",
                }
            ],
        }
        a = reconstruct_chains(_stores(tmp_path / "a", records))  # type: ignore[arg-type]
        b = reconstruct_chains(_stores(tmp_path / "b", records))  # type: ignore[arg-type]
        assert [c.to_dict() for c in a] == [c.to_dict() for c in b]

    def test_multiple_correlation_ids_ordered(self, tmp_path: Path) -> None:
        stores = _stores(
            tmp_path,
            {
                "root": [
                    {
                        "correlation_id": "r2",
                        "kind": "result",
                        "node": "root",
                        "verified": True,
                        "value": 2,
                        "fingerprint": "fp2",
                    },
                    {
                        "correlation_id": "r1",
                        "kind": "result",
                        "node": "root",
                        "verified": True,
                        "value": 1,
                        "fingerprint": "fp1",
                    },
                ]
            },
        )
        chains = reconstruct_chains(stores)  # type: ignore[arg-type]
        assert [c.correlation_id for c in chains] == ["r1", "r2"]