"""Pure, allowlisted behavior for the example ``shell`` component.

The shell is the **root** of the CBP tree (``cbp/shell.py``): it owns the
top-level context and delegates work down the tree. In the distributed model the
shell is an ordinary component — the tree's root, not an external orchestrator
(SPEC-0007 §1). This module is the *executable* form of the shell's first duty:
turn a mission and its children into a deterministic delegation plan.

It is pure — no I/O, no model, no clock — so a boot is reproducible and the plan
can be independently verified. Execution is allowlisted (``component_runtime``).
"""

from __future__ import annotations

from typing import Any


class ShellComponentError(TypeError):
    """A shell plan payload is malformed (fail-closed)."""


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ShellComponentError(f"{name} must be a mapping")
    return value


def plan_delegation(payload: Any) -> dict[str, Any]:
    """Produce a deterministic delegation plan for the shell root (pure).

    Payload: ``{"mission": str, "components": [name, ...]}``. Returns the mission
    plus the children names in sorted order — the deterministic order in which
    the shell delegates work. Malformed input fails closed.
    """
    data = _mapping(payload, "plan_delegation payload")
    mission = data.get("mission")
    if not isinstance(mission, str) or not mission:
        raise ShellComponentError("mission must be a non-empty string")
    raw = data.get("components", [])
    if not isinstance(raw, list):
        raise ShellComponentError("components must be a list")
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ShellComponentError("component names must be non-empty strings")
        names.append(item)
    ordered = sorted(names)
    return {"mission": mission, "order": ordered, "count": len(ordered)}
