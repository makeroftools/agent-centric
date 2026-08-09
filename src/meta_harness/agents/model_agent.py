"""A concrete model-using agent, fully governed by the Manager.

This agent answers a constrained prompt by *requesting* the ``llm_complete``
tool from the Manager. It can only call the model if the task explicitly grants
the tool; otherwise the Manager delivers a rejected ``ToolResult`` and the agent
reports the failure. The agent is deliberately minimal: it builds a single
prompt from the payload, requests completion, and returns the model's text.

The payload is a mapping with key ``prompt`` (str). The output is the model's
generated text. As with every agent, model output alone is never a verified
result: the Manager's mandatory verification gate still applies.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext, ToolRequest, ToolResult


class ModelAgent:
    """A minimal agent that uses the mediated ``llm_complete`` tool."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("ModelAgent payload must be a mapping.")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise TypeError("ModelAgent payload['prompt'] must be a non-empty string.")

        yield AgentStep(
            description="validated payload",
            detail={"prompt_len": len(prompt)},
        )

        if not tools.available("llm_complete"):
            yield AgentStep(description="tool llm_complete not granted")
            # Without the model we have no verified answer; the agent reports it
            # could not complete the request. The verification gate below will
            # reject this output, so an ungranted model never verifies.
            return AgentResult(output="UNVERIFIED")

        # Request the Manager to execute the llm_complete tool. The yield
        # expression's value is the ToolResult sent back by the Manager.
        sent: Any = yield ToolRequest(name="llm_complete", args={"prompt": prompt})
        if not isinstance(sent, ToolResult):
            raise TypeError("Manager did not deliver a ToolResult for llm_complete.")
        if not sent.success:
            yield AgentStep(description=f"model call failed: {sent.error}")
            return AgentResult(output="UNVERIFIED")

        output = sent.output
        yield AgentStep(
            description="received llm_complete tool result",
            detail={"result_is_str": isinstance(output, str)},
        )
        return AgentResult(output=output)


def create_model_agent() -> ModelAgent:
    """Factory matching the manifest entry-point convention."""
    return ModelAgent()