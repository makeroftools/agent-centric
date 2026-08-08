"""Concrete, deterministic tools and the Manager-controlled ToolRegistry.

Tools are pure with respect to the agent: an agent can only *request* a tool by
name; execution happens in the control plane (the Manager or its registry).
Every tool here is a deterministic, side-effect-free local function, so tool
use produces deterministic, replayable trajectories.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts.tool import ToolDescriptor, ToolVersion


class ToolExecutionError(Exception):
    """Raised when a tool call fails (bad arguments, runtime error, etc.)."""


# A tool implementation is a pure callable mapping keyword arguments to a
# result value (which must be JSON-serialisable for durable recording).
ToolImpl = Callable[..., Any]


def _require_key(args: dict[str, Any], name: str) -> Any:
    if name not in args:
        raise ToolExecutionError(f"Missing required argument {name!r}.")
    return args[name]


def _to_upper(text: str) -> str:
    return text.upper()


def _add(a: int, b: int) -> int:
    return a + b


TO_UPPER_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="to_upper",
    description="Return the input string converted to uppercase.",
    input_schema={"text": "str"},
    output_schema="str",
    execution_semantics="pure, deterministic, side-effect free",
)

ADD_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="add",
    description="Return the sum of two integers.",
    input_schema={"a": "int", "b": "int"},
    output_schema="int",
    execution_semantics="pure, deterministic, side-effect free",
)


class ToolRegistry:
    """Deterministic registry and executor of concrete tools.

    The ToolRegistry is the tightly controlled executor under the Manager. It
    holds the concrete implementations and executes tool calls on behalf of an
    agent, applying basic argument arity validation so that malformed requests
    fail explicitly.
    """

    def __init__(self) -> None:
        self._impls: dict[str, Callable[..., Any]] = {
            "to_upper": _to_upper,
            "add": _add,
        }
        self._descriptors: dict[str, ToolDescriptor] = {
            "to_upper": TO_UPPER_DESCRIPTOR,
            "add": ADD_DESCRIPTOR,
        }

    def descriptor(self, name: str) -> ToolDescriptor | None:
        """Return the descriptor for a registered tool, or None."""
        return self._descriptors.get(name)

    def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a registered tool with the given keyword arguments.

        Raises:
            ToolExecutionError: If the tool is unknown or the arguments are
                malformed.
        """
        try:
            impl = self._impls[name]
        except KeyError as exc:
            raise ToolExecutionError(f"Unknown tool {name!r}.") from exc
        return impl(**args)