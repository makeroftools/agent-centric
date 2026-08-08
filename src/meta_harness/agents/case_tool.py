"""A concrete, deterministic agent component that uses a mediated tool.

This agent performs a simple, fully verifiable transformation (uppercase its
input string) by *requesting* the ``to_upper`` tool from the Manager. It can
only use the tool if the task explicitly grants it; otherwise the Manager
returns a rejected ``ToolResult`` and the agent reports the failure.

The payload is a mapping with key ``text`` (str). The output is the uppercased
string.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext, ToolRequest, ToolResult


class CaseToolAgent:
    """Deterministic agent that uppercases a string via a mediated tool."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("CaseToolAgent payload must be a mapping.")
        text = payload.get("text")
        if not isinstance(text, str):
            raise TypeError("CaseToolAgent payload['text'] must be a string.")

        yield AgentStep(description="validated payload", detail={"text_len": len(text)})

        if not tools.available("to_upper"):
            yield AgentStep(description="tool to_upper not granted")
            return AgentResult(output=text)

        # Request the Manager to execute the to_upper tool. The value of the
        # yield expression is the ToolResult sent back by the Manager.
        sent: Any = yield ToolRequest(name="to_upper", args={"text": text})
        if not isinstance(sent, ToolResult):
            raise TypeError("Manager did not deliver a ToolResult for to_upper.")
        if not sent.success:
            yield AgentStep(description=f"tool call failed: {sent.error}")
            return AgentResult(output=text)

        output = sent.output
        yield AgentStep(
            description="received to_upper tool result",
            detail={"result_is_str": isinstance(output, str)},
        )
        return AgentResult(output=output)


def create_case_tool_agent() -> CaseToolAgent:
    """Factory matching the manifest entry-point convention."""
    return CaseToolAgent()