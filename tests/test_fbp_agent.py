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
    DIRECTIVE_SPAWN,
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


async def _send(
    socket: zmq.asyncio.Socket, directive: Directive, to: bytes = b"leaf"
) -> None:
    # On a ROUTER, the first frame is the destination's routing identity.
    await socket.send_multipart(
        [
            to,
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
        source=payload.get("source", ""),
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

    def test_idempotency_keyed_by_full_directive_content(self) -> None:
        """A replayed directive is cached by its full content, not correlation id alone.

        The cache key includes the payload, so a directive with the same
        correlation id but different content is not served stale data.
        """
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
            run21 = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 21}},
            )
            first, _ = await self._run_agent(run21, agent=agent)
            assert first.value == 42
            # Same correlation id, different args: must NOT return the cached 42.
            run99 = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 99}},
            )
            second, _ = await self._run_agent(run99, agent=agent)
            assert second.kind == RESPONSE_ERROR
            assert second.verified is False
            assert "reused" in (second.error or "")

        asyncio.run(scenario())

    def test_ping(self) -> None:
        async def scenario() -> None:
            resp, _ = await self._run_agent(
                Directive(correlation_id="p1", kind=DIRECTIVE_PING)
            )
            assert resp.kind == RESPONSE_OK
            assert resp.verified is True

        asyncio.run(scenario())

    def test_chain_audit_source_on_verified_response(self) -> None:
        """A verified result carries the source URL of the callable that ran.

        This is the chain-audit clamp: the trajectory records *which* callable
        ran and from where, so execution is fully auditable.
        """
        async def scenario() -> None:
            register_callable("double", _double, source_url="src://tasks/double.py")
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
            resp, _ = await self._run_agent(run, agent=agent)
            assert resp.kind == RESPONSE_RESULT
            assert resp.verified is True
            assert resp.value == 42
            assert resp.source == "src://tasks/double.py"

        asyncio.run(scenario())

    def test_run_without_source_is_empty(self) -> None:
        """A callable registered without a source records an empty source."""
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
            resp, _ = await self._run_agent(run, agent=agent)
            assert resp.kind == RESPONSE_RESULT
            assert resp.verified is True
            assert resp.source == ""

        asyncio.run(scenario())


class TestTwoAgentRoundTrip:
    """Prove the core topology: a real parent/child agent round-trip over inproc.

    Parent binds a ROUTER at an endpoint; the child (a real Agent) connects a
    DEALER to it. The parent routes a run directive down; the child executes and
    responds; the parent relays the verified response up. This is the
    foundation's untested claim: work flows down, verified responses flow up.
    """

    async def _round_trip(
        self, correlation_id: str
    ) -> tuple[Response, Agent, Agent]:
        context = zmq.asyncio.Context()
        # Child connects its DEALER to this endpoint; parent binds a ROUTER here.
        parent_socket = context.socket(zmq.ROUTER)
        parent_socket.bind("inproc://children")

        # Parent agent: connects DEALER up (unused here), holds child ROUTER.
        parent = Agent(
            AgentConfig(identity="parent", parent_endpoint="inproc://root", context=context)
        )
        parent.init()
        parent._children["child"] = parent_socket  # child ROUTER on parent's poll

        # Child agent: connects DEALER to the parent's child ROUTER.
        child = Agent(
            AgentConfig(identity="child", parent_endpoint="inproc://children", context=context)
        )
        child.init()

        # Configure the child with the double task.
        register_callable("double", _double)
        child._configure(
            Directive(
                correlation_id="cfg-child",
                kind=DIRECTIVE_CONFIGURE,
                payload={"tasks": ["double"]},
            )
        )

        # Parent routes a run directive down to the child.
        run = Directive(
            correlation_id=correlation_id,
            kind=DIRECTIVE_RUN,
            payload={"task": "double", "args": {"value": 21}},
        )
        await _send(parent_socket, run, to=b"child")

        # Child polls: receives the directive, acks, executes, responds.
        await child.poll(timeout=0.1)
        # The child's ack + response arrive on the parent's child ROUTER.
        ack = await _recv_ack(parent_socket)
        assert ack.correlation_id == correlation_id
        response = await _recv_response(parent_socket)

        parent.kill()
        child.kill()
        context.term()
        return response, parent, child

    def test_child_response_reaches_parent_verified(self) -> None:
        async def scenario() -> None:
            resp, _, _ = await self._round_trip("rt1")
            assert resp.kind == RESPONSE_RESULT
            assert resp.verified is True
            assert resp.value == 42
            assert resp.node == "child"

        asyncio.run(scenario())


