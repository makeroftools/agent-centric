"""The abstract Agent — the one type at the heart of the agent-centric design.

Every agent is the center of its own little universe: a worker to its parent
and a manager to its children, all serviced by one async poll loop over a
dynamic set of channels.

The agent implements three operations — ``init``, ``run``, ``kill`` — and is
constructed with only a minimal ``AgentConfig`` (identity + parent endpoint).
Everything else arrives dynamically as directives.

The poll loop is *steppable*: ``poll(timeout)`` services one batch of ready
events and returns. ``run()`` is a thin driver over it. This keeps the loop
deterministic and testable — you can inject a directive, step the poll, and
assert the exact response.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import zmq
import zmq.asyncio

from .config import AgentConfig
from .message import (
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_KILL,
    DIRECTIVE_PING,
    DIRECTIVE_RUN,
    DIRECTIVE_SPAWN,
    MESSAGE_ACK,
    MESSAGE_DIRECTIVE,
    MESSAGE_RESPONSE,
    RESPONSE_ERROR,
    RESPONSE_OK,
    RESPONSE_RESULT,
    Ack,
    Directive,
    ProtocolError,
    Response,
    validate_ack,
    validate_directive,
    validate_response,
)
from .registry import Registry

# A task is a registered callable: same reference, same args -> same result.
Task = Callable[..., Any]
# A verifier is a pure callable: given a value, return True if verified.
Verifier = Callable[[Any], bool]


# The registry of callables known to the system. In the foundation this is a
# module-level stub; trust and persistence are clamped down later.
_REGISTRY: dict[str, Callable[..., Any]] = {}


def _resolve(name: str) -> Callable[..., Any]:
    """Resolve a callable by name from the system registry.

    Raises:
        KeyError: If the name is not registered.
    """
    return _REGISTRY[name]


def register_callable(name: str, fn: Callable[..., Any]) -> None:
    """Register a callable by name so directives can reference it."""
    _REGISTRY[name] = fn


class Agent:
    """The abstract agent: worker to its parent, manager to its children.

    Attributes:
        config: The minimal bootstrap configuration.
        context: The ZeroMQ context (shared across the agent's sockets).
        channels: The set of channels the poll loop monitors (dynamic).
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._context: zmq.asyncio.Context | None = None
        self._owns_context = True
        self._parent: zmq.asyncio.Socket | None = None
        self._children: dict[str, zmq.asyncio.Socket] = {}
        self._registry = Registry()
        self._rules: tuple[str, ...] = ()
        self._alive = False
        self._results: dict[str, Response] = {}  # correlation_id -> response (idempotency)

    @property
    def identity(self) -> str:
        return self._config.identity

    @property
    def config(self) -> AgentConfig:
        return self._config

    def init(self) -> None:
        """Bootstrap: create the context and connect to the parent.

        The agent is born knowing only its identity and its parent's endpoint.
        Everything else arrives as directives. When the config carries a shared
        context (required for ``inproc://``), the agent uses it and does not
        own it; otherwise it creates its own.
        """
        self._context = self._config.context or zmq.asyncio.Context()
        self._owns_context = self._config.context is None
        self._parent = self._context.socket(zmq.DEALER)
        self._parent.setsockopt(zmq.IDENTITY, self._config.identity.encode())
        endpoint = self._endpoint(self._config.parent_endpoint)
        self._parent.connect(endpoint)
        self._alive = True

    def kill(self) -> None:
        """Teardown: close children, then the parent, then the context."""
        self._alive = False
        for child in self._children.values():
            child.close(0)
        self._children.clear()
        if self._parent is not None:
            self._parent.close(0)
            self._parent = None
        if self._context is not None and self._owns_context:
            self._context.term()
            self._context = None

    def _endpoint(self, ep: str) -> str:
        """Resolve a channel endpoint against the configured transport."""
        if "://" in ep:
            return ep
        return f"{self._config.transport}://{ep}"

    async def _send(self, msg: Response) -> None:
        """Send a response up to the parent."""
        if self._parent is None:
            raise RuntimeError(f"Agent {self.identity!r} is not initialised.")
        validate_response(msg)
        await self._parent.send_multipart(
            [
                msg.correlation_id.encode(),
                MESSAGE_RESPONSE.encode(),
                msg.kind.encode(),
                json.dumps(self._payload(msg)).encode(),
            ]
        )

    async def _send_ack(self, correlation_id: str) -> None:
        """Send a dumb acknowledgment that a directive was received."""
        if self._parent is None:
            raise RuntimeError(f"Agent {self.identity!r} is not initialised.")
        ack = Ack(correlation_id=correlation_id, node=self.identity)
        validate_ack(ack)
        await self._parent.send_multipart(
            [
                ack.correlation_id.encode(),
                MESSAGE_ACK.encode(),
                b"",
            ]
        )

    @staticmethod
    def _payload(msg: Response) -> dict[str, Any]:
        """Serialize a response to its wire payload."""
        return {
            "value": msg.value,
            "verified": msg.verified,
            "node": msg.node,
            "error": msg.error,
            "protocol": msg.protocol,
        }

    async def _recv(self) -> Directive:
        """Receive and validate a directive from the parent."""
        if self._parent is None:
            raise RuntimeError(f"Agent {self.identity!r} is not initialised.")
        frames = await self._parent.recv_multipart()
        if len(frames) != 4:
            raise ProtocolError(f"malformed directive: {len(frames)} frames")
        correlation_id = frames[0].decode()
        message_kind = frames[1].decode()
        directive_kind = frames[2].decode()
        payload = json.loads(frames[3].decode())
        if message_kind != MESSAGE_DIRECTIVE:
            raise ProtocolError(f"expected a directive, got message kind {message_kind!r}")
        msg = Directive(correlation_id=correlation_id, kind=directive_kind, payload=payload)
        validate_directive(msg)
        return msg

    async def poll(self, timeout: float = 0.0) -> list[Response]:
        """Service one batch of ready events and return the responses produced.

        This is the steppable core of the agent. It polls the parent channel
        (and any child channels) for activity and handles whatever is ready.
        Returns the list of responses produced this step.
        """
        if self._parent is None:
            raise RuntimeError(f"Agent {self.identity!r} is not initialised.")
        poller = zmq.asyncio.Poller()
        poller.register(self._parent, zmq.POLLIN)
        for child in self._children.values():
            poller.register(child, zmq.POLLIN)

        events = dict(await poller.poll(timeout))
        responses: list[Response] = []

        if events.get(self._parent) == zmq.POLLIN:
            directive = await self._recv()
            await self._send_ack(directive.correlation_id)
            response = self._handle(directive)
            await self._send(response)
            responses.append(response)

        for child in self._children.values():
            if events.get(child) == zmq.POLLIN:
                responses.append(await self._handle_child(child))

        return responses

    def _handle(self, directive: Directive) -> Response:
        """Handle a directive from the parent, dispatching by kind."""
        if directive.kind == DIRECTIVE_CONFIGURE:
            return self._configure(directive)
        if directive.kind == DIRECTIVE_RUN:
            return self._run_task(directive)
        if directive.kind == DIRECTIVE_SPAWN:
            return self._spawn(directive)
        if directive.kind == DIRECTIVE_PING:
            return Response(
                correlation_id=directive.correlation_id,
                kind=RESPONSE_OK,
                verified=True,
                node=self.identity,
            )
        if directive.kind == DIRECTIVE_KILL:
            self.kill()
            return Response(
                correlation_id=directive.correlation_id,
                kind=RESPONSE_OK,
                verified=True,
                node=self.identity,
            )
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_ERROR,
            verified=False,
            node=self.identity,
            error=f"unknown directive kind {directive.kind!r}",
        )

    def _configure(self, directive: Directive) -> Response:
        """Configure the agent's rules and callable registry from the directive.

        The directive is self-contained: it carries the rules and the names of
        the tasks/verifiers the agent may run. The callables themselves are
        resolved from the registry (the wire carries names, not callables).
        """
        payload = directive.payload
        self._rules = tuple(payload.get("rules", ()))
        for name in payload.get("tasks", ()):
            self._registry.register(name, _resolve(name))
        for name in payload.get("verifiers", ()):
            self._registry.register(name, _resolve(name))
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    def _run_task(self, directive: Directive) -> Response:
        """Execute a task from the directive's payload.

        Idempotency: if this correlation id was already handled, return the
        cached result instead of re-executing.
        """
        if directive.correlation_id in self._results:
            return self._results[directive.correlation_id]

        task_name = directive.payload.get("task")
        args = directive.payload.get("args", {})
        verifier_name = directive.payload.get("verifier")

        task = self._registry.resolve(task_name) if task_name else None
        if task is None:
            return self._error(directive, f"unknown task {task_name!r}")

        try:
            value = task(**args)
        except Exception as exc:  # noqa: BLE001 - explicit failure, never silent
            return self._error(directive, f"task {task_name!r} raised: {exc}")

        verified = self._verify(value, verifier_name)
        response = Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT if verified else RESPONSE_ERROR,
            value=value if verified else None,
            verified=verified,
            node=self.identity,
            error=None if verified else f"verification failed for task {task_name!r}",
        )
        self._results[directive.correlation_id] = response
        return response

    def _verify(self, value: Any, verifier_name: str | None) -> bool:
        """Apply the named verifier to a value (True if none named)."""
        if verifier_name is None:
            return True
        verifier = self._registry.resolve(verifier_name)
        if verifier is None:
            return False
        return bool(verifier(value))

    def _spawn(self, directive: Directive) -> Response:
        """Spawn a child agent as prescribed by the directive.

        The child is created with the config in the payload and connected to
        this agent as its parent. The child's socket is added to the poll set.
        """
        payload = directive.payload
        child_identity = payload.get("identity")
        child_endpoint = payload.get("endpoint")
        if not child_identity or not child_endpoint:
            return self._error(directive, "spawn requires 'identity' and 'endpoint'")

        if self._context is None:
            return self._error(directive, "agent not initialised")

        child = self._context.socket(zmq.ROUTER)
        child.bind(self._endpoint(child_endpoint))
        self._children[child_identity] = child
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    async def _handle_child(self, child: zmq.asyncio.Socket) -> Response:
        """Handle a response from a child, verifying it on the way up."""
        frames = await child.recv_multipart()
        if len(frames) != 5:
            return Response(
                correlation_id="",
                kind=RESPONSE_ERROR,
                verified=False,
                node=self.identity,
                error="malformed child response",
            )
        identity = frames[0].decode()
        correlation_id = frames[1].decode()
        message_kind = frames[2].decode()
        response_kind = frames[3].decode()
        payload = json.loads(frames[4].decode())
        if message_kind != MESSAGE_RESPONSE:
            return Response(
                correlation_id=correlation_id,
                kind=RESPONSE_ERROR,
                verified=False,
                node=self.identity,
                error=f"child {identity!r} sent message kind {message_kind!r}, expected response",
            )
        msg = Response(
            correlation_id=correlation_id,
            kind=response_kind,
            value=payload.get("value"),
            verified=payload.get("verified", False),
            node=payload.get("node", identity),
            error=payload.get("error"),
        )
        try:
            validate_response(msg)
        except ProtocolError as exc:
            return Response(
                correlation_id=correlation_id,
                kind=RESPONSE_ERROR,
                verified=False,
                node=self.identity,
                error=f"child {identity!r} sent invalid response: {exc}",
            )
        return msg

    def _error(self, directive: Directive, message: str) -> Response:
        """Build an explicit, audited failure response."""
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_ERROR,
            verified=False,
            node=self.identity,
            error=message,
        )

    async def run(self) -> None:
        """Drive the poll loop until killed.

        This is a thin driver over the steppable ``poll``. It is not a daemon:
        it runs in the caller's event loop and returns when the agent is killed.
        """
        while self._alive:
            await self.poll(timeout=0.1)