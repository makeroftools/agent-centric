"""A deterministic bills-registry + calendar agenda agent (Volley 025).

This agent performs a single deterministic operation on the canonical local
bills registry in the allowlisted workspace: either ``load`` (read + validate
the registry) or ``calendar`` (project an ordered agenda for a date window). It
operates entirely through the Manager's mediated ``bills_registry_read`` /
``bills_calendar`` tools (only if explicitly granted), and returns a structured,
deterministic result that the verification gate recomputes independently.

No model is involved: due dates and amounts are read from registry data and
projected deterministically — never invented.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext, ToolRequest, ToolResult


class BillsRegistryAgent:
    """Deterministic agent that reads the bills registry and projects the agenda."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("BillsRegistryAgent payload must be a mapping.")

        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation:
            raise TypeError(
                "BillsRegistryAgent payload['operation'] must be a non-empty string."
            )

        yield AgentStep(
            description="validated bills-registry payload",
            detail={"operation": operation},
        )

        if operation == "load":
            if not tools.available("bills_registry_read"):
                yield AgentStep(description="tool bills_registry_read not granted")
                return AgentResult(output=None)
            sent: Any = yield ToolRequest(name="bills_registry_read", args={})
            return (yield from self._finish_tool(sent, "bills_registry_read"))

        if operation == "calendar":
            from_date = payload.get("from_date")
            to_date = payload.get("to_date")
            if not isinstance(from_date, str) or not from_date:
                raise TypeError("BillsRegistryAgent payload['from_date'] must be a string.")
            if not isinstance(to_date, str) or not to_date:
                raise TypeError("BillsRegistryAgent payload['to_date'] must be a string.")
            include_paid = payload.get("include_paid", False)
            if not isinstance(include_paid, bool):
                raise TypeError("BillsRegistryAgent payload['include_paid'] must be a bool.")
            if not tools.available("bills_calendar"):
                yield AgentStep(description="tool bills_calendar not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="bills_calendar",
                args={
                    "from_date": from_date,
                    "to_date": to_date,
                    "include_paid": include_paid,
                },
            )
            return (yield from self._finish_tool(sent, "bills_calendar"))

        raise TypeError(f"BillsRegistryAgent does not support operation {operation!r}.")

    @staticmethod
    def _finish_tool(
        sent: Any, tool_name: str
    ) -> Generator[AgentStep, None, AgentResult]:
        """Handle the ToolResult from a bills-registry tool call.

        On success the structured mapping output is returned as the agent output
        (the verifier recomputes it). On failure the agent returns ``None``,
        which the verification gate rejects — a failed or disallowed registry
        operation never produces a verified success.
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
            detail=_safe_detail(output),
        )
        return AgentResult(output=output)


def _safe_detail(output: dict[str, Any]) -> dict[str, Any]:
    """Return a small, deterministic detail for the step (never full bodies)."""
    result: dict[str, Any] = {}
    for key in ("from_date", "to_date", "count"):
        if key in output:
            result[key] = output[key]
    if "bills" in output:
        result["bill_count"] = len(output["bills"])
    return result


def create_bills_registry_agent() -> BillsRegistryAgent:
    """Factory matching the manifest entry-point convention."""
    return BillsRegistryAgent()