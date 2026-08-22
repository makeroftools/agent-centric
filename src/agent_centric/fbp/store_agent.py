"""A domain store/registry agent that owns a durable state store.

This is the concrete realization of "state can be controlled by a
domain-specific agent": a ``StoreAgent`` is the **single writer** to a
``StateStore`` it owns, and serves it to other agents over the ``run``
operations (``store_get`` / ``store_set``).

Why an agent and not a shared file?

- **Single-writer mediation.** Only the store agent writes its store. Other
  agents read/write *through* it (via the parent's mediated delegation), so
  there is no ungoverned concurrent access to a resource.
- **Grant via context.** The store path is granted by the parent in
  ``configure`` (``state=...``); the store agent never opens a file it was
  not granted.
- **Grant via key allowlist.** The set of keys it may serve is delivered by
  the parent (``store_keys`` in configure). A key outside the grant fails
  closed — the store does not serve arbitrary keys.
- **Idempotency + audit preserved.** Writes are idempotent (keyed by the
  directive fingerprint), and every served operation is recorded in the
  agent's own local audit (the start of chain audit for that resource). The
  correctness spine (parent re-verifies on the way up) still applies to
  every response it relays.
"""

from __future__ import annotations

from typing import Any

from . import store as _store
from .agent import Agent
from .message import (
    DIRECTIVE_RUN,
    RESPONSE_OK,
    RESPONSE_RESULT,
    Directive,
    Response,
)

# The run-task names this store agent serves.
STORE_GET = "store_get"
STORE_SET = "store_set"


class StoreAgent(Agent):
    """A domain agent that owns a ``StateStore`` and serves it via ``run``."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        # The keys this store is allowed to serve, granted by the parent (an
        # explicit allowlist). A key outside the grant fails closed.
        self._store_keys: set[str] = set()

    def _handle(self, directive: Directive) -> Response:
        if directive.kind == DIRECTIVE_RUN:
            task = directive.payload.get("task")
            if task == STORE_SET:
                return self._op_store_set(directive)
            if task == STORE_GET:
                return self._op_store_get(directive)
        return super()._handle(directive)

    def _configure_extra(self, payload: dict[str, Any]) -> None:
        """Pick up the parent-granted key allowlist from a configure directive."""
        keys = payload.get("store_keys", ())
        if isinstance(keys, (list, tuple)):
            self._store_keys = {k for k in keys if isinstance(k, str)}

    def _is_served(self, key: str) -> bool:
        return key in self._store_keys

    def _op_args(self, directive: Directive) -> dict[str, Any]:
        """The run payload's arguments (``args`` dict, or the payload itself)."""
        args = directive.payload.get("args")
        if isinstance(args, dict):
            return args
        return dict(directive.payload)

    def _op_store_set(self, directive: Directive) -> Response:
        store = self._state_store
        if store is None:
            return self._error(directive, "store agent has no granted state store")
        args = self._op_args(directive)
        key = args.get("key")
        value = args.get("value")
        if not isinstance(key, str) or not key:
            return self._error(directive, "store_set requires a 'key'")
        if not self._is_served(key):
            return self._error(
                directive, f"store key {key!r} is not granted to this store"
            )
        fingerprint = "|".join(self._fingerprint(directive))
        try:
            store.set(key, value, fingerprint=fingerprint)
        except _store.StoreError as exc:
            return self._error(directive, f"store write failed: {exc}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    def _op_store_get(self, directive: Directive) -> Response:
        store = self._state_store
        if store is None:
            return self._error(directive, "store agent has no granted state store")
        args = self._op_args(directive)
        key = args.get("key")
        if not isinstance(key, str) or not key:
            return self._error(directive, "store_get requires a 'key'")
        if not self._is_served(key):
            return self._error(
                directive, f"store key {key!r} is not granted to this store"
            )
        try:
            value = store.get(key)
        except _store.StoreError as exc:
            return self._error(directive, f"store read failed: {exc}")
        if value is None:
            return self._error(directive, f"store key {key!r} not found")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=value,
            verified=True,
            node=self.identity,
        )