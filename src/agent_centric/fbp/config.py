"""The minimal bootstrap configuration for an agent.

An agent is born knowing only two things: its identity, and how to reach its
parent/creator. Everything else — domain, rules, verifier, task, children —
arrives dynamically as directives.

The config is deliberately minimal and immutable. It is the *only* static
configuration an agent has; all further configuration is dynamic, via the
directive/response protocol over the transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .message import PROTOCOL_VERSION


@dataclass(frozen=True)
class AgentConfig:
    """The minimal bootstrap configuration for an agent.

    Attributes:
        identity: The agent's routing identity / name.
        parent_endpoint: The endpoint of the agent's parent (the umbilical).
        transport: The transport to use (inproc, tcp, ipc).
        protocol: The protocol version the agent speaks.
        context: An optional shared ZeroMQ context. When None, the agent
            creates (and owns) its own context. A shared context is required
            for ``inproc://`` links, which only connect within one context.
    """

    identity: str
    parent_endpoint: str
    transport: str = "inproc"
    protocol: str = PROTOCOL_VERSION
    context: Any | None = None