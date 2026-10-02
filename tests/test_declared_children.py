"""Tests for parent-declared child kinds (SPEC-0002 Phase-1 item 2b).

The runtime holds no central kind->class map any more: the tree builder
declares the concrete classes a parent may spawn, and an undeclared kind fails
closed **before** any socket is bound (no partial side effect). Offline.
"""

from __future__ import annotations

import zmq
import zmq.asyncio

from agent_centric.cbp import (
    DIRECTIVE_SPAWN,
    Agent,
    AgentConfig,
    CbpDriver,
)
from agent_centric.cbp.message import Directive
from agent_centric.cbp.store_agent import StoreAgent


def _spawn_directive(kind: object) -> Directive:
    return Directive(
        correlation_id="spawn-1",
        kind=DIRECTIVE_SPAWN,
        payload={"identity": "child", "endpoint": "inproc://child", "kind": kind},
    )


class TestDeclaredChildren:
    def test_undeclared_kind_fails_closed_without_a_child(self) -> None:
        context = zmq.asyncio.Context()
        agent = Agent(
            AgentConfig(identity="root", parent_endpoint="inproc://root", context=context)
        )
        agent.init()
        try:
            response = agent._handle(_spawn_directive("store"))
            assert response.verified is False
            assert response.error is not None
            assert "undeclared" in response.error
            assert agent.children == {}
        finally:
            agent.kill()
            context.term()

    def test_declared_kind_spawns_the_declared_class(self) -> None:
        context = zmq.asyncio.Context()
        agent = Agent(
            AgentConfig(identity="root", parent_endpoint="inproc://root", context=context)
        )
        agent.init()
        agent.declare_child_kind("store", StoreAgent)
        try:
            response = agent._handle(_spawn_directive("store"))
            assert response.verified is True
            assert type(agent.children["child"]) is StoreAgent
        finally:
            agent.kill()
            context.term()

    def test_generic_spawn_kind_none_uses_base_agent(self) -> None:
        with CbpDriver() as driver:
            response = driver.spawn("generic")
            assert response.verified is True
            assert type(driver._root.children["generic"]) is Agent

    def test_driver_declares_builtin_kinds(self) -> None:
        with CbpDriver() as driver:
            response = driver.spawn("store", kind="store")
            assert response.verified is True
            assert type(driver._root.children["store"]) is StoreAgent
