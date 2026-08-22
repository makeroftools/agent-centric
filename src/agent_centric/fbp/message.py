"""The message contract for the agent-centric FBP tree.

A message is the unit that travels the bus. It is a versioned, enforced
contract. There are three message kinds:

- **Directive** — a complete, self-contained task specification sent down the
  tree.
- **Ack** — a dumb acknowledgment meaning "message received", sent back to the
  originator immediately upon receipt, before any work begins (like a TCP ACK).
- **Response** — the outcome of a directive, sent when the directive is
  *completed*.

Every message echoes its directive's correlation id, which is what makes async
matching deterministic and idempotency checkable.

This module defines the message dataclasses and the validators that enforce
the wire contract. Messages that do not conform are rejected — never silently
accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The protocol version every agent speaks.
PROTOCOL_VERSION = "directive-response/v1"

# Message kinds (the envelope's ``kind`` frame).
MESSAGE_DIRECTIVE = "directive"
MESSAGE_ACK = "ack"
MESSAGE_RESPONSE = "response"

# Directive kinds.
DIRECTIVE_CONFIGURE = "configure"
DIRECTIVE_RUN = "run"
DIRECTIVE_SPAWN = "spawn"
DIRECTIVE_PING = "ping"
DIRECTIVE_KILL = "kill"
DIRECTIVE_REGISTER = "register"
DIRECTIVE_RESOLVE = "resolve"
DIRECTIVE_STATE_GET = "state_get"
DIRECTIVE_STATE_SET = "state_set"
DIRECTIVE_AUDIT = "audit"
_DIRECTIVE_KINDS = frozenset(
    {
        DIRECTIVE_CONFIGURE,
        DIRECTIVE_RUN,
        DIRECTIVE_SPAWN,
        DIRECTIVE_PING,
        DIRECTIVE_KILL,
        DIRECTIVE_REGISTER,
        DIRECTIVE_RESOLVE,
        DIRECTIVE_STATE_GET,
        DIRECTIVE_STATE_SET,
        DIRECTIVE_AUDIT,
    }
)

# Response kinds.
RESPONSE_OK = "ok"
RESPONSE_RESULT = "result"
RESPONSE_ERROR = "error"
RESPONSE_TELEMETRY = "telemetry"
_RESPONSE_KINDS = frozenset(
    {RESPONSE_OK, RESPONSE_RESULT, RESPONSE_ERROR, RESPONSE_TELEMETRY}
)


@dataclass(frozen=True)
class Directive:
    """A complete, self-contained unit of work sent down the tree.

    Attributes:
        correlation_id: Unique id tying this directive to its ack/response(s).
        kind: The directive kind (configure, run, spawn, ping, kill).
        payload: The directive's payload (JSON-serializable).
        protocol: The protocol version this directive speaks.
    """

    correlation_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    protocol: str = PROTOCOL_VERSION


@dataclass(frozen=True)
class Ack:
    """A dumb acknowledgment that a directive was received.

    An Ack is sent back to the originator immediately upon receipt, before any
    work begins. It carries no result and implies nothing about completion —
    it only confirms delivery (like a TCP ACK). The ``Response`` is the message
    that reports completion.

    Attributes:
        correlation_id: Echoes the acknowledged directive's correlation id.
        node: The node that received (acknowledged) the directive.
        protocol: The protocol version this ack speaks.
    """

    correlation_id: str
    node: str = ""
    protocol: str = PROTOCOL_VERSION


@dataclass(frozen=True)
class Response:
    """The outcome of a directive, sent when the directive is completed.

    Attributes:
        correlation_id: Echoes the directive's correlation id.
        kind: The response kind (ok, result, error, telemetry).
        value: The response value (verified) or None.
        verified: True if the value passed verification on the upward path.
        node: The node that produced this response (responsibility/audit).
        error: A human-readable error message on failure, else None.
        source: The source location (URL) of the callable that produced this
            response, for chain audit. Empty when not applicable.
        protocol: The protocol version this response speaks.
    """

    correlation_id: str
    kind: str
    value: Any = None
    verified: bool = False
    node: str = ""
    error: str | None = None
    source: str = ""
    protocol: str = PROTOCOL_VERSION


class ProtocolError(ValueError):
    """A message violated the directive/response protocol."""


def validate_directive(msg: Directive) -> None:
    """Validate a directive against the protocol contract.

    Raises:
        ProtocolError: If the directive violates the contract.
    """
    if msg.protocol != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol {msg.protocol!r} (expected {PROTOCOL_VERSION!r})"
        )
    if not msg.correlation_id:
        raise ProtocolError("directive is missing a correlation id")
    if msg.kind not in _DIRECTIVE_KINDS:
        raise ProtocolError(f"unknown directive kind {msg.kind!r}")


def validate_ack(msg: Ack) -> None:
    """Validate an ack against the protocol contract.

    Raises:
        ProtocolError: If the ack violates the contract.
    """
    if msg.protocol != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol {msg.protocol!r} (expected {PROTOCOL_VERSION!r})"
        )
    if not msg.correlation_id:
        raise ProtocolError("ack is missing a correlation id")


def validate_response(msg: Response) -> None:
    """Validate a response against the protocol contract.

    Raises:
        ProtocolError: If the response violates the contract.
    """
    if msg.protocol != PROTOCOL_VERSION:
        raise ProtocolError(
            f"unsupported protocol {msg.protocol!r} (expected {PROTOCOL_VERSION!r})"
        )
    if not msg.correlation_id:
        raise ProtocolError("response is missing a correlation id")
    if msg.kind not in _RESPONSE_KINDS:
        raise ProtocolError(f"unknown response kind {msg.kind!r}")
    if msg.kind == RESPONSE_RESULT and not msg.verified:
        raise ProtocolError("a 'result' response must be verified")
    if msg.kind == RESPONSE_ERROR and msg.verified:
        raise ProtocolError("an 'error' response must not be verified")
    if not isinstance(msg.source, str):
        raise ProtocolError("response source must be a string")