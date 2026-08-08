"""A concrete, deterministic agent component: the character counter.

This agent performs simple, fully verifiable work: it counts the number of
occurrences of a target character within a string payload. It is deterministic
and self-contained, making it an ideal first governed agent for the harness.

The payload is a mapping with keys ``text`` (str) and ``target`` (str, a single
character). The output is an integer count.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext


class CounterAgent:
    """Deterministic agent that counts occurrences of a target character."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("CounterAgent payload must be a mapping.")
        text = payload.get("text")
        target = payload.get("target")
        if not isinstance(text, str):
            raise TypeError("CounterAgent payload['text'] must be a string.")
        if not isinstance(target, str) or len(target) != 1:
            raise TypeError("CounterAgent payload['target'] must be a single character.")

        yield AgentStep(description="validated payload", detail={"text_len": len(text)})

        count = 0
        for i, ch in enumerate(text):
            if i >= step_budget:
                # We cannot exceed the step budget; stop early and report what we have.
                yield AgentStep(
                    description="step budget reached; stopping early",
                    detail={"steps_consumed": i},
                )
                break
            if ch == target:
                count += 1
            if (i + 1) % 1000 == 0:
                yield AgentStep(
                    description=f"processed {i + 1} characters",
                    detail={"count_so_far": count},
                )

        yield AgentStep(description="computed final count", detail={"count": count})
        return AgentResult(output=count)


def create_counter_agent() -> CounterAgent:
    """Factory matching the manifest entry-point convention."""
    return CounterAgent()
