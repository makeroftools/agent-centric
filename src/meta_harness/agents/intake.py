"""A deterministic dump-intake agent: inbox inventory + draft proposals + accept (Volley 026 / 029).

This agent performs a single intake operation through the Manager's mediated
tools (only if explicitly granted):

- ``inventory`` — list the allowlisted inbox,
- ``drafts`` — produce unverified draft proposals from supported inbox files,
- ``draft_from_email`` — produce unverified bill drafts from a fetched email
  message (read-only, weak/absent parse fails closed to no draft),
- ``accept`` — explicitly accept only the provided draft ids into the bills
  registry.

No operation may mutate the registry silently — accept is the only writing
path, requires its own tool grant, and only persists the explicitly provided
draft ids. ``draft_from_email`` is read-only and never sends/deletes/mutates
mail, and its grant is separate from both ``email_fetch`` and ``intake_accept``.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext, ToolRequest, ToolResult


class IntakeAgent:
    """Deterministic agent that runs a single intake operation."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("IntakeAgent payload must be a mapping.")

        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation:
            raise TypeError("IntakeAgent payload['operation'] must be a non-empty string.")

        yield AgentStep(description="validated intake payload", detail={"operation": operation})

        if operation == "inventory":
            if not tools.available("inbox_inventory"):
                yield AgentStep(description="tool inbox_inventory not granted")
                return AgentResult(output=None)
            sent: Any = yield ToolRequest(name="inbox_inventory", args={})
            return (yield from self._finish_tool(sent, "inbox_inventory"))

        if operation == "drafts":
            if not tools.available("intake_drafts"):
                yield AgentStep(description="tool intake_drafts not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(name="intake_drafts", args={})
            return (yield from self._finish_tool(sent, "intake_drafts"))

        if operation == "draft_from_email":
            message = payload.get("message")
            if not isinstance(message, dict):
                raise TypeError("IntakeAgent payload['message'] must be a mapping.")
            if not tools.available("intake_email_draft"):
                yield AgentStep(description="tool intake_email_draft not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="intake_email_draft", args={"message": message}
            )
            return (yield from self._finish_tool(sent, "intake_email_draft"))

        if operation == "accept":
            drafts = payload.get("drafts")
            accept_ids = payload.get("accept_ids")
            if not isinstance(drafts, dict):
                raise TypeError("IntakeAgent payload['drafts'] must be a mapping.")
            if not isinstance(accept_ids, list) or not accept_ids:
                raise TypeError("IntakeAgent payload['accept_ids'] must be a non-empty list.")
            if not all(isinstance(i, str) and i for i in accept_ids):
                raise TypeError("IntakeAgent payload['accept_ids'] must contain non-empty strings.")
            if not tools.available("intake_accept"):
                yield AgentStep(description="tool intake_accept not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="intake_accept", args={"drafts": drafts, "accept_ids": accept_ids}
            )
            return (yield from self._finish_tool(sent, "intake_accept"))

        raise TypeError(f"IntakeAgent does not support operation {operation!r}.")

    @staticmethod
    def _finish_tool(sent: Any, tool_name: str) -> Generator[AgentStep, None, AgentResult]:
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
            detail=_safe_detail(output),
        )
        return AgentResult(output=output)


def _safe_detail(output: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("count", "unverified"):
        if key in output:
            result[key] = output[key]
    if "accepted" in output:
        result["accepted_count"] = len(output["accepted"])
    return result


def create_intake_agent() -> IntakeAgent:
    """Factory matching the manifest entry-point convention."""
    return IntakeAgent()