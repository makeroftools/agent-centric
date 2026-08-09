"""Concrete, deterministic tools and the Manager-controlled ToolRegistry.

Tools are pure with respect to the agent: an agent can only *request* a tool by
name; execution happens in the control plane (the Manager or its registry).
Every tool here is a deterministic, side-effect-free local function, so tool
use produces deterministic, replayable trajectories.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts.model import ModelProvider, ModelProviderError
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

LLM_COMPLETE_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="llm_complete",
    description="Complete a prompt via a language model, mediated by the Manager.",
    input_schema={"prompt": "str"},
    output_schema="str",
    execution_semantics="mediated, bounded, recorded model call (stochastic backend)",
)


class ToolRegistry:
    """Deterministic registry and executor of concrete tools.

    The ToolRegistry is the tightly controlled executor under the Manager. It
    holds the concrete implementations and executes tool calls on behalf of an
    agent, applying basic argument arity validation so that malformed requests
    fail explicitly.
    """

    def __init__(self, model_provider: ModelProvider | None = None) -> None:
        self._model_provider = model_provider
        self._impls: dict[str, Callable[..., Any]] = {
            "to_upper": _to_upper,
            "add": _add,
        }
        self._descriptors: dict[str, ToolDescriptor] = {
            "to_upper": TO_UPPER_DESCRIPTOR,
            "add": ADD_DESCRIPTOR,
        }
        if model_provider is not None:
            self.register_llm_tool(model_provider)

    def register_llm_tool(self, provider: ModelProvider) -> None:
        """Enable the ``llm_complete`` tool with the given model provider.

        The tool is only registered (and thus only grantable) when an explicit
        ``ModelProvider`` is supplied. Without a provider the tool does not
        exist in the registry, so it cannot be granted and the agent can never
        call a model. This keeps model use opt-in and fail-closed.
        """
        self._model_provider = provider
        self._impls["llm_complete"] = self._execute_llm
        self._descriptors["llm_complete"] = LLM_COMPLETE_DESCRIPTOR

    def _execute_llm(self, prompt: str) -> str:
        provider = self._model_provider
        if provider is None:
            raise ToolExecutionError("llm_complete is not enabled (no model provider).")
        try:
            response = provider(prompt)
        except ModelProviderError as exc:
            raise ToolExecutionError(f"Model provider failed: {exc}") from exc
        return response.text

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