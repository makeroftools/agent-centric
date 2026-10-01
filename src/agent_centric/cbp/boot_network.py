"""Run a typed-port ``network.v1`` over a booted ``components.lock`` (SPEC-0011).

This is the additive bridge between two delivered primitives: ``boot_from_lock``
(verify a signed lock and instantiate its verified component entries) and
``run_network``/``run_flow`` (deterministic typed-port Information-Packet flow).
It closes the SPEC-0011 "viable" wiring: **boot a signed lock, then execute its
typed dataflow** — with every edge type-checked by ``network.validate`` and every
component entry resolved from the verified lock, not from a fresh registry.

Fail-closed: a network component that has no ``child`` route must map to a booted
component of the same name; an unmapped node refuses before anything runs.
Components that name a ``child`` (e.g. a spawned ``model``) are left to the
driver, which routes and re-verifies them.
"""

from __future__ import annotations

from typing import Any

from .component_boot import BootedTree
from .network import ComponentNetwork, run_network


def run_booted_network(
    tree: BootedTree,
    network: ComponentNetwork,
    *,
    driver: Any,
    step_limit: int | None = None,
) -> dict[str, Any]:
    """Register booted entries and run ``network`` as deterministic typed flow.

    Args:
        tree: A verified :class:`~agent_centric.cbp.component_boot.BootedTree`.
        network: The typed-port network to execute (validated by ``run_network``).
        driver: A ``CbpDriver`` (or compatible) that owns the run/child routes.
        step_limit: Optional ceiling on the number of components.

    Returns:
        The ``run_network`` result shape ``{"ok", "results", "completed", ...}``.
    """
    registered: list[str] = []
    for component in network.components():
        if component.child:
            # Routed to a spawned child (e.g. a model boundary); the driver owns
            # it and re-verifies its output. It is not a booted lock entry.
            continue
        booted = tree.by_name.get(component.id)
        if booted is None:
            return {
                "ok": False,
                "results": [],
                "completed": 0,
                "error": (
                    f"network component {component.id!r} is not in the booted lock "
                    f"{tree.lock_hash[:12]} (fail-closed)"
                ),
            }
        driver.register(
            component.task,
            booted.entry,
            source_url=f"lock://{tree.lock_hash}/{component.id}",
        )
        registered.append(component.task)
    if registered:
        driver.configure(tasks=tuple(registered))
    return run_network(driver, network, step_limit=step_limit)
