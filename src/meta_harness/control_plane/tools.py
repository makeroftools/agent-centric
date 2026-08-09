"""Concrete, deterministic tools and the Manager-controlled ToolRegistry.

Tools are pure with respect to the agent: an agent can only *request* a tool by
name; execution happens in the control plane (the Manager or its registry).
Every tool here is a deterministic, side-effect-free local function, so tool
use produces deterministic, replayable trajectories.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts.bill import Bill, BillTotal
from ..contracts.model import ModelProvider, ModelProviderError
from ..contracts.tool import ToolDescriptor, ToolVersion
from .mcp_tools import McpToolAdapter, McpToolError


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


def _bill_total(bill: dict[str, Any]) -> dict[str, int]:
    """Compute a bill's totals from a mapping, rejecting bad/missing data.

    Raises:
        ToolExecutionError: If the bill mapping is malformed or incomplete.
    """
    try:
        parsed = Bill.from_mapping(bill)
    except ValueError as exc:
        raise ToolExecutionError(f"Invalid bill: {exc}") from exc
    return BillTotal.compute(parsed).as_mapping()


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

BILL_TOTAL_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="bill_total",
    description="Compute deterministic totals for a structured bill.",
    input_schema={"bill": "mapping"},
    output_schema="mapping",
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

LIST_WORKSPACE_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="list_workspace",
    description="List the allowlisted workspace entries that exist.",
    input_schema={},
    output_schema="mapping",
    execution_semantics="allowlisted, deterministic, side-effect free",
)

READ_WORKSPACE_FILE_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="read_workspace_file",
    description="Read an allowlisted workspace file.",
    input_schema={"relative_path": "str"},
    output_schema="mapping",
    execution_semantics="allowlisted, deterministic, side-effect free",
)

WRITE_WORKSPACE_FILE_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="write_workspace_file",
    description="Write an allowlisted workspace file.",
    input_schema={"relative_path": "str", "content": "str"},
    output_schema="mapping",
    execution_semantics="allowlisted, bounded side effect",
)

CREATE_WORKSPACE_DIR_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="create_workspace_dir",
    description="Create an allowlisted workspace directory.",
    input_schema={"relative_path": "str"},
    output_schema="mapping",
    execution_semantics="allowlisted, bounded side effect",
)

EMAIL_LIST_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="email_list",
    description="List the most recent messages in a folder (bounded).",
    input_schema={"folder": "str", "limit": "int"},
    output_schema="mapping",
    execution_semantics="read-only, gateway-mediated, bounded",
)

EMAIL_FETCH_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="email_fetch",
    description="Fetch a message's headers and body by id.",
    input_schema={"folder": "str", "message_id": "str"},
    output_schema="mapping",
    execution_semantics="read-only, gateway-mediated",
)

BILLS_REGISTRY_READ_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="bills_registry_read",
    description="Read and validate the allowlisted bills registry.",
    input_schema={},
    output_schema="mapping",
    execution_semantics="allowlisted, deterministic, side-effect free",
)

BILLS_CALENDAR_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="bills_calendar",
    description="Project a deterministic ordered agenda for a date window.",
    input_schema={"from_date": "str", "to_date": "str", "include_paid": "bool"},
    output_schema="mapping",
    execution_semantics="allowlisted, deterministic, side-effect free",
)

INBOX_INVENTORY_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="inbox_inventory",
    description="List the allowlisted inbox entries.",
    input_schema={},
    output_schema="mapping",
    execution_semantics="allowlisted, deterministic, side-effect free",
)

INTAKE_DRAFTS_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="intake_drafts",
    description="Produce unverified draft bill proposals from inbox files.",
    input_schema={},
    output_schema="mapping",
    execution_semantics="allowlisted, deterministic, production of unverified drafts",
)

INTAKE_ACCEPT_DESCRIPTOR = ToolDescriptor(
    version=ToolVersion.V1,
    name="intake_accept",
    description="Explicitly accept only the given draft ids into the registry.",
    input_schema={"drafts": "mapping", "accept_ids": "list"},
    output_schema="mapping",
    execution_semantics="explicit write, least-privilege, human-in-the-loop",
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
            "bill_total": _bill_total,
        }
        self._descriptors: dict[str, ToolDescriptor] = {
            "to_upper": TO_UPPER_DESCRIPTOR,
            "add": ADD_DESCRIPTOR,
            "bill_total": BILL_TOTAL_DESCRIPTOR,
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

    def register_impl(self, descriptor: ToolDescriptor, impl: Callable[..., Any]) -> None:
        """Register a concrete tool implementation and its descriptor.

        This is the explicit wiring point used by adapters (e.g. the workspace
        tools). Once registered, the tool is subject to the same grant, policy,
        envelope, recording, and verification paths as every other tool.
        """
        self._impls[descriptor.name] = impl
        self._descriptors[descriptor.name] = descriptor

    def register_mcp(self, adapter: McpToolAdapter) -> tuple[str, ...]:
        """Enumerate and register an MCP adapter's tools (explicit, opt-in).

        MCP tools are added to the registry exactly like local tools, so they
        are subject to the same grant, policy, envelope, recording, and
        verification paths. Registration is explicit: the adapter must be
        supplied by the caller (e.g. the CLI or an operator); no MCP tool is
        available unless it is registered here.

        Returns the names of the tools that were registered.
        """
        registered: list[str] = []
        for descriptor in adapter.list_tools():
            self._impls[descriptor.name] = self._make_mcp_impl(adapter, descriptor.name)
            self._descriptors[descriptor.name] = descriptor
            registered.append(descriptor.name)
        return tuple(registered)

    def _make_mcp_impl(
        self, adapter: McpToolAdapter, name: str
    ) -> Callable[..., Any]:
        """Bind an MCP tool name to an executable impl that fails closed.

        ``adapter.call_tool`` returns a structured result or raises an explicit
        ``McpToolError``. This wrapper converts the latter into a
        ``ToolExecutionError`` so the Manager records an audited, fail-closed
        tool failure and never a verified success.
        """

        def _impl(**args: Any) -> Any:
            try:
                return adapter.call_tool(name, args)
            except McpToolError as exc:
                raise ToolExecutionError(f"MCP tool {name!r} failed: {exc}") from exc

        return _impl

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