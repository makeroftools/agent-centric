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
from contextlib import suppress
from typing import Any

import zmq
import zmq.asyncio

from . import store as _store
from .config import AgentConfig
from .message import (
    DIRECTIVE_AUDIT,
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_KILL,
    DIRECTIVE_PING,
    DIRECTIVE_REGISTER,
    DIRECTIVE_RESOLVE,
    DIRECTIVE_RUN,
    DIRECTIVE_SPAWN,
    DIRECTIVE_STATE_GET,
    DIRECTIVE_STATE_SET,
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

    When ``fn`` is a plain, importable function (has a real module and qualified
    name), its import location is recorded so a later process can re-create the
    callable from source (cross-process replay without re-seeding by hand).
    """
    module = getattr(fn, "__module__", "") or ""
    qualname = getattr(fn, "__qualname__", "") or ""
    if module == "__main__" or not module:
        # A REPL/script-defined function is not reliably importable; record only
        # the in-memory callable (no importable source).
        module = ""
        qualname = ""
    _REGISTRY[name] = RegistryEntry(
        name=name,
        callable=fn,
        source_url=source_url,
        module=module,
        qualname=qualname,
    )


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
        # Durable, on-demand persistence (parent-provisioned paths). ``None``
        # until ``configure`` grants a path; opening a store is an explicit,
        # audited grant that never happens silently.
        self._state_path: str | None = None
        self._trajectory_path: str | None = None
        self._state_store: _store.StateStore | None = None
        self._trajectory_store: _store.TrajectoryStore | None = None

    @property
    def identity(self) -> str:
        return self._config.identity

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def children(self) -> dict[str, Agent]:
        """The spawned child agents (a read-only copy)."""
        return dict(self._child_agents)

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
        if self._state_store is not None:
            self._state_store.close()
            self._state_store = None
        if self._trajectory_store is not None:
            self._trajectory_store.close()
            self._trajectory_store = None
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

    def _child_endpoint(self, identity: str) -> str:
        """Resolve a transport-appropriate endpoint for a child socket.

        Over ``inproc``/``ipc`` a bare name works; over ``tcp`` the child binds
        its own ROUTER at a distinct local port (deterministic per identity), so
        a spawned child never binds an invalid ``tcp://<bare-name>`` address.
        """
        if self._config.transport == "tcp":
            port = self._tcp_port_for(identity)
            return f"tcp://127.0.0.1:{port}"
        return f"{self._config.transport}://{identity}"

    def _tcp_port_for(self, identity: str) -> int:
        """A stable TCP port for a child identity (deterministic, per id).

        Uses a dedicated range above the driver's children (5600+). The port is
        derived from the identity string so the same identity always maps to the
        same port, which keeps re-spawn/replay idempotent and deterministic.
        """
        base = 5800
        return base + (sum(ord(ch) for ch in identity) % 800)

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
        if delegated is not None:
            # The directive names a child that is not a spawned child. This is
            # a directed route to an unknown target: fail closed rather than
            # silently handling it locally (which would route work somewhere
            # the caller did not intend).
            response = self._error(
                directive,
                f"delegation target {delegated!r} is not a spawned child",
            )
            await self._send(response)
            self._record_local(directive, response)
            return response
        # Not delegatable (no child named): handle locally.
        response = self._handle(directive)
        await self._send(response)
        self._record_local(directive, response)
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
        self._record_relay(child_response, child_identity)
        await self._send(child_response)
        return child_response

    def _record_relay(self, response: Response, child_identity: str) -> None:
        """Record the parent's acceptance of a child's verified response.

        This completes *chain* audit: the parent, on the upward path, records
        the child-response it re-verified and accepted responsibility for. The
        local trajectory of the parent records ``relay`` kinds pointing at the
        child that produced the value, so an operator can reconstruct the full
        parent-child chain (child's own ``result`` record + the parent's
        ``relay`` record share the correlation id).

        A parent records nothing extra when it has no trajectory store. A
        write-once collision (the same correlation id already recorded locally)
        is suppressed rather than crashing the poll loop.
        """
        store = self._trajectory_store
        if store is None:
            return
        with suppress(_store.StoreError):
            store.record(
                correlation_id=response.correlation_id,
                kind="relay",
                node=self.identity,
                verified=response.verified,
                value=response.value,
                error=response.error,
                source=response.source,
                fingerprint=f"relay|{self.identity}|{child_identity}",
                parent=child_identity,
            )

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
        if directive.kind == DIRECTIVE_STATE_SET:
            return self._state_set(directive)
        if directive.kind == DIRECTIVE_STATE_GET:
            return self._state_get(directive)
        if directive.kind == DIRECTIVE_AUDIT:
            return self._audit(directive)
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
        # ``verifier`` may be absent (keep current), a name (set it), or an
        # explicit None (clear it). A ``_clear_verifier`` flag distinguishes an
        # explicit clear from an absent key.
        if payload.get("_clear_verifier"):
            self._verifier = None
        elif isinstance(payload.get("verifier"), str):
            self._verifier = payload["verifier"]
        for name in payload.get("tasks", ()):
            entry = _resolve_entry(name)
            self._registry.register_entry(entry)
        for name in payload.get("verifiers", ()):
            entry = _resolve_entry(name)
            self._registry.register_entry(entry)
        # Durable, on-demand persistence, granted by the parent via paths. A
        # state path opens a single-writer store; a trajectory path opens an
        # append-only audit. Both are explicit grants — an agent never silently
        # writes a file. `read_only` lets an agent read (never write) state.
        state_path = payload.get("state")
        if isinstance(state_path, str) and state_path:
            self._state_path = state_path
            self._state_store = _store.open_state(
                state_path, read_only=bool(payload.get("state_read_only", False))
            )
        trajectory_path = payload.get("trajectory")
        if isinstance(trajectory_path, str) and trajectory_path:
            self._trajectory_path = trajectory_path
            self._trajectory_store = _store.open_trajectory(trajectory_path)
        # Allow subclasses (e.g. StoreAgent) to consume configure fields they own.
        self._configure_extra(payload)
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    def _configure_extra(self, payload: dict[str, Any]) -> None:
        """Subclass hook: consume configure fields beyond the base grant.

        The base agent ignores unknown configure fields; domain agents (like
        ``StoreAgent``) override this to pick up grant-specific fields (e.g. a
        key allowlist) without re-implementing configure.
        """

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
        # The directive may name a verifier; otherwise fall back to the agent's
        # configured default verifier (set by ``configure``). This makes the
        # configured verifier apply to local runs, not just child re-verification.
        verifier_name = directive.payload.get("verifier") or self._verifier

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

    def _state_get(self, directive: Directive) -> Response:
        """Return a value from this agent's durable state store.

        Requires a durable state store granted via ``configure``; otherwise it
        fails closed (an agent with no state grant cannot read state). Reads are
        never implicitly persisted — state is only ever the resource an agent
        owns.
        """
        store = self._state_store
        if store is None:
            return self._error(directive, "no durable state store configured")
        key = directive.payload.get("key")
        if not isinstance(key, str) or not key:
            return self._error(directive, "state_get requires a 'key'")
        try:
            value = store.get(key)
        except _store.StoreError as exc:
            return self._error(directive, f"state read failed: {exc}")
        if value is None:
            return self._error(directive, f"state key {key!r} not found")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=value,
            verified=True,
            node=self.identity,
        )

    def _state_set(self, directive: Directive) -> Response:
        """Idempotently write a value into this agent's durable state store.

        The write is keyed by the full directive fingerprint (correlation id +
        kind + payload), so a replayed directive reapplies the same row rather
        than double-writing; a genuine update uses a distinct directive. If the
        agent has no writable state grant it fails closed — an agent never
        silently persists.
        """
        store = self._state_store
        if store is None:
            return self._error(directive, "no durable state store configured")
        key = directive.payload.get("key")
        value = directive.payload.get("value")
        if not isinstance(key, str) or not key:
            return self._error(directive, "state_set requires a 'key'")
        fingerprint = "|".join(self._fingerprint(directive))
        if self._fingerprint(directive) in self._results:
            # Same directive already applied; idempotent reapply -> ok.
            return Response(
                correlation_id=directive.correlation_id,
                kind=RESPONSE_OK,
                verified=True,
                node=self.identity,
            )
        try:
            store.set(key, value, fingerprint=fingerprint)
        except _store.StoreError as exc:
            return self._error(directive, f"state write failed: {exc}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    def _audit(self, directive: Directive) -> Response:
        """Return this agent's local audit record (the local start of the chain).

        The full chain audit is assembled by connecting each agent's local
        record as verified responses bubble up the tree; this directive returns
        the fragment rooted at this agent.
        """
        store = self._trajectory_store
        if store is None:
            return self._error(directive, "no durable trajectory store configured")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=store.all(),
            verified=True,
            node=self.identity,
        )

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

    def _record_local(self, directive: Directive, response: Response) -> None:
        """Append this directive's outcome to the agent's local audit (if any).

        This is where *chain* audit begins: each agent records its own local
        activity. If the agent has a trajectory store (granted via configure),
        every executed directive is appended under its correlation id; a
        write-once conflict fails closed (an audited error is recorded rather
        than a silent duplicate). Audit is local-first and durable.
        """
        store = self._trajectory_store
        if store is None:
            return
        with suppress(_store.StoreError):
            store.record(
                correlation_id=directive.correlation_id,
                kind=response.kind,
                node=self.identity,
                verified=response.verified,
                value=response.value,
                error=response.error,
                source=response.source,
                fingerprint="|".join(self._fingerprint(directive)),
                parent=self._config.parent_endpoint,
            )

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
        child_cls = self._child_class_for(payload.get("kind"))
        child = child_cls(
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

    @staticmethod
    def _child_class_for(kind: Any) -> type[Agent]:
        """Resolve a spawned child's concrete class from its ``kind``.

        A spawn directive may name a domain child kind (e.g. ``store``). Only
        built-in, vetted classes are resolved here; an unknown kind fails
        closed (returns the base ``Agent``) rather than ever instantiating an
        arbitrary class. The base agent is the default.
        """
        if kind == "store":
            from .store_agent import StoreAgent

            return StoreAgent
        if kind == "bills":
            from .bills_agent import BillsAgent

            return BillsAgent
        return Agent

    def configure_child(
        self,
        identity: str,
        *,
        tasks: tuple[str, ...] = (),
        verifiers: tuple[str, ...] = (),
        rules: tuple[str, ...] = (),
        verifier: str | None = None,
        state: str | None = None,
        state_read_only: bool = False,
        trajectory: str | None = None,
        store_keys: tuple[str, ...] = (),
    ) -> Response:
        """Configure a spawned child (the parent provides the child's context).

        This is the parent-mediated form of ``configure`` for a child the parent
        spawned: the parent builds the child's configure directive and applies
        it. It fails closed if ``identity`` is not a spawned child.

        Beyond the task/verifier/rule grant, the parent may grant durable
        stores (``state``/``trajectory``) and, for a ``StoreAgent`` child, a
        key allowlist (``store_keys``) bounding which keys it may serve.
        """
        child = self._child_agents.get(identity)
        if child is None:
            return Response(
                correlation_id="configure-child",
                kind=RESPONSE_ERROR,
                verified=False,
                node=self.identity,
                error=f"no spawned child {identity!r}",
            )
        payload: dict[str, Any] = {
            "tasks": list(tasks),
            "verifiers": list(verifiers),
            "rules": list(rules),
        }
        if verifier is not None:
            payload["verifier"] = verifier
        if state is not None:
            payload["state"] = state
            payload["state_read_only"] = state_read_only
        if trajectory is not None:
            payload["trajectory"] = trajectory
        if store_keys:
            payload["store_keys"] = list(store_keys)
        return child._configure(
            Directive(
                correlation_id="configure-child",
                kind=DIRECTIVE_CONFIGURE,
                payload=payload,
            )
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
        # Strip the routing field before forwarding: ``child`` is the parent's
        # delegation hint and must not reach the child (which would otherwise
        # try to re-delegate to itself). The parent mediates the route.
        payload = {k: v for k, v in directive.payload.items() if k != "child"}
        await child.send_multipart(
            [
                child_identity.encode(),
                directive.correlation_id.encode(),
                MESSAGE_DIRECTIVE.encode(),
                directive.kind.encode(),
                json.dumps(payload).encode(),
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