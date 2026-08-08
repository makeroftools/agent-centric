"""Agent implementations and the thin agent interface.

The interface in this package is the single contract every governable agent
must implement. It is intentionally minimal: an agent is a callable that takes
a task payload, a step budget, and a ``ToolContext`` of granted tools, and
yields steps and tool requests until it produces a final output.
"""

from .interface import (
    Agent,
    AgentResult,
    AgentStep,
    ToolContext,
    ToolRequest,
    ToolResult,
)

__all__ = [
    "Agent",
    "AgentStep",
    "AgentResult",
    "ToolContext",
    "ToolRequest",
    "ToolResult",
]
