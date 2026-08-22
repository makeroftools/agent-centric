"""A high-level, synchronous driver over the agent-centric FBP protocol.

This is the *easy UX* layer. The raw ``Agent`` speaks the directive/response
protocol over ZeroMQ frames; ``FbpDriver`` wraps that in a plain, synchronous
API so a caller can build and drive a tree of agents without touching sockets,
frames, or an event loop.

The driver owns a root ``Agent`` (the shell of the tree) and exposes the
directive kinds as methods:

- ``register`` / ``resolve`` — the passive registry-as-agent catalog.
- ``configure`` / ``configure_child`` — parent provides context (rules,
  verifiers, task allowlist).
- ``run`` — execute a task, optionally delegating to a named child.
- ``spawn`` — provision a real child agent.
- ``ping`` / ``kill`` — liveness and teardown.

Every method returns a ``Response`` (``.value`` / ``.verified`` / ``.node`` /
``.error`` / ``.source``). A response that failed verification is an explicit,
audited failure — never a verified success. The driver is deterministic and
offline-testable over ``inproc://``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import zmq
import zmq.asyncio

from .agent import Agent, _resolve_entry, register_callable
from .config import AgentConfig
from .message import (
    DIRECTIVE_AUDIT,
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_KILL,
    DIRECTIVE_PING,
    DIRECTIVE_RESOLVE,
    DIRECTIVE_RUN,
    DIRECTIVE_SPAWN,
    DIRECTIVE_STATE_GET,
    DIRECTIVE_STATE_SET,
    MESSAGE_DIRECTIVE,
    Response,
)

# A task is a registered callable; a verifier is a pure predicate.
Task = Callable[..., Any]
Verifier = Callable[[Any], bool]


class FbpDriver:
    """A synchronous driver over a root agent and its tree.

    The driver binds a ROUTER at the root endpoint, creates a root ``Agent``
    that connects a DEALER to it, and sends directives on the caller's behalf.
    Each method blocks until the matching response arrives (or the agent fails
    closed), so the caller never manages the poll loop.

    Args:
        endpoint: The root channel name (default ``root``).
        transport: The transport to use (``inproc``, ``tcp``, ``ipc``).
        identity: The root agent's identity (default ``root``).
    """

    def __init__(
        self,
        *,
        endpoint: str = "root",
        transport: str = "inproc",
        identity: str = "root",
    ) -> None:
        self._transport = transport
        self._endpoint = f"{transport}://{endpoint}"
        # The driver owns a private, dedicated event loop. It is set as the
        # current loop so ZeroMQ's async sockets resolve the right loop (in
        # Python 3.13 ``get_event_loop`` raises if none is set, which otherwise
        # fails spuriously when other tests have torn down loop state).
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._context = zmq.asyncio.Context()
        self._root_socket = self._context.socket(zmq.ROUTER)
        self._root_socket.bind(self._endpoint)
        self._root = Agent(
            AgentConfig(
                identity=identity,
                parent_endpoint=self._endpoint,
                transport=transport,
                context=self._context,
            )
        )
        self._root.init()
        self._seq = 0
        self._child_base = 0
        # Over ``tcp``/``ipc`` the DEALER link connects asynchronously; retry a
        # bounded number of times with a short settle before giving up.
        self._settle_attempts = 5
        self._settle_delay = 0.05
        self._poll_timeout = 0.1
        if transport != "inproc":
            # Give the async transport link a beat to establish before the
            # first directive is sent.
            self._loop.run_until_complete(asyncio.sleep(0.2))

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Teardown the root agent, its sockets, and the event loop."""
        self._root.kill()
        self._root_socket.close(0)
        self._context.term()
        self._loop.close()

    def __enter__(self) -> FbpDriver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level round-trip ----------------------------------------------

    def _correlation(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _roundtrip(self, kind: str, payload: dict[str, Any], prefix: str) -> Response:
        """Send a directive to the root and step the poll loop until its response.

        A delegated directive is routed down to a child agent, so the whole
        tree must be polled (root and every descendant child) until the
        matching response bubbles back up.

        Over ``tcp``/``ipc`` the DEALER link is established asynchronously, so a
        message sent before the peer's registration can be dropped by the
        transport. Per the protocol, we retry until the response arrives — the
        re-send is idempotency-safe (the same directive fingerprint returns the
        cached result rather than re-executing).
        """
        correlation_id = self._correlation(prefix)
        directive_frames = [
            self._root.identity.encode(),
            correlation_id.encode(),
            MESSAGE_DIRECTIVE.encode(),
            kind.encode(),
            json.dumps(payload).encode(),
        ]
        # Bounded retry for transport asynchrony; a settled link answers on the
        # first attempt, so this loop normally exits immediately.
        attempts = self._settle_attempts
        for attempt in range(1, attempts + 1):
            self._root_socket.send_multipart(directive_frames)
            responses = self._poll_tree_bounded()
            for response in responses:
                if response.correlation_id == correlation_id:
                    return response
            if attempt < attempts:
                time.sleep(self._settle_delay)
        raise RuntimeError(
            f"no response from root for {prefix!r} after {attempts} attempts"
        )

    def _poll_tree_bounded(self) -> list[Response]:
        """Poll the tree for one window; return the root's responses.

        Children are polled first so a child's response is available to be
        relayed by the root in the same step. A poll window that exhausts with
        no event simply returns an empty list — the caller retries.
        """
        self._poll_children(self._root)
        return self._loop.run_until_complete(
            self._root.poll(timeout=self._poll_timeout)
        )

    def _poll_children(self, agent: Agent) -> None:
        """Poll ``agent``'s children depth-first, discarding their responses."""
        for child in agent.children.values():
            self._poll_children(child)
            self._loop.run_until_complete(child.poll(timeout=self._poll_timeout))

    # -- registry-as-agent -------------------------------------------------

    def register(
        self, name: str, fn: Task, *, source_url: str = ""
    ) -> None:
        """Register a callable so directives can reference it (chain-audited).

        The callable is registered in the module-level catalog (so directives
        can resolve it by name) and immediately made available to the root
        agent's own registry, so ``resolve`` works without a prior ``configure``.
        """
        register_callable(name, fn, source_url=source_url)
        self._root._registry.register_entry(_resolve_entry(name))

    def resolve(self, name: str) -> Response:
        """Return the passive-catalog location for a named capability."""
        return self._roundtrip(
            DIRECTIVE_RESOLVE, {"name": name}, prefix="resolve"
        )

    def state_set(self, key: str, value: Any) -> Response:
        """Idempotently persist ``value`` at ``key`` in the root's state store.

        A replayed directive (same key/value/fingerprint) is a no-op; a
        distinct directive is a real update. Requires a state store granted via
        ``configure(state=...)``.
        """
        return self._roundtrip(
            DIRECTIVE_STATE_SET,
            {"key": key, "value": value},
            prefix="state-set",
        )

    def state_get(self, key: str) -> Response:
        """Return the value at ``key`` from the root's durable state store."""
        return self._roundtrip(DIRECTIVE_STATE_GET, {"key": key}, prefix="state-get")

    def audit(self) -> Response:
        """Return the root agent's local audit record (the chain's local start)."""
        return self._roundtrip(DIRECTIVE_AUDIT, {}, prefix="audit")

    # -- configuration -----------------------------------------------------

    def configure(
        self,
        *,
        tasks: tuple[str, ...] = (),
        verifiers: tuple[str, ...] = (),
        rules: tuple[str, ...] = (),
        verifier: str | None = None,
        state: str | None = None,
        state_read_only: bool = False,
        trajectory: str | None = None,
    ) -> Response:
        """Configure the root agent's rules, task allowlist, verifier, and
        optional durable stores.

        Args:
            tasks/verifiers/rules/verifier: The task allowlist, verifier list,
                hard rules, and default verifier for the root agent.
            state: Optional durable state file path grant (a single-writer
                key/value store the agent owns).
            state_read_only: If true, open the state store read-only (read-only
                grant; writes fail closed).
            trajectory: Optional durable trajectory file path grant (an
                append-only local audit — the start of chain audit).
        """
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
        return self._roundtrip(DIRECTIVE_CONFIGURE, payload, prefix="configure")

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
        """Configure a spawned child (the parent provides the child's context)."""
        return self._root.configure_child(
            identity,
            tasks=tasks,
            verifiers=verifiers,
            rules=rules,
            verifier=verifier,
            state=state,
            state_read_only=state_read_only,
            trajectory=trajectory,
            store_keys=store_keys,
        )

    # -- execution ---------------------------------------------------------

    def run(
        self,
        task: str,
        args: dict[str, Any] | None = None,
        *,
        verifier: str | None = None,
        child: str | None = None,
    ) -> Response:
        """Run a task, optionally delegating to a named child.

        Returns:
            A ``Response``. If ``child`` is given and is a spawned child, the
            directive is routed down and the child's verified response is
            relayed up (re-verified by the parent). Otherwise the root resolves
            the task locally.
        """
        payload: dict[str, Any] = {"task": task, "args": args or {}}
        if verifier is not None:
            payload["verifier"] = verifier
        if child is not None:
            payload["child"] = child
        return self._roundtrip(DIRECTIVE_RUN, payload, prefix="run")

    def spawn(
        self,
        identity: str,
        endpoint: str | None = None,
        *,
        kind: str | None = None,
    ) -> Response:
        """Provision a real child agent (mediated spawn).

        Args:
            identity: The child agent's identity.
            endpoint: The child's endpoint. If omitted (or a bare name), it is
                resolved against the driver's transport.
            kind: An optional domain child kind (e.g. ``store`` to spawn a
                ``StoreAgent``). Defaults to the base ``Agent``.
        """
        if endpoint is None or "://" not in endpoint:
            endpoint = self._child_endpoint(identity)
        payload: dict[str, Any] = {"identity": identity, "endpoint": endpoint}
        if kind is not None:
            payload["kind"] = kind
        return self._roundtrip(DIRECTIVE_SPAWN, payload, prefix="spawn")

    def _child_endpoint(self, identity: str) -> str:
        """Return a transport-appropriate child endpoint for ``identity``."""
        if self._transport == "tcp":
            # Each child binds its own ROUTER at a distinct local port.
            self._child_base += 1
            port = 5600 + self._child_base * 2
            return f"tcp://127.0.0.1:{port}"
        name = f"children-{identity}"
        return f"{self._transport}://{name}"

    # -- liveness / teardown ------------------------------------------------

    def ping(self) -> Response:
        """Return an ``ok`` response if the root agent is alive."""
        return self._roundtrip(DIRECTIVE_PING, {}, prefix="ping")

    def kill(self) -> Response:
        """Kill the root agent (teardown)."""
        return self._roundtrip(DIRECTIVE_KILL, {}, prefix="kill")