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
    DIRECTIVE_REGISTER,
    DIRECTIVE_RESOLVE,
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
from .registry import Registry, RegistryEntry

# A task is a registered callable: same reference, same args -> same result.
Task = Callable[..., Any]
# A verifier is a pure callable: given a value, return True if verified.
Verifier = Callable[[Any], bool]


# The registry of callables known to the system. In the foundation this is a
# module-level stub; trust and persistence are clamped down later. Each entry
# carries the callable and its source location (URL) so the trajectory can
# record *which* callable ran and from where (chain audit).
_REGISTRY: dict[str, RegistryEntry] = {}


def _resolve_entry(name: str) -> RegistryEntry:
    """Resolve the full registry entry (callable + source) by name.

    Raises:
        KeyError: If the name is not registered.
    """
    return _REGISTRY[name]


def register_callable(
    name: str, fn: Callable[..., Any], *, source_url: str = ""
) -> None:
    """Register a callable by name so directives can reference it.

    Args:
        name: The name directives will use to reference the callable.
        fn: The callable to register.
        source_url: The source location (URL) of the callable, for chain audit.
    """
    _REGISTRY[name] = RegistryEntry(name=name, callable=fn, source_url=source_url)


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
        self._child_agents: dict[str, Agent] = {}
        self._registry = Registry()
        self._rules: tuple[str, ...] = ()
        self._verifier: str | None = None
        self._alive = False
        # Idempotency cache: full directive fingerprint -> response. A replayed
        # directive (same correlation_id + kind + canonic payload) returns the
        # cached result instead of re-executing, so retry/replay are safe.
        self._results: dict[tuple[str, str, str], Response] = {}
        # Correlation ids already served, to reject a correlation id reused for
        # different work (fail closed rather than silently returning stale data).
        self._used_keys: set[tuple[str, str]] = set()
        # correlation_id -> child_identity, for mediated delegation awaiting a
        # child response to verify (via the parent's own verifier) and route up.
        self._delegated: dict[str, str] = {}

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
        """Teardown: kill child agents, close children, then parent, then context."""
        self._alive = False
        for child_agent in self._child_agents.values():
            child_agent.kill()
        self._child_agents.clear()
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
            "source": msg.source,
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
            response = await self._handle_parent_message()
            if response is not None:
                responses.append(response)

        for child_id, child in self._children.items():
            if events.get(child) == zmq.POLLIN:
                responses.extend(await self._drain_child(child, child_id))

        return responses

    async def _handle_parent_message(self) -> Response | None:
        """Receive and handle one message from the parent, failing closed.

        A malformed or invalid message — wrong frame count, bad protocol, or
        unparseable JSON — is converted into an explicit, audited error response
        rather than crashing the poll loop. This enforces ``Explicit Failure``:
        garbage input is never an implicit or silent outcome.
        """
        try:
            directive = await self._recv()
        except (ProtocolError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            response = self._correlationless_error(f"malformed directive rejected: {exc}")
            await self._send(response)
            return response
        await self._send_ack(directive.correlation_id)
        return await self._dispatch(directive)

    async def _dispatch(self, directive: Directive) -> Response | None:
        """Dispatch a directive, delegating down or handling locally (fail-closed)."""
        try:
            return await self._dispatch_inner(directive)
        except (ProtocolError, ValueError, KeyError) as exc:
            response = self._error(directive, f"directive rejected: {exc}")
            await self._send(response)
            return response

    async def _dispatch_inner(self, directive: Directive) -> Response | None:
        """Route to a child (delegation) or handle locally."""
        delegated = self._maybe_delegate(directive)
        if delegated is not None and delegated in self._children:
            # Route the directive down to the named child; the child response
            # comes back later via a child event and is relayed up.
            await self.delegate(delegated, directive)
            self._delegated[directive.correlation_id] = delegated
            return None
        # Either not a run directive, not delegatable, or an unknown delegation
        # target (fail closed): handle locally.
        response = self._handle(directive)
        await self._send(response)
        return response

    async def _drain_child(
        self, child: zmq.asyncio.Socket, child_identity: str
    ) -> list[Response]:
        """Receive and handle all ready messages from a child channel.

        A child sends an ack (delivery) then a response (completion), so a
        single POLLIN event can cover two messages. Drain until the socket has
        no more pending messages; acks are consumed and skipped, responses are
        verified and (if they fulfil a delegation) relayed up.
        """
        drained: list[Response] = []
        while True:
            if not await self._child_ready(child):
                break
            child_response = await self._handle_child(child)
            if child_response is None:
                # A consumed ack; continue draining.
                continue
            delegated = self._delegated.pop(child_response.correlation_id, None)
            if delegated is not None:
                # The parent re-verifies the child's value on the upward path
                # before accepting responsibility (the correctness spine).
                drained.append(
                    await self._relay_verified(child_response, child_identity)
                )
            else:
                drained.append(child_response)
        return drained

    @staticmethod
    async def _child_ready(child: zmq.asyncio.Socket) -> bool:
        """Return True if the child channel has a ready message."""
        poller = zmq.asyncio.Poller()
        poller.register(child, zmq.POLLIN)
        events = dict(await poller.poll(0))
        return events.get(child) == zmq.POLLIN

    async def _relay_verified(
        self, child_response: Response, child_identity: str
    ) -> Response:
        """Verify a delegated child's value on the upward path and relay it up.

        This is the agent-protocol form of the correctness spine: a parent
        verifies a child's response against its own verifier before accepting
        responsibility for it. If the child claimed verified but the value fails
        the parent's configured verifier, the response is demoted to an explicit,
        audited failure — never a verified success. The (possibly demoted)
        response is relayed up to the parent.
        """
        if (
            child_response.verified
            and self._verifier is not None
            and not self._verify(child_response.value, self._verifier)
        ):
            child_response = Response(
                correlation_id=child_response.correlation_id,
                kind=RESPONSE_ERROR,
                verified=False,
                node=self.identity,
                error=(
                    f"child {child_identity!r} returned a value that failed the "
                    "parent's verifier"
                ),
            )
        await self._send(child_response)
        return child_response

    def _maybe_delegate(self, directive: Directive) -> str | None:
        """Return the named child a run directive should be delegated to, else None.

        Delegation is explicit and mediated: a parent routes a ``run`` directive
        to a child only when the payload names one (``child`` field) and that
        child is a spawned child. If the directive names no child, or names an
        unknown child, the parent does not delegate (it handles the directive
        itself, or fails closed if it cannot). This is fail-closed: a directive
        cannot silently route to an arbitrary child.
        """
        if directive.kind != DIRECTIVE_RUN:
            return None
        target = directive.payload.get("child")
        if not isinstance(target, str) or not target:
            return None
        if target in self._children:
            return target
        # Unknown target: return it so the caller can fail closed explicitly.
        return target

    def _handle(self, directive: Directive) -> Response:
        """Handle a directive from the parent, dispatching by kind."""
        if directive.kind == DIRECTIVE_CONFIGURE:
            return self._configure(directive)
        if directive.kind == DIRECTIVE_RUN:
            return self._run_task(directive)
        if directive.kind == DIRECTIVE_SPAWN:
            return self._spawn(directive)
        if directive.kind == DIRECTIVE_REGISTER:
            return self._register_capability(directive)
        if directive.kind == DIRECTIVE_RESOLVE:
            return self._resolve_capability(directive)
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
        verifier = payload.get("verifier")
        self._verifier = verifier if isinstance(verifier, str) else self._verifier
        for name in payload.get("tasks", ()):
            entry = _resolve_entry(name)
            self._registry.register_entry(entry)
        for name in payload.get("verifiers", ()):
            entry = _resolve_entry(name)
            self._registry.register_entry(entry)
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    def _register_capability(self, directive: Directive) -> Response:
        """Handle a ``register`` directive: record capability metadata.

        This is the registry-as-agent write clamp. The registry is a passive
        catalog: it records the name, kind, and source location (URL) — never
        a callable, which cannot cross the JSON bus. Registration is explicit
        and audited; the agent records the entry so other agents can resolve it.
        """
        payload = directive.payload
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return self._error(directive, "register requires a 'name'")
        source_url = payload.get("source_url") or ""
        kind = payload.get("kind") or "python"
        entry = RegistryEntry(name=name, source_url=source_url, kind=kind)
        self._registry.register_entry(entry)
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    def _resolve_capability(self, directive: Directive) -> Response:
        """Handle a ``resolve`` directive: return a capability's location.

        This is the passive-catalog read: it returns the metadata (name, kind,
        source location) for a named capability. It never returns a callable and
        never executes anything — resolve only serves the location.
        """
        payload = directive.payload
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return self._error(directive, "resolve requires a 'name'")
        entry = self._registry.entry(name)
        if entry is None:
            return self._error(directive, f"unknown capability {name!r}")
        verified = bool(entry.source_url)
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT if verified else RESPONSE_ERROR,
            value={"name": entry.name, "kind": entry.kind, "source_url": entry.source_url},
            verified=verified,
            node=self.identity,
            error=None if verified else f"capability {name!r} has no source location",
        )

    def _run_task(self, directive: Directive) -> Response:
        """Execute a task from the directive's payload.

        Idempotency: a directive is identified by its full fingerprint
        (correlation id + kind + payload), so a replayed directive returns the
        cached result instead of re-executing. If the same correlation id is
        reused for a *different* directive, that is a protocol violation and
        fails closed rather than silently serving stale data.
        """
        fingerprint = self._fingerprint(directive)
        if fingerprint in self._results:
            return self._results[fingerprint]
        # A correlation id must be unique per directive. If it was already used
        # for different work, fail closed instead of returning a stale result.
        key = (directive.correlation_id, directive.kind)
        if key in self._used_keys:
            return self._error(directive, "correlation id reused for different directive")

        task_name = directive.payload.get("task")
        args = directive.payload.get("args", {})
        verifier_name = directive.payload.get("verifier")

        if not isinstance(task_name, str):
            return self._error(directive, "run directive requires a 'task' name")

        task = self._registry.resolve(task_name)
        if task is None:
            return self._error(directive, f"unknown task {task_name!r}")

        source = self._registry.source(task_name) or ""

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
            source=source,
        )
        self._results[fingerprint] = response
        self._used_keys.add(key)
        return response

    @staticmethod
    def _fingerprint(directive: Directive) -> tuple[str, str, str]:
        """A stable identity for a directive: correlation id + kind + canonic payload.

        The payload is serialised with sorted keys so that two directives with
        the same correlation id, kind, and content map to the same fingerprint,
        while a different directive with the same correlation id does not. This
        is what makes idempotency safe against replay.
        """
        canonic = json.dumps(directive.payload, sort_keys=True, default=str)
        return (directive.correlation_id, directive.kind, canonic)

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

        The parent provisions a real child ``Agent``: it binds a ROUTER socket
        at the child's endpoint, creates the child with a config pointing at
        that endpoint, and stores both the socket and the child agent. The
        child is then initialised (its DEALER connects to the parent's ROUTER).
        This is mediated spawn — the parent creates and governs the child,
        rather than merely bookkeeping a socket.
        """
        payload = directive.payload
        child_identity = payload.get("identity")
        child_endpoint = payload.get("endpoint")
        if not child_identity or not child_endpoint:
            return self._error(directive, "spawn requires 'identity' and 'endpoint'")

        if self._context is None:
            return self._error(directive, "agent not initialised")

        # Idempotency: a replayed spawn reuses an already-provisioned child
        # rather than re-binding the endpoint (which would fail) or creating a
        # duplicate. Same identity -> same child, so retry/replay are safe.
        if child_identity in self._child_agents:
            return Response(
                correlation_id=directive.correlation_id,
                kind=RESPONSE_OK,
                verified=True,
                node=self.identity,
            )

        child_socket = self._context.socket(zmq.ROUTER)
        child_socket.bind(self._endpoint(child_endpoint))
        child = Agent(
            AgentConfig(
                identity=child_identity,
                parent_endpoint=child_endpoint,
                transport=self._config.transport,
                context=self._context,
            )
        )
        child.init()
        self._children[child_identity] = child_socket
        self._child_agents[child_identity] = child
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    async def delegate(self, child_identity: str, directive: Directive) -> None:
        """Route a directive down to a child agent (mediated delegation).

        The parent sends the directive to the child over the child's ROUTER
        socket. The child's response is later received by ``poll`` and relayed
        up. This is how a parent delegates work it cannot resolve locally.

        Raises:
            KeyError: If ``child_identity`` is not a spawned child.
        """
        child = self._children.get(child_identity)
        if child is None:
            raise KeyError(f"no spawned child {child_identity!r}")
        await child.send_multipart(
            [
                child_identity.encode(),
                directive.correlation_id.encode(),
                MESSAGE_DIRECTIVE.encode(),
                directive.kind.encode(),
                json.dumps(directive.payload).encode(),
            ]
        )

    async def _handle_child(self, child: zmq.asyncio.Socket) -> Response | None:
        """Handle a message from a child, verifying on the way up.

        A child sends an ack (delivery) then a response (completion). The ack
        merely confirms delivery down and carries no result, so it is consumed
        and skipped (returns None). The response is verified and returned.
        A malformed or non-response message fails closed.
        """
        frames = await child.recv_multipart()
        if len(frames) == 4:
            # Ack from child: [identity, correlation_id, kind=ack, payload=""].
            if frames[2].decode() == MESSAGE_ACK:
                return None
            return Response(
                correlation_id="",
                kind=RESPONSE_ERROR,
                verified=False,
                node=self.identity,
                error="malformed child message",
            )
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
            source=payload.get("source", ""),
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

    def _correlationless_error(self, message: str) -> Response:
        """Build an explicit failure for a message whose correlation id could
        not be read (e.g. a malformed or unparseable directive).

        The protocol requires a non-empty correlation id, so a fixed sentinel
        is used; the error is audited and never a verified success.
        """
        return Response(
            correlation_id="malformed",
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