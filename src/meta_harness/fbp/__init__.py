"""Agent-centric FBP foundation (branch `agent-centric-fbp`).

This package is the first concrete step of the pivot to a truly agent-centric,
flow-based architecture. It implements the deterministic core of the model:

- a rooted, recursive **tree of nodes** (no central AgentManager),
- hierarchical **context** as the governance mechanism,
- the **init / run / kill** node contract,
- the **shell** as the root node,
- **verification on the upward path** (the correctness spine).

The core is pure, deterministic, and offline-testable. ZeroMQ transport and a
FastAPI layer are deferred optional adapters (see ``spec.md``).
"""

from .context import Context, Verifier
from .node import AgentNode, Node, Response
from .shell import Shell

__all__ = [
    "Context",
    "Verifier",
    "AgentNode",
    "Node",
    "Response",
    "Shell",
]