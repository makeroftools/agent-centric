"""Agent-centric FBP foundation (branch `agent-centric-fbp`).

This package is the pivot to a truly agent-centric, flow-based architecture.
It implements the deterministic core of the model:

- a rooted, recursive **tree of nodes** (no central AgentManager),
- hierarchical **context** as the governance mechanism,
- the **init / run / kill** node contract,
- the **shell** as the root node,
- **verification on the upward path** (the correctness spine),
- the **directive/response protocol** as the crux (the language agents speak),
- the **abstract Agent** (one type) with a steppable async poll loop over a
  dynamic channel set,
- **ZeroMQ** (`zmq_poll`) as the first-class comms channel,
- the **fractal principle**: every task is an agent, recursively, down to
  individual instructions,
- **Critical Path Method (CPM)** as a first-class, deterministic, read-only
  observational tool.

The core is pure, deterministic, and offline-testable via ``inproc://``.
Real distribution (``tcp://``/``ipc://``) and a FastAPI layer are deferred
adapters (see ``spec.md`` and ``protocol.md``).
"""

from .agent import Agent, register_callable
from .config import AgentConfig
from .context import Context, Verifier
from .critical_path import CpmAnalysis, CpmError, CpmNode, analyse_cpm, cpm_from_dict
from .driver import FbpDriver
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
    PROTOCOL_VERSION,
    RESPONSE_ERROR,
    RESPONSE_OK,
    RESPONSE_RESULT,
    RESPONSE_TELEMETRY,
    Ack,
    Directive,
    ProtocolError,
    Response,
    validate_ack,
    validate_directive,
    validate_response,
)
from .node import AgentNode, Node
from .node import Response as NodeResponse
from .shell import Shell
from .store import StateStore, StoreError, TrajectoryStore, open_state, open_trajectory
from .store_agent import STORE_GET, STORE_SET, StoreAgent

__all__ = [
    # Agent
    "Agent",
    "AgentConfig",
    "FbpDriver",
    "register_callable",
    # Context / governance
    "Context",
    "Verifier",
    # Protocol
    "PROTOCOL_VERSION",
    "MESSAGE_DIRECTIVE",
    "MESSAGE_ACK",
    "MESSAGE_RESPONSE",
    "DIRECTIVE_CONFIGURE",
    "DIRECTIVE_RUN",
    "DIRECTIVE_SPAWN",
    "DIRECTIVE_PING",
    "DIRECTIVE_KILL",
    "DIRECTIVE_REGISTER",
    "DIRECTIVE_RESOLVE",
    "DIRECTIVE_STATE_GET",
    "DIRECTIVE_STATE_SET",
    "DIRECTIVE_AUDIT",
    "RESPONSE_OK",
    "RESPONSE_RESULT",
    "RESPONSE_ERROR",
    "RESPONSE_TELEMETRY",
    "Directive",
    "Ack",
    "Response",
    "ProtocolError",
    "validate_directive",
    "validate_ack",
    "validate_response",
    # Foundation node contract
    "AgentNode",
    "Node",
    "NodeResponse",
    "Shell",
    # Durable, on-demand persistence (state + trajectory/audit)
    "StateStore",
    "TrajectoryStore",
    "StoreError",
    "open_state",
    "open_trajectory",
    "StoreAgent",
    "STORE_GET",
    "STORE_SET",
    # CPM: a read-only, deterministic capability (not an agent)
    "CpmAnalysis",
    "CpmNode",
    "CpmError",
    "analyse_cpm",
    "cpm_from_dict",
]