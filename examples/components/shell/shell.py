"""Reference implementation of the example ``shell`` component entry.

This is the code a distributed bundle would ship. It is **documentation** only:
the harness executes the allowlisted in-tree entry
``agent_centric.cbp.shell_component:plan_delegation`` (see
``component_runtime.py``), which is byte-for-byte this behavior.
"""

from __future__ import annotations

from typing import Any


def plan_delegation(payload: Any) -> dict[str, Any]:
    """Produce a deterministic delegation plan for the shell root (pure)."""
    if not isinstance(payload, dict):
        raise TypeError("plan_delegation payload must be a mapping")
    mission = payload.get("mission")
    if not isinstance(mission, str) or not mission:
        raise TypeError("mission must be a non-empty string")
    raw = payload.get("components", [])
    if not isinstance(raw, list):
        raise TypeError("components must be a list")
    names = [item for item in raw if isinstance(item, str) and item]
    ordered = sorted(names)
    return {"mission": mission, "order": ordered, "count": len(ordered)}