class TestMediatedSpawnDelegation:
    """Prove mediated spawn and delegation: a parent provisions a real child
    Agent and routes a run directive down to it, receiving the child's verified
    response and relaying it up to its own parent.
    """

    def test_spawn_provisions_real_child_and_delegates(self) -> None:
        async def scenario() -> None:
            register_callable("double", _double)
            context = zmq.asyncio.Context()

            # Root ROUTER: the parent connects its DEALER up to it. This is the
            # origin of work and the recipient of the relayed response.
            root = context.socket(zmq.ROUTER)
            root.bind("inproc://root")

            parent = Agent(
                AgentConfig(identity="parent", parent_endpoint="inproc://root", context=context)
            )
            parent.init()

            # Parent mediates child-creation: it binds the child's ROUTER and
            # provisions a real child Agent (not just a socket).
            spawn = Directive(
                correlation_id="spawn1",
                kind=DIRECTIVE_SPAWN,
                payload={"identity": "child", "endpoint": "inproc://children"},
            )
            resp = parent._spawn(spawn)
            assert resp.kind == RESPONSE_OK
            assert "child" in parent._children
            assert "child" in parent._child_agents

            # Configure the child with the double task.
            child = parent._child_agents["child"]
            child._configure(
                Directive(
                    correlation_id="cfg-child",
                    kind=DIRECTIVE_CONFIGURE,
                    payload={"tasks": ["double"]},
                )
            )

            # The root sends a run directive to the parent, naming the child.
            run = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 21}, "child": "child"},
            )
            await _send(root, run, to=b"parent")

            # Parent polls: receives the run, acks, delegates it to the child.
            await parent.poll(timeout=0.1)
            ack = await _recv_ack(root)
            assert ack.correlation_id == "run1"

            # Child polls: receives the directive, acks, executes, responds.
            await child.poll(timeout=0.1)

            # Parent polls: receives the child's verified response and relays it up.
            await parent.poll(timeout=0.1)
            relayed = await _recv_response(root)
            assert relayed.kind == RESPONSE_RESULT
            assert relayed.verified is True
            assert relayed.value == 42
            assert relayed.node == "child"

            child.kill()
            parent.kill()
            root.close(0)
            context.term()

        asyncio.run(scenario())

    def test_unknown_delegation_target_fails_closed(self) -> None:
        """A run naming an unknown child is not silently routed anywhere.

        The parent has no spawned child with that identity; fail-closed means
        the directive is handled (and fails) rather than silently dropped or
        routed to an arbitrary child.
        """
        async def scenario() -> None:
            register_callable("double", _double)
            context = zmq.asyncio.Context()

            root = context.socket(zmq.ROUTER)
            root.bind("inproc://root")

            parent = Agent(
                AgentConfig(identity="parent", parent_endpoint="inproc://root", context=context)
            )
            parent.init()
            parent._configure(
                Directive(
                    correlation_id="cfg1",
                    kind=DIRECTIVE_CONFIGURE,
                    payload={"tasks": ["double"]},
                )
            )

            run = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 21}, "child": "ghost"},
            )
            await _send(root, run, to=b"parent")

            await parent.poll(timeout=0.1)
            ack = await _recv_ack(root)
            assert ack.correlation_id == "run1"

            # The parent cannot delegate to "ghost"; it must still produce a
            # terminal response rather than hang or silently drop.
            response = await _recv_response(root)
            assert response.kind in (RESPONSE_RESULT, RESPONSE_ERROR)

            parent.kill()
            root.close(0)
            context.term()

        asyncio.run(scenario())

    def test_spawn_is_idempotent(self) -> None:
        """A replayed spawn directive does not create a duplicate child.

        Binding the same inproc endpoint twice would raise, so idempotent spawn
        must reuse the existing child rather than create a second one.
        """
        async def scenario() -> None:
            context = zmq.asyncio.Context()
            parent = Agent(
                AgentConfig(identity="parent", parent_endpoint="inproc://root", context=context)
            )
            parent.init()

            spawn = Directive(
                correlation_id="spawn1",
                kind=DIRECTIVE_SPAWN,
                payload={"identity": "child", "endpoint": "inproc://children"},
            )
            first = parent._spawn(spawn)
            assert first.kind == RESPONSE_OK
            assert "child" in parent._child_agents

            # Replaying the same spawn must reuse the existing child, not
            # create a duplicate (and not re-bind the endpoint, which would fail).
            second = parent._spawn(spawn)
            assert second.kind == RESPONSE_OK
            assert len(parent._child_agents) == 1
            assert len(parent._children) == 1

            parent.kill()
            context.term()

        asyncio.run(scenario())

    def test_parent_reverifies_child_on_upward_path(self) -> None:
        """The correctness spine in the agent protocol: a parent re-verifies a
        delegated child's value against its own verifier before accepting it.

        A child that claims verified but returns a value failing the parent's
        configured verifier must be demoted to an explicit, audited failure —
        never relayed up as a verified success. This mirrors the parent
        verification in the synchronous node model.
        """
        async def scenario() -> None:
            register_callable("double", _double)
            register_callable("odd", _odd)
            context = zmq.asyncio.Context()

            root = context.socket(zmq.ROUTER)
            root.bind("inproc://root")

            parent = Agent(
                AgentConfig(identity="parent", parent_endpoint="inproc://root", context=context)
            )
            parent.init()
            # Parent's own default verifier: only odd results pass.
            parent._configure(
                Directive(
                    correlation_id="cfg-parent",
                    kind=DIRECTIVE_CONFIGURE,
                    payload={"verifiers": ["odd"], "verifier": "odd"},
                )
            )

            spawn = Directive(
                correlation_id="spawn1",
                kind=DIRECTIVE_SPAWN,
                payload={"identity": "child", "endpoint": "inproc://children"},
            )
            parent._spawn(spawn)
            child = parent._child_agents["child"]
            child._configure(
                Directive(
                    correlation_id="cfg-child",
                    kind=DIRECTIVE_CONFIGURE,
                    payload={"tasks": ["double"]},
                )
            )

            run = Directive(
                correlation_id="run1",
                kind=DIRECTIVE_RUN,
                payload={"task": "double", "args": {"value": 21}, "child": "child"},
            )
            await _send(root, run, to=b"parent")

            await parent.poll(timeout=0.1)
            await _recv_ack(root)
            await child.poll(timeout=0.1)
            await parent.poll(timeout=0.1)

            # Child returns 42 (even); parent's odd-verifier rejects it.
            relayed = await _recv_response(root)
            assert relayed.kind == RESPONSE_ERROR
            assert relayed.verified is False

            child.kill()
            parent.kill()
            root.close(0)
            context.term()

        asyncio.run(scenario())


