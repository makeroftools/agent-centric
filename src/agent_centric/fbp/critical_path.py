"""Deterministic, read-only Critical Path Method (CPM) for the FBP foundation.

CPM is a first-class, deterministic, read-only observational tool (see
``spec.md`` / ``protocol.md``). This module provides the **pure** analysis over
a DAG of activities (nodes with durations and dependency edges). It never
mutates the input and is fully deterministic: identical input produces
identical results.

Semantics (classic CPM/PDM):

- **Forward pass** computes each activity's early start/finish: a node starts
  only after all its dependencies finish (max of their early finishes; 0 if
  none). ``early_finish = early_start + duration``.
- **Backward pass** computes each activity's late start/finish: a node must
  finish before the earliest of its dependents' late starts (the project end
  if it has none). ``late_finish`` is that bound; ``late_start = late_finish -
  duration``.
- **Slack / float** = ``late_start - early_start``. An activity with zero
  slack lies on the critical path.
- **Critical path** = the chain(s) of zero-slack activities that determine the
  minimum feasible duration of the whole.

Determinism:

- Dependencies are ordered; topological ordering tie-breaks by node id, so the
  traversal (and every slack/start value) is stable across runs.
- A cyclic graph **fails closed** (raises ``CpmError``) rather than producing
  ambiguous results.
- An unknown dependency fails closed.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any


class CpmError(ValueError):
    """A CPM input violated the DAG contract (fail-closed)."""


@dataclass(frozen=True)
class CpmNode:
    """An activity in the CPM network.

    Attributes:
        id: A stable, unique activity id (never auto-generated).
        duration: The activity's duration / cost. Must be positive.
        depends_on: The ids this activity depends on (must complete first).
        name: Optional human-readable label (defaults to ``id`` when analysed).
    """

    id: str
    duration: int | float
    depends_on: tuple[str, ...] = ()
    name: str | None = None

    @property
    def deps(self) -> tuple[str, ...]:
        return self.depends_on


@dataclass(frozen=True)
class CpmAnalysis:
    """The deterministic CPM result.

    Attributes:
        critical_path: The ids of the zero-slack activities forming the longest
            chain that dominates the project end, in dependency order.
        duration: The minimum feasible project duration.
        early_start / early_finish / late_start / late_finish / slack:
            per-node values keyed by node id.
        on_critical: node id -> whether it lies on the critical path.
    """

    critical_path: tuple[str, ...]
    duration: int | float
    early_start: dict[str, int | float]
    early_finish: dict[str, int | float]
    late_start: dict[str, int | float]
    late_finish: dict[str, int | float]
    slack: dict[str, int | float]
    on_critical: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready rendering of the analysis (for the wire)."""
        return {
            "critical_path": list(self.critical_path),
            "duration": self.duration,
            "early_start": self.early_start,
            "early_finish": self.early_finish,
            "late_start": self.late_start,
            "late_finish": self.late_finish,
            "slack": self.slack,
            "on_critical": self.on_critical,
        }


def analyse_cpm(nodes: list[CpmNode]) -> CpmAnalysis:
    """Compute a deterministic critical-path analysis over a DAG of activities.

    Args:
        nodes: The activities of the network. Ids must be unique and durations
            positive; every ``depends_on`` id must exist.

    Raises:
        CpmError: On a duplicate id, non-positive duration, unknown dependency,
            self-dependency, or a cycle (all fail closed).
    """
    by_id: dict[str, CpmNode] = {}
    for node in nodes:
        if node.id in by_id:
            raise CpmError(f"duplicate node id {node.id!r}")
        if node.duration <= 0:
            raise CpmError(f"node {node.id!r} must have a positive duration")
        by_id[node.id] = node

    # Validate dependencies up front (fail closed).
    for node in nodes:
        for dep in node.deps:
            if dep == node.id:
                raise CpmError(f"node {node.id!r} depends on itself")
            if dep not in by_id:
                raise CpmError(
                    f"node {node.id!r} depends on unknown {dep!r}"
                )

    order = _topo_sort(nodes, by_id)

    # Forward pass: early start = max early finish of deps; early finish = +duration.
    es: dict[str, int | float] = {}
    ef: dict[str, int | float] = {}
    for nid in order:
        node = by_id[nid]
        start = max((ef[d] for d in node.deps), default=0)
        es[nid] = start
        ef[nid] = start + node.duration
    project_end = max(ef.values()) if ef else 0

    # Build dependent adjacency.
    dependents: dict[str, list[str]] = {nid: [] for nid in by_id}
    for node in nodes:
        for d in node.deps:
            dependents[d].append(node.id)

    # Backward pass: late finish = min late start of dependents (else project end).
    ls: dict[str, int | float] = {}
    lf: dict[str, int | float] = {}
    for nid in reversed(order):
        end = min((ls[dep] for dep in dependents[nid]), default=project_end)
        lf[nid] = end
        ls[nid] = end - by_id[nid].duration

    slack = {nid: ls[nid] - es[nid] for nid in order}
    on = {nid: slack[nid] == 0 for nid in order}

    critical = _critical_chain(order, by_id, es, ef, on)

    return CpmAnalysis(
        critical_path=tuple(critical),
        duration=project_end,
        early_start=es,
        early_finish=ef,
        late_start=ls,
        late_finish=lf,
        slack=slack,
        on_critical=on,
    )


def _topo_sort(nodes: list[CpmNode], by_id: dict[str, CpmNode]) -> list[str]:
    """A deterministic topological order (Kahn, tie-broken by sorted id)."""
    indegree = {nid: 0 for nid in by_id}
    adj: dict[str, list[str]] = {nid: [] for nid in by_id}
    for node in nodes:
        for d in node.deps:
            indegree[node.id] += 1
            adj[d].append(node.id)
    ready = [nid for nid, deg in indegree.items() if deg == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        nid = heapq.heappop(ready)
        order.append(nid)
        for m in sorted(adj[nid]):
            indegree[m] -= 1
            if indegree[m] == 0:
                heapq.heappush(ready, m)
    if len(order) != len(by_id):
        raise CpmError("graph contains a cycle (not a DAG)")
    return order


def _critical_chain(
    order: list[str],
    by_id: dict[str, CpmNode],
    es: dict[str, int | float],
    ef: dict[str, int | float],
    on: dict[str, bool],
) -> list[str]:
    """The critical path: the longest zero-slack chain, in dependency order.

    DP over the topological order: for each critical node, ``best_len`` is the
    length of the longest critical chain ending at it, and ``prev`` its
    predecessor on that chain (a zero-slack dependency whose early finish ties
    this node's early start). The overall critical path is the critical chain
    with the largest total length. Deterministic: order is stable and ties
    break by sorted id.
    """
    length: dict[str, int | float] = {}
    prev: dict[str, str | None] = {nid: None for nid in order}
    for nid in order:
        if not on[nid]:
            continue
        node = by_id[nid]
        best_len: int | float = node.duration
        best_pred: str | None = None
        for d in sorted(node.deps):
            if not on[d]:
                continue
            if ef[d] == es[nid] and length[d] + node.duration >= best_len:
                best_len = length[d] + node.duration
                best_pred = d
        length[nid] = best_len
        prev[nid] = best_pred
    # Reconstruct from the critical node with the largest path length.
    if not length:
        return []
    end = max(length, key=lambda nid: length[nid])
    chain: list[str] = []
    cur: str | None = end
    while cur is not None:
        chain.append(cur)
        cur = prev[cur]
    chain.reverse()
    return chain