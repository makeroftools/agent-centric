"""A deterministic bills-registry + calendar agenda agent (Volley 025 / 028).

This agent performs a single deterministic operation on the canonical local
bills registry in the allowlisted workspace: ``load`` (read + validate the
registry), ``calendar`` (project an ordered agenda for a date window), or a
governed maintenance operation ``upsert`` / ``mark_paid`` / ``mark_status``
(explicit registry mutations). It operates entirely through the Manager's
mediated tools (only if explicitly granted), and returns a structured,
deterministic result that the verification gate recomputes independently.

Registry mutations are explicit, mediated, and verified: an upsert or status
update writes only through the allowlisted ``bills/registry.json`` path with a
validated merged registry, and never implicitly accepts intake drafts. Calendar
stays correct when projected after maintenance (the verifier recomputes the
merge, so a disallowed or failed mutation is never a verified success).

No model is involved: due dates and amounts are read from registry data and
projected/mutated deterministically — never invented.
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

        if operation == "upsert":
            bill = payload.get("bill")
            if not isinstance(bill, dict):
                raise TypeError("BillsRegistryAgent payload['bill'] must be a mapping.")
            if not tools.available("bills_registry_upsert"):
                yield AgentStep(description="tool bills_registry_upsert not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="bills_registry_upsert", args={"bill": bill}
            )
            return (yield from self._finish_tool(sent, "bills_registry_upsert"))

        if operation in ("mark_paid", "mark_status"):
            bill_id = payload.get("bill_id")
            if not isinstance(bill_id, str) or not bill_id:
                raise TypeError(
                    "BillsRegistryAgent payload['bill_id'] must be a non-empty string."
                )
            tool = (
                "bills_registry_mark_paid"
                if operation == "mark_paid"
                else "bills_registry_mark_status"
            )
            if not tools.available(tool):
                yield AgentStep(description=f"tool {tool} not granted")
                return AgentResult(output=None)
            args: dict[str, Any] = {"bill_id": bill_id}
            if operation == "mark_status":
                status = payload.get("status")
                if not isinstance(status, str) or not status:
                    raise TypeError(
                        "BillsRegistryAgent payload['status'] must be a non-empty string."
                    )
                args["status"] = status
            sent = yield ToolRequest(name=tool, args=args)
            return (yield from self._finish_tool(sent, tool))

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
    for key in ("operation", "bill_id", "created"):
        if key in output:
            result[key] = output[key]
    return result


def create_bills_registry_agent() -> BillsRegistryAgent:
    """Factory matching the manifest entry-point convention."""
    return BillsRegistryAgent()