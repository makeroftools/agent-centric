"""A deterministic, read-only email specialty agent (Volley 024).

This agent performs a single read-only email operation (``list`` or ``fetch``)
through the Manager's mediated ``email_list`` / ``email_fetch`` tools (only if
explicitly granted), and returns a deterministic, structured result that the
verification gate checks. It is deliberately deterministic and requires no model.

Email is sensitive: the payload carries only the operation and (for fetch) a
message id — never credentials. All secrets live in the gateway configuration,
and any gateway error is already redacted before it can be recorded. This agent
never sends, deletes, or mutates mail.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext, ToolRequest, ToolResult


class EmailAgent:
    """Deterministic agent that performs a read-only email operation."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("EmailAgent payload must be a mapping.")

        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation:
            raise TypeError("EmailAgent payload['operation'] must be a non-empty string.")

        yield AgentStep(description="validated email payload", detail={"operation": operation})

        if operation == "list":
            folder = payload.get("folder")
            if not isinstance(folder, str) or not folder:
                raise TypeError("EmailAgent payload['folder'] must be a non-empty string.")
            limit = payload.get("limit", 20)
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise TypeError("EmailAgent payload['limit'] must be a positive integer.")
            if not tools.available("email_list"):
                yield AgentStep(description="tool email_list not granted")
                return AgentResult(output=None)
            sent: Any = yield ToolRequest(
                name="email_list", args={"folder": folder, "limit": limit}
            )
            return (yield from self._finish_tool(sent, "email_list"))

        if operation == "fetch":
            folder = payload.get("folder")
            message_id = payload.get("message_id")
            if not isinstance(folder, str) or not folder:
                raise TypeError("EmailAgent payload['folder'] must be a non-empty string.")
            if not isinstance(message_id, str) or not message_id:
                raise TypeError("EmailAgent payload['message_id'] must be a non-empty string.")
            if not tools.available("email_fetch"):
                yield AgentStep(description="tool email_fetch not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="email_fetch", args={"folder": folder, "message_id": message_id}
            )
            return (yield from self._finish_tool(sent, "email_fetch"))

        raise TypeError(f"EmailAgent does not support operation {operation!r}.")

    @staticmethod
    def _finish_tool(sent: Any, tool_name: str) -> Generator[AgentStep, None, AgentResult]:
        """Handle the ToolResult from an email tool call.

        On success the tool's mapping output (which is already structured and
        secret-free) is returned as the agent output; the verifier checks it. On
        failure the agent returns ``None``, which the verification gate rejects —
        a failed or disallowed email operation never produces a verified success.
        """
        if not isinstance(sent, ToolResult):
            raise TypeError(f"Manager did not deliver a ToolResult for {tool_name}.")
        if not sent.success:
            yield AgentStep(description=f"{tool_name} tool call failed: {sent.error}")
            return AgentResult(output=None)
        output = sent.output
        if not isinstance(output, dict):
            raise TypeError(f"{tool_name} tool returned a non-mapping result.")
        yield AgentStep(
            description=f"received {tool_name} tool result",
            detail={"folder": output.get("folder")},
        )
        return AgentResult(output=output)


def create_email_agent() -> EmailAgent:
    """Factory matching the manifest entry-point convention."""
    return EmailAgent()