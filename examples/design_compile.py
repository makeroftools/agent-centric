"""End-to-end demo of design validation + compilation (SPEC-0008 Phase 1).

Offline, deterministic. A human (or an agent, or n8n) composes a `design.v1`
document — a `network.v1` graph plus requested component refs. The harness
validates it fail-closed and compiles it to a canonical, content-hashed network.

Nothing here executes a component: component availability is verified later, at
pin time (SPEC-0007 §4).
"""

from __future__ import annotations

from agent_centric.cbp.design import compile_design, validate_design
from agent_centric.contracts.design import Design, RequestedComponent

_NETWORK = {
    "components": [
        {"id": "double", "task": "double", "args": {"n": 21}},
        {"id": "negate", "task": "negate", "args": {}},
    ],
    "edges": [
        {"source": "double", "source_field": "n", "target": "negate", "target_arg": "value"}
    ],
}

_CYCLIC = {
    "components": [
        {"id": "a", "task": "t", "args": {"x": 1}},
        {"id": "b", "task": "t", "args": {"y": 1}},
    ],
    "edges": [
        {"source": "a", "source_field": "x", "target": "b", "target_arg": "y"},
        {"source": "b", "source_field": "y", "target": "a", "target_arg": "x"},
    ],
}


def main() -> None:
    design = Design(
        id="demo",
        title="double then negate",
        network=_NETWORK,
        components=(RequestedComponent("counter", "v1"),),
    )
    report = validate_design(design)
    compiled = compile_design(design)
    print(f"valid={report.ok} components={report.component_ids}")
    print(
        f"design_hash={compiled.design_hash[:12]} "
        f"plan={[step['task'] for step in compiled.plan]}"
    )
    print(f"cycle_valid={validate_design(Design(id='bad', network=_CYCLIC)).ok}")


if __name__ == "__main__":
    main()
