"""A deterministic bills specialty agent (Volley 022).

This agent takes a structured bill in and produces deterministic totals out. It
is fully governed by the Manager: it validates the payload, may *request* the
``bill_total`` tool (only if explicitly granted), and returns a ``BillTotal``
that the Manager's mandatory verification gate recomputes independently.

The payload is a mapping describing a bill (see
:class:`meta_harness.contracts.bill.Bill`). The output is a
:class:`meta_harness.contracts.bill.BillTotal`. Money math is integer-only
(minor units / cents) and deterministic; no cloud model is involved, so the
result is exact and replayable.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from ..contracts.bill import Bill, BillTotal
from .interface import AgentResult, AgentStep, ToolContext, ToolRequest, ToolResult


class BillsAgent:
    """Deterministic agent that computes a bill's totals."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        # Validate the structured bill at the boundary. Bad or missing data is
        # rejected explicitly (fail-closed) before any work begins.
        bill = Bill.from_mapping(payload)
        yield AgentStep(
            description="validated bill payload",
            detail={"lines": len(bill.lines)},
        )

        # If the bill_total tool is granted, request it from the Manager. The
        # Manager executes and records it; the result is still untrusted until
        # the verification gate recomputes it.
        if tools.available("bill_total"):
            sent: Any = yield ToolRequest(
                name="bill_total", args={"bill": bill.as_mapping()}
            )
            if not isinstance(sent, ToolResult):
                raise TypeError("Manager did not deliver a ToolResult for bill_total.")
            if not sent.success:
                yield AgentStep(description=f"bill_total tool call failed: {sent.error}")
                return AgentResult(output=None)
            output = sent.output
            if not isinstance(output, dict):
                raise TypeError("bill_total tool returned a non-mapping result.")
            yield AgentStep(
                description="received bill_total tool result",
                detail={"grand_total_cents": output["grand_total_cents"]},
            )
            return AgentResult(output=output)

        # Without the tool, compute the totals locally (deterministic). Either
        # path yields the same recomputed total, which the verifier checks. The
        # output is a plain mapping so it is JSON-serialisable for the durable
        # store and the subprocess backend.
        total = BillTotal.compute(bill)
        yield AgentStep(
            description="computed bill totals",
            detail={"grand_total_cents": total.grand_total_cents},
        )
        return AgentResult(output=total.as_mapping())


def create_bills_agent() -> BillsAgent:
    """Factory matching the manifest entry-point convention."""
    return BillsAgent()