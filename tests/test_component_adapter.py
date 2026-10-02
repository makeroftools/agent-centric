"""Equivalence tests for the Agent→Component adapter (SPEC-0002 Phase-1 item 1).

The adapter is additive: it wraps a live ``Agent`` and projects it onto the
frozen ``component.v1`` contract. These tests prove the two claims the design
makes (``docs/agent/spec-0002-phase1-adapter-design.md`` §3):

- the **entry bridge** produces the same terminal response as the driver's
  round-trip for the same directive, and
- the **declarative projection** is a pure, stable function of live state.

No network, no daemons: dispatch is synchronous and offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_centric.cbp import (
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_RUN,
    RESPONSE_RESULT,
    Agent,
    AgentConfig,
    CbpDriver,
)
from agent_centric.cbp.component_adapter import AgentComponent, ComponentAdapterError
from agent_centric.cbp.component_catalog import ComponentCatalog
from agent_centric.cbp.message import Directive
from agent_centric.contracts.component import (
    ChildRef,
    ComponentManifest,
    EntryDescriptor,
)

_ENTRY = EntryDescriptor(runtime="python", entrypoint="tests.sample:entry")


def _double(value: int) -> int:
    return value * 2


def _even(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 0


def _configured_agent() -> Agent:
    catalog = ComponentCatalog()
    catalog.register("double", _double)
    catalog.register("even", _even)
    agent = Agent(
        AgentConfig(
            identity="leaf", parent_endpoint="inproc://parent", catalog=catalog
        )
    )
    agent._configure(
        Directive(
            correlation_id="cfg",
            kind=DIRECTIVE_CONFIGURE,
            payload={"tasks": ["double"], "verifiers": ["even"], "verifier": "even"},
        )
    )
    return agent


class TestEntryBridgeEquivalence:
    def test_run_once_matches_driver_round_trip(self) -> None:
        with CbpDriver() as driver:
            driver.register("double", _double)
            driver.register("even", _even)
            driver.configure(tasks=("double",), verifiers=("even",), verifier="even")
            driver_response = driver.run("double", {"value": 21})

        adapter = AgentComponent(_configured_agent(), entry=_ENTRY)
        response = adapter.run_once(
            Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 21}, "verifier": "even"},
            )
        )

        assert response.kind == RESPONSE_RESULT
        assert response.verified is True
        assert response.value == 42
        assert (response.kind, response.value, response.verified) == (
            driver_response.kind,
            driver_response.value,
            driver_response.verified,
        )

    def test_run_once_fails_closed_on_unknown_task(self) -> None:
        adapter = AgentComponent(_configured_agent(), entry=_ENTRY)
        response = adapter.run_once(
            Directive(
                correlation_id="run-ghost",
                kind=DIRECTIVE_RUN,
                payload={"task": "ghost", "args": {}},
            )
        )
        assert response.verified is False
        assert response.error is not None


class TestDeclarativeProjection:
    def test_manifest_is_valid_and_deterministic(self) -> None:
        catalog = ComponentCatalog()
        catalog.register("double", _double)
        catalog.register("even", _even)
        agent = Agent(
            AgentConfig(
                identity="leaf", parent_endpoint="inproc://parent", catalog=catalog
            )
        )
        agent._configure(
            Directive(
                correlation_id="cfg",
                kind=DIRECTIVE_CONFIGURE,
                payload={"tasks": ["double", "even"]},
            )
        )
        adapter = AgentComponent(agent, entry=_ENTRY)

        first = adapter.manifest()
        second = adapter.manifest()

        assert first == second
        assert first.version == "component.v1"
        assert first.name == "leaf"
        assert first.kind == "atomic"
        assert first.implements == ("component.v1",)
        assert first.entry == _ENTRY
        assert tuple(cap.name for cap in first.capabilities) == ("double", "even")
        assert ComponentManifest.from_dict(first.to_dict()) == first

    def test_state_descriptor_projected_when_granted(self, tmp_path: Path) -> None:
        agent = Agent(AgentConfig(identity="leaf", parent_endpoint="inproc://parent"))
        state_path = str(tmp_path / "state.db")
        agent._configure(
            Directive(
                correlation_id="cfg",
                kind=DIRECTIVE_CONFIGURE,
                payload={"state": state_path},
            )
        )
        manifest = AgentComponent(agent, entry=_ENTRY).manifest()
        assert manifest.state is not None
        assert manifest.state.kind == "sqlite"
        assert manifest.state.path == state_path

    def test_composite_kind_and_children(self) -> None:
        agent = Agent(AgentConfig(identity="parent", parent_endpoint="inproc://root"))
        agent._child_agents["child"] = Agent(
            AgentConfig(identity="child", parent_endpoint="inproc://root")
        )
        manifest = AgentComponent(agent, entry=_ENTRY).manifest()
        assert manifest.kind == "composite"
        assert manifest.children == (ChildRef(embed="child"),)


class TestFailClosedConstruction:
    def test_rejects_non_agent(self) -> None:
        with pytest.raises(ComponentAdapterError):
            AgentComponent(object(), entry=_ENTRY)  # type: ignore[arg-type]

    def test_rejects_empty_implements(self) -> None:
        agent = Agent(AgentConfig(identity="leaf", parent_endpoint="inproc://parent"))
        with pytest.raises(ComponentAdapterError):
            AgentComponent(agent, entry=_ENTRY, implements=())

    def test_run_once_rejects_non_directive(self) -> None:
        agent = Agent(AgentConfig(identity="leaf", parent_endpoint="inproc://parent"))
        adapter = AgentComponent(agent, entry=_ENTRY)
        with pytest.raises(ComponentAdapterError):
            adapter.run_once("not-a-directive")  # type: ignore[arg-type]
