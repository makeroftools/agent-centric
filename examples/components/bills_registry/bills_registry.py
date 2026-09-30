"""Bundled reference behavior for the example ``bills_registry`` composite.

The harness executes the **allowlisted, in-tree** entry
(``agent_centric.fbp.bills_component:registry_snapshot``); this file documents
the component's own behavior and is included in the bundle so its bytes are
content-hashed (see ``SPEC-0007`` Phase 1b).

It mirrors the pure, deterministic rules: validate a bills-registry mapping
against the canonical ``bills_registry.v1`` contract and return its canonical
form. No I/O, no model, no clock.
"""

from __future__ import annotations


def registry_snapshot(registry: dict) -> dict:
    """Return the validated, canonical form of a bills-registry mapping.

    Raises ``ValueError`` on any malformed or missing field (fail-closed).
    """
    if not isinstance(registry, dict):
        raise ValueError("registry must be a mapping")
    bills = registry.get("bills")
    if not isinstance(bills, (list, tuple)) or not bills:
        raise ValueError("registry 'bills' must be a non-empty list")
    return {
        "version": "bills_registry.v1",
        "bills": list(bills),
        "description": registry.get("description", ""),
    }
