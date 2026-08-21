"""The thin, intentional agent interface.

An agent is a generator: it is invoked with a task payload and a step budget,
yields zero or more ``AgentStep`` records describing its work, and finally
returns an ``AgentResult`` containing the final output. The Manager drives the
generator, enforces the step budget, and records each yielded step in the
trajectory.

The interface is deliberately minimal so that agents are easy to implement,
isolate, and verify.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..contracts.tool import ToolDescriptor


@dataclass(frozen=True)
class AgentStep:
    """A single unit of work an agent reports to the Manager.

    Attributes:
        description: Human-readable description of what this step did.
        detail: Optional structured detail about the step (opaque).
    """

    description: str
    detail: Any = None


@dataclass(frozen=True)
class AgentResult:
    """The final output of an agent.

    Attributes:
        output: The agent's final output, to be verified by the Manager.
    """

    output: Any


@dataclass(frozen=True)
class ToolRequest:
    """An agent's request to invoke a tool.

    The agent does not execute the tool itself; it yields a ``ToolRequest`` and
    the Manager validates, executes (or rejects), records, and then *sends* the
    ``ToolResult`` back into the agent's generator.

    Attributes:
        name: The name of the tool to invoke.
        args: The keyword arguments for the tool call.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """The outcome of a mediated tool call delivered back to the agent.

    Attributes:
        success: True if the tool executed successfully.
        output: The tool's output on success (opaque).
        error: A human-readable error message on failure (else None).
    """

    success: bool
    output: Any = None
    error: str | None = None


@dataclass(frozen=True)
class Cancelled:
    """A cooperative cancellation signal delivered to a running agent.

    The Manager delivers this as the value of the generator's ``yield`` when a
    stage or composition envelope is exhausted (or an explicit cancel is
    required). An agent may observe it and exit cleanly; it is purely advisory
    and cooperative. No verified success may be returned after cancellation: the
    Manager records the cancellation and fails the run regardless of what the
    agent does next.

    Attributes:
        reason: A human-readable explanation of why the Manager cancelled.
    """

    reason: str | None = None


@dataclass(frozen=True)
class ToolContext:
    """The tools explicitly granted to the agent for this task.

    An agent may call ``available(name)`` to check whether a tool is granted.
    The context exposes only names and descriptors, never executable
    implementations: the agent must yield a ``ToolRequest``, and the Manager
    performs the actual execution.
    """

    tools: tuple[ToolDescriptor, ...] = field(default_factory=tuple)

    def available(self, name: str) -> bool:
        return any(tool.name == name for tool in self.tools)

    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)


class Agent(Protocol):
    """Protocol describing a governable agent.

    An agent is a callable that returns a generator. The generator yields
    ``AgentStep`` and ``ToolRequest`` records and finally returns an
    ``AgentResult``. It may raise an exception, in which case the Manager
    records an explicit failure.

    The agent receives the task payload, the step budget, and the ``ToolContext``
    of tools granted for this task. The Manager may send a ``Cancelled`` value
    back into the generator when an envelope is exhausted; a cooperative agent
    observes it and exits cleanly.
    """

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, ToolResult | None | Cancelled, AgentResult]: ...
