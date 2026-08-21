"""Tests for the agent-centric FBP agent module (ZeroMQ directive/response).

These tests prove the deterministic core of the agent over ``inproc://``:

- the directive/response protocol is a versioned, enforced contract,
- the agent is born with only a minimal config (identity + parent endpoint),
- configuration is dynamic, via directives,
- the poll loop is steppable and deterministic,
- verification on the upward path is the correctness spine,
- idempotency: the same directive yields the same result.

No network, no daemons — the core is pure and offline-testable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import zmq
import zmq.asyncio

from agent_centric.fbp import (
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_PING,
    DIRECTIVE_RUN,
    MESSAGE_ACK,
    MESSAGE_DIRECTIVE,
    MESSAGE_RESPONSE,
    RESPONSE_ERROR,
    RESPONSE_OK,
    RESPONSE_RESULT,
    Agent,
    AgentConfig,
    ProtocolError,
    register_callable,
    validate_ack,
    validate_directive,
    validate_response,
)
from agent_centric.fbp.message import Ack, Directive, Response


def _double(value: int) -> int:
    return value * 2


def _even(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 0


def _odd(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 1


async def _send(socket: zmq.asyncio.Socket, directive: Directive) -> None:
    # On a ROUTER, the first frame is the destination's routing identity.
    await socket.send_multipart(
        [
            b"leaf",
            directive.correlation_id.encode(),
            MESSAGE_DIRECTIVE.encode(),
            directive.kind.encode(),
            __import__("json").dumps(directive.payload).encode(),
        ]
    )


async def _recv_ack(socket: zmq.asyncio.Socket) -> Ack:
    frames = await socket.recv_multipart()
    # ROUTER prepends the sender's routing identity as the first frame.
    identity = frames[0].decode()
    correlation_id = frames[1].decode()
    message_kind = frames[2].decode()
    assert message_kind == MESSAGE_ACK
    return Ack(correlation_id=correlation_id, node=identity)


async def _recv_response(socket: zmq.asyncio.Socket) -> Response:
    frames = await socket.recv_multipart()
    # ROUTER prepends the sender's routing identity as the first frame.
    identity = frames[0].decode()
    correlation_id = frames[1].decode()
    message_kind = frames[2].decode()
    response_kind = frames[3].decode()
    payload = __import__("json").loads(frames[4].decode())
    assert message_kind == MESSAGE_RESPONSE
    return Response(
        correlation_id=correlation_id,
        kind=response_kind,
        value=payload.get("value"),
        verified=payload.get("verified", False),
        node=payload.get("node", identity),
        error=payload.get("error"),
    )


class TestProtocol:
    def test_directive_validation(self) -> None:
        validate_directive(Directive(correlation_id="c1", kind=DIRECTIVE_RUN))
        with pytest.raises(ProtocolError):
            validate_directive(Directive(correlation_id="", kind=DIRECTIVE_RUN))
        with pytest.raises(ProtocolError):
            validate_directive(Directive(correlation_id="c1", kind="bogus"))

    def test_ack_validation(self) -> None:
        validate_ack(Ack(correlation_id="c1"))
        with pytest.raises(ProtocolError):
            validate_ack(Ack(correlation_id=""))

    def test_response_validation(self) -> None:
        validate_response(
            Response(correlation_id="c1", kind=RESPONSE_RESULT, value=2, verified=True)
        )
        # A 'result' response must be verified.
        with pytest.raises(ProtocolError):
            validate_response(
                Response(correlation_id="c1", kind=RESPONSE_RESULT, value=2, verified=False)
            )
        # An 'error' response must not be verified.
        with pytest.raises(ProtocolError):
            validate_response(
                Response(correlation_id="c1", kind=RESPONSE_ERROR, verified=True)
            )


class TestAgentLifecycle:
    def test_init_provides_minimal_config(self) -> None:
        config = AgentConfig(identity="leaf", parent_endpoint="inproc://parent")
        agent = Agent(config)
        assert agent.identity == "leaf"
        assert agent.config.parent_endpoint == "inproc://parent"

    def test_kill_releases_state(self) -> None:
        agent = Agent(AgentConfig(identity="leaf", parent_endpoint="inproc://parent"))
        agent.init()
        agent.kill()
        assert agent._parent is None  # noqa: SLF001


class TestAgentRun:
    async def _run_agent(
        self, directive: Directive, agent: Agent | None = None
    ) -> tuple[Response, Agent]:
        """Send a directive to an agent sharing a context, step its poll.

        The agent and the parent ROUTER must share one context for inproc://
        to connect. If no agent is given, one is created with a fresh context.
        Returns (response, agent).
        """
        context = zmq.asyncio.Context()
        parent = context.socket(zmq.ROUTER)
        parent.bind("inproc://parent")
        if agent is None:
            agent = Agent(
                AgentConfig(
                    identity="leaf",
                    parent_endpoint="inproc://parent",
                    context=context,
                )
            )
        else:
            # Re-point the pre-configured agent at the shared context.
            agent._config = AgentConfig(
                identity=agent.identity,
                parent_endpoint="inproc://parent",
                context=context,
            )
        agent.init()
        await _send(parent, directive)
        await agent.poll(timeout=0.1)
        ack = await _recv_ack(parent)
        assert ack.correlation_id == directive.correlation_id
        response = await _recv_response(parent)
        agent.kill()
        parent.close(0)
        context.term()
        return response, agent

    def test_configure_then_run(self) -> None:
        async def scenario() -> None:
            register_callable("double", _double)
            register_callable("even", _even)
            cfg = Directive(
                correlation_id="cfg1",
                kind=DIRECTIVE_CONFIGURE,
                payload={"tasks": ["double"], "verifiers": ["even"]},
            )
            resp, _ = await self._run_agent(cfg)
            assert resp.kind == RESPONSE_OK
            assert resp.verified is True

        asyncio.run(scenario())

    def test_run_verified_task(self) -> None:
        async def scenario() -> None:
            register_callable("double", _double)
            register_callable("even", _even)
            agent = Agent(
                AgentConfig(
                    identity="leaf",
                    parent_endpoint="inproc://parent",
                    context=zmq.asyncio.Context(),
                )
            )
            agent.init()
            agent._configure(
                Directive(
                    correlation_id="cfg1",
                    kind=DIRECTIVE_CONFIGURE,
                    payload={"tasks": ["double"], "verifiers": ["even"]},
                )
            )
            run = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 21}, "verifier": "even"},
            )
            resp, _ = await self._run_agent(run, agent=agent)
            assert resp.kind == RESPONSE_RESULT
            assert resp.verified is True
            assert resp.value == 42

        asyncio.run(scenario())

    def test_run_fails_verification(self) -> None:
        async def scenario() -> None:
            register_callable("double", _double)
            register_callable("odd", _odd)
            agent = Agent(
                AgentConfig(
                    identity="leaf",
                    parent_endpoint="inproc://parent",
                    context=zmq.asyncio.Context(),
                )
            )
            agent.init()
            agent._configure(
                Directive(
                    correlation_id="cfg1",
                    kind=DIRECTIVE_CONFIGURE,
                    payload={"tasks": ["double"], "verifiers": ["odd"]},
                )
            )
            run = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 3}, "verifier": "odd"},
            )
            resp, _ = await self._run_agent(run, agent=agent)
            assert resp.kind == RESPONSE_ERROR
            assert resp.verified is False
            assert resp.error is not None

        asyncio.run(scenario())

    def test_idempotency(self) -> None:
        async def scenario() -> None:
            register_callable("double", _double)
            agent = Agent(
                AgentConfig(
                    identity="leaf",
                    parent_endpoint="inproc://parent",
                    context=zmq.asyncio.Context(),
                )
            )
            agent.init()
            agent._configure(
                Directive(
                    correlation_id="cfg1",
                    kind=DIRECTIVE_CONFIGURE,
                    payload={"tasks": ["double"]},
                )
            )
            run = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 21}},
            )
            first, _ = await self._run_agent(run, agent=agent)
            second, _ = await self._run_agent(run, agent=agent)
            assert first.value == second.value == 42
            assert first.verified == second.verified is True

        asyncio.run(scenario())

    def test_ping(self) -> None:
        async def scenario() -> None:
            resp, _ = await self._run_agent(
                Directive(correlation_id="p1", kind=DIRECTIVE_PING)
            )
            assert resp.kind == RESPONSE_OK
            assert resp.verified is True

        asyncio.run(scenario())
        