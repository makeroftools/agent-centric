"""Tests for the parent-provisioned component catalog (SPEC-0002 Phase-1 S2).

The catalog is an additive, explicit carrier for the passive registry. These
tests prove:

- the catalog resolves/projects like the registry it wraps, and
- an agent given a catalog resolves **only** from what its parent provisioned:
  a name absent from the catalog fails closed, even if the module global has it.

No network; dispatch is synchronous and offline.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp import (
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_RUN,
    RESPONSE_RESULT,
    Agent,
    AgentConfig,
    CbpDriver,
)
from agent_centric.cbp.component_catalog import ComponentCatalog
from agent_centric.cbp.message import Directive, Response
from agent_centric.cbp.registry import RegistryEntry


def _double(value: int) -> int:
    return value * 2


def _run_task(agent: Agent, task: str, value: int = 21) -> Response:
    return agent._handle(
        Directive(
            correlation_id=f"run-{task}",
            kind=DIRECTIVE_RUN,
            payload={"task": task, "args": {"value": value}},
        )
    )


def _configure(agent: Agent, tasks: tuple[str, ...]) -> None:
    agent._configure(
        Directive(
            correlation_id="cfg",
            kind=DIRECTIVE_CONFIGURE,
            payload={"tasks": list(tasks)},
        )
    )


class TestCatalogBasics:
    def test_register_resolve_and_names(self) -> None:
        catalog = ComponentCatalog()
        catalog.register("cat_double", _double, source_url="src://tasks/double.py")
        assert catalog.resolve("cat_double") is _double
        assert catalog.resolve("absent") is None
        assert catalog.names() == ("cat_double",)
        entry = catalog.entry("cat_double")
        assert entry is not None
        assert entry.source_url == "src://tasks/double.py"
        assert entry.module
        assert entry.qualname

    def test_from_entries_is_sorted_and_deterministic(self) -> None:
        entries = {
            "b": RegistryEntry.from_callable("b", _double),
            "a": RegistryEntry.from_callable("a", _double),
        }
        catalog = ComponentCatalog.from_entries(entries)
        assert catalog.names() == ("a", "b")

    def test_catalog_payload_is_canonical(self) -> None:
        catalog = ComponentCatalog()
        catalog.register("b", _double, source_url="src://b")
        catalog.register("a", _double)
        assert catalog.catalog_payload() == {
            "catalog": [
                {"name": "a", "source_url": "", "kind": "python"},
                {"name": "b", "source_url": "src://b", "kind": "python"},
            ]
        }


class TestProvisionedResolution:
    def test_provisioned_agent_resolves_from_catalog(self) -> None:
        catalog = ComponentCatalog()
        catalog.register("cat_only_double", _double)
        agent = Agent(
            AgentConfig(
                identity="leaf",
                parent_endpoint="inproc://parent",
                catalog=catalog,
            )
        )
        _configure(agent, ("cat_only_double",))
        response = _run_task(agent, "cat_only_double")
        assert response.kind == RESPONSE_RESULT
        assert response.verified is True
        assert response.value == 42

    def test_provisioned_agent_refuses_unprovisioned_name(self) -> None:
        # A name absent from the provisioned catalog fails closed.
        catalog = ComponentCatalog()
        agent = Agent(
            AgentConfig(
                identity="leaf",
                parent_endpoint="inproc://parent",
                catalog=catalog,
            )
        )
        with pytest.raises(KeyError):
            _configure(agent, ("global_only_double",))

    def test_agent_without_catalog_fails_closed(self) -> None:
        agent = Agent(AgentConfig(identity="leaf", parent_endpoint="inproc://parent"))
        with pytest.raises(KeyError):
            _configure(agent, ("any_double",))


class TestDriverProvisionsTheCatalog:
    """S3a: the driver owns the tree catalog and provisions it to the root and
    every spawned child; runtime resolution never falls back to the live global."""

    def test_driver_owns_catalog_and_provisions_children(self) -> None:
        with CbpDriver() as driver:
            driver.register("cat_driver_double", _double)
            assert driver._root._catalog is driver._catalog
            driver.configure(tasks=("cat_driver_double",))
            driver.spawn("child")
            assert driver._root.children["child"]._catalog is driver._catalog

    def test_unregistered_name_is_not_consulted(self) -> None:
        with CbpDriver() as driver:
            # Nothing was registered on this driver; an unregistered name must
            # fail closed (no ambient fallback).
            response = driver.configure(tasks=("never_registered_double",))
            assert response.verified is False


class TestNoGlobalRegistry:
    """SPEC-0002 item 2 acceptance: no module-level registry global remains."""

    def test_agent_module_has_no_registry_global(self) -> None:
        from agent_centric.cbp import agent as agent_module

        assert not hasattr(agent_module, "_REGISTRY")
        assert not hasattr(agent_module, "register_callable")
        assert not hasattr(agent_module, "_resolve_entry")

    def test_package_does_not_export_the_shim(self) -> None:
        import agent_centric.cbp as cbp

        assert not hasattr(cbp, "register_callable")
