"""Adversarial and helper agent components for control-plane tests."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

from meta_harness.agents.interface import (
    AgentResult,
    AgentStep,
    ToolContext,
    ToolRequest,
)
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion


class WrongOutputAgent:
    """Agent that produces a deliberately incorrect count (fails verification)."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        yield AgentStep(description="produced intentionally wrong output")
        return AgentResult(output=-1)


class SleepyAgent:
    """Agent that sleeps in a loop forever, to be bounded by the Manager."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        while True:
            time.sleep(0.01)
            yield AgentStep(description="sleeping step")


class SlowStepAgent:
    """Agent whose first step exceeds the per-step time budget."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        time.sleep(0.5)  # slow before yielding, so the step itself is slow
        yield AgentStep(description="slow step")
        return AgentResult(output=payload)


class UnguardedToolAgent:
    """Agent that blindly requests a tool, regardless of whether it was granted.

    Used to prove the *Manager* (not the agent) enforces the grant: an ungranted
    tool request is rejected by the control plane, recorded, and handed back to
    the agent as a failed ToolResult.
    """

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        sent: Any = yield ToolRequest(name="to_upper", args={"text": payload})
        yield AgentStep(
            description="received tool result",
            detail={"success": getattr(sent, "success", None)},
        )
        return AgentResult(output=payload)


def create_wrong() -> WrongOutputAgent:
    return WrongOutputAgent()


def create_sleepy() -> SleepyAgent:
    return SleepyAgent()


def create_slow_step() -> SlowStepAgent:
    return SlowStepAgent()


WRONG_AGENT_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="wrong",
    entry_point="tests.fake_agent:create_wrong",
    description="Agent that always returns wrong output.",
)

SLEEPY_AGENT_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="sleepy",
    entry_point="tests.fake_agent:create_sleepy",
    description="Agent that sleeps forever.",
)

SLOW_STEP_AGENT_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="slow_step",
    entry_point="tests.fake_agent:create_slow_step",
    description="Agent with a slow first step.",
)

UNGUARDED_TOOL_AGENT_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="unguarded_tool",
    entry_point="tests.fake_agent:create_unguarded_tool",
    description="Agent that blindly requests a tool regardless of grant.",
)


def create_unguarded_tool() -> UnguardedToolAgent:
    return UnguardedToolAgent()