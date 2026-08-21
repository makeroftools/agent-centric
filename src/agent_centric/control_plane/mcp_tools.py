"""Thin, Manager-mediated MCP tool adapter (Volley 019).

MCP is treated strictly as an external tool *transport*. Agents never talk to an
MCP server directly: they can only *request* a granted tool by name, and the
Manager (through ``ToolRegistry``) is the sole executor. The adapter maps a
single MCP server's tools into the existing ``ToolDescriptor`` model and
executes calls with a bounded timeout.

Every failure mode — server unavailable, protocol error, tool error, or timeout
— surfaces as an explicit :class:`McpToolError`, which ``ToolRegistry`` converts
into a fail-closed ``ToolExecutionError``. A tool therefore never bypasses
grant, policy, envelope accounting, trajectory recording, or the mandatory
verification gate: an MCP result alone is never a verified success.

For v1 a single local (in-process) transport is sufficient. ``LocalMcpServer``
is both that local transport and the fake MCP double used by the automated
tests, so no real network or external server is required in CI.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any, Protocol

from ..contracts.tool import ToolDescriptor, ToolVersion

# Default bound for an individual MCP tool call. The Manager's per-step and
# overall envelope timeouts still apply independently; this bounds a genuinely
# hung transport so it cannot stall the Manager.
_DEFAULT_CALL_TIMEOUT = 5.0


class McpToolError(Exception):
    """Base class for MCP adapter failures (server, protocol, tool, timeout).

    Raised across the adapter boundary and converted by ``ToolRegistry`` into an
    explicit, fail-closed ``ToolExecutionError``.
    """


class McpProtocolError(McpToolError):
    """Raised when an MCP server is unavailable or violates the protocol."""


class McpToolCallError(McpToolError):
    """Raised when the MCP server reports a tool-call error."""


class McpTimeoutError(McpToolError):
    """Raised when an MCP tool call exceeds its bounded timeout."""


class McpGateway(Protocol):
    """The transport boundary to a single MCP server.

    Concrete transports (e.g. stdio) implement this; the automated tests supply
    an in-process fake. Tools are enumerated as ``ToolDescriptor`` records so
    they drop straight into the existing ToolRegistry contract.
    """

    def list_tools(self) -> list[ToolDescriptor]: ...
    def call_tool(self, name: str, args: dict[str, Any]) -> Any: ...
    def close(self) -> None: ...


class McpToolAdapter:
    """Maps a single MCP gateway's tools into the ToolRegistry contract.

    ``list_tools`` yields ``ToolDescriptor`` records for grant and policy
    discovery. ``call_tool`` executes a named tool with a bounded timeout,
    converting transport failures into explicit :class:`McpToolError` subclasses
    (never an exception type the Manager could mistake for a verified result).
    ``close`` releases the transport.
    """

    def __init__(
        self, gateway: McpGateway, timeout_seconds: float = _DEFAULT_CALL_TIMEOUT
    ) -> None:
        self._gateway = gateway
        self._timeout = timeout_seconds

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._gateway.list_tools())

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        return _call_with_timeout(
            self._gateway.call_tool, name, args, timeout=self._timeout
        )

    def close(self) -> None:
        self._gateway.close()


def _call_with_timeout(
    call: Callable[[str, dict[str, Any]], Any],
    name: str,
    args: dict[str, Any],
    timeout: float,
) -> Any:
    """Run ``call(name, args)`` in a daemon thread with a bounded timeout.

    A genuinely hung server cannot block the Manager: if the call does not return
    within ``timeout`` it raises :class:`McpTimeoutError` (fail-closed). The
    worker is a daemon so a truly stuck transport cannot block process exit; the
    thread is discarded after the timeout.
    """
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

    def _worker() -> None:
        try:
            result = call(name, args)
        except McpToolError as exc:
            result_queue.put(("error", exc))
        except Exception as exc:  # noqa: BLE001 - surfaced as an explicit error
            result_queue.put(("error", McpToolCallError(f"Unexpected error: {exc}")))
        else:
            result_queue.put(("ok", result))

    threading.Thread(target=_worker, daemon=True).start()
    try:
        kind, value = result_queue.get(timeout=timeout)
    except queue.Empty:
        raise McpTimeoutError(
            f"MCP tool {name!r} call timed out after {timeout}s."
        ) from None
    if kind == "error":
        raise value
    return value


# A local MCP tool handler: deterministic keyword-arguments -> result.
ToolHandler = Callable[..., Any]


class LocalMcpServer:
    """An in-process MCP server double and v1 local transport.

    Holds deterministic tool handlers. ``list_tools`` returns their descriptors;
    ``call_tool`` dispatches by name, raising :class:`McpProtocolError` for an
    unavailable server or unknown tool and wrapping handler exceptions as
    :class:`McpToolCallError`. A handler may raise an :class:`McpToolError` (or
    subclass) directly to simulate a specific server/protocol failure.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._closed = False

    def register_tool(
        self, descriptor: ToolDescriptor, handler: ToolHandler
    ) -> None:
        self._descriptors[descriptor.name] = descriptor
        self._handlers[descriptor.name] = handler

    def list_tools(self) -> list[ToolDescriptor]:
        return list(self._descriptors.values())

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        if self._closed:
            raise McpProtocolError("MCP server is closed (unavailable).")
        handler = self._handlers.get(name)
        if handler is None:
            raise McpProtocolError(f"MCP server has no tool named {name!r}.")
        try:
            return handler(**args)
        except McpToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - raised as an explicit error
            raise McpToolCallError(
                f"MCP tool {name!r} raised {type(exc).__name__}: {exc}"
            ) from exc

    def close(self) -> None:
        self._closed = True


def mcp_descriptor(
    name: str,
    description: str,
    input_schema: dict[str, str] | None = None,
    output_schema: str = "",
) -> ToolDescriptor:
    """Build a ``ToolDescriptor`` for an MCP-backed tool.

    MCP tools have external, potentially non-deterministic semantics, so the
    default execution-semantics note is explicit and honest.
    """
    return ToolDescriptor(
        version=ToolVersion.V1,
        name=name,
        description=description,
        input_schema=input_schema or {},
        output_schema=output_schema,
        execution_semantics="MCP-mediated external tool (Manager-controlled, recorded)",
    )