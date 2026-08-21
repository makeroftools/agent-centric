"""A concrete, deterministic agent component: the string reverser.

This agent performs simple, fully verifiable work: it reverses a string payload.
It is deterministic and self-contained, and its capability (``reverse``) is
distinct from the counter agent's (``count``), demonstrating multi-agent
registration and capability-based selection.

The payload is a mapping with key ``text`` (str). The output is the reversed
string.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext


class ReverseAgent:
    """Deterministic agent that reverses a string."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("ReverseAgent payload must be a mapping.")
        text = payload.get("text")
        if not isinstance(text, str):
            raise TypeError("ReverseAgent payload['text'] must be a string.")

        yield AgentStep(description="validated payload", detail={"text_len": len(text)})

        # Reverse in chunks to demonstrate step-wise progress while remaining
        # deterministic and bounded by the step budget. Each chunk is reversed
        # and prepended so that the final result is the full reversed string.
        result = ""
        for i in range(0, len(text), 100):
            if i // 100 >= step_budget:
                yield AgentStep(
                    description="step budget reached; stopping early",
                    detail={"steps_consumed": i // 100},
                )
                break
            chunk = text[i : i + 100]
            result = chunk[::-1] + result
            yield AgentStep(
                description=f"reversed chunk starting at {i}",
                detail={"chunk_len": len(chunk)},
            )

        yield AgentStep(description="computed final reversed string", detail={"len": len(result)})
        return AgentResult(output=result)


def create_reverse_agent() -> ReverseAgent:
    """Factory matching the manifest entry-point convention."""
    return ReverseAgent()