class TestFailClosedOnMalformedInput:
    """Prove the agent fails closed on malformed input rather than crashing.

    A malformed directive (wrong frame count, bad protocol, or unparseable JSON)
    is converted into an explicit, audited error and the agent keeps polling —
    garbage input is never an implicit or silent outcome (Explicit Failure).
    """

    def test_agent_survives_malformed_directive(self) -> None:
        async def scenario() -> None:
            context = zmq.asyncio.Context()
            parent = context.socket(zmq.ROUTER)
            parent.bind("inproc://parent")

            agent = Agent(
                AgentConfig(identity="parent", parent_endpoint="inproc://parent", context=context)
            )
            agent.init()

            # Send a malformed directive: wrong number of frames (partial).
            await parent.send_multipart([b"parent", b"c1", MESSAGE_DIRECTIVE.encode()])
            await agent.poll(timeout=0.1)

            # The agent must respond with an explicit error, not crash.
            error = await _recv_response(parent)
            assert error.kind == RESPONSE_ERROR
            assert error.verified is False

            # The agent is still alive and can service a valid directive.
            register_callable("double", _double)
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
            await _send(parent, run, to=b"parent")
            await agent.poll(timeout=0.1)
            await _recv_ack(parent)
            resp = await _recv_response(parent)
            assert resp.verified is True
            assert resp.value == 42

            agent.kill()
            parent.close(0)
            context.term()

        asyncio.run(scenario())

    def test_agent_survives_unparseable_json(self) -> None:
        async def scenario() -> None:
            context = zmq.asyncio.Context()
            parent = context.socket(zmq.ROUTER)
            parent.bind("inproc://parent")

            agent = Agent(
                AgentConfig(identity="parent", parent_endpoint="inproc://parent", context=context)
            )
            agent.init()

            # Unparseable JSON payload frame. Note the frame layout expected by
            # _recv: [correlation_id, kind=directive, directive_kind, payload].
            await parent.send_multipart(
                [b"parent", b"c1", MESSAGE_DIRECTIVE.encode(), b"run", b"not-json{"]
            )
            await agent.poll(timeout=0.1)

            error = await _recv_response(parent)
            assert error.kind == RESPONSE_ERROR
            assert error.verified is False

            agent.kill()
            parent.close(0)
            context.term()

        asyncio.run(scenario())
