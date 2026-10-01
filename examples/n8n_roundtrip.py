"""Demo: ``design.v1`` ⇄ n8n workflow round-trip (SPEC-0008 Phase 2).

Offline and fixture-only. A human authors a component graph in n8n; the adapter
projects it to a ``design.v1`` (and back) **losslessly** — the same design
re-imports to the same ``design_hash``, and re-exporting reproduces the same
canonical workflow bytes. n8n is never on the execution path.
"""

from __future__ import annotations

from typing import Any

from agent_centric.cbp.n8n_adapter import from_n8n, to_n8n
from agent_centric.contracts.design import Design, RequestedComponent

_NETWORK: dict[str, Any] = {
    "components": [
        {"id": "a", "task": "double", "args": {"n": 21}},
        {"id": "b", "task": "negate", "args": {}},
        {"id": "sum", "task": "sum", "args": {}},
    ],
    "edges": [
        {"source": "a", "source_field": "n", "target": "sum", "target_arg": "a"},
        {"source": "b", "source_field": "n", "target": "sum", "target_arg": "b"},
    ],
    "iips": [{"component": "a", "port": "n", "value": 21}],
}


def main() -> None:
    design = Design(
        id="demo",
        title="double then sum",
        network=_NETWORK,
        components=(RequestedComponent("counter", "v1"),),
        metadata={"author": "demo"},
    )
    workflow = to_n8n(design)
    print(
        f"nodes={len(workflow['nodes'])} connections={len(workflow['connections'])} "
        f"edges={len(_NETWORK['edges'])} iips={len(_NETWORK['iips'])}"
    )
    back = from_n8n(workflow)
    print(
        f"round_trip_stable={back.design_hash() == design.design_hash()} "
        f"design_hash={back.design_hash()[:12]}"
    )
    print(f"re_export_stable={to_n8n(back) == workflow}")


if __name__ == "__main__":
    main()
