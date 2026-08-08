"""Adversarial and helper agent components for control-plane tests."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

from meta_harness.agents.interface import (
    AgentResult,
    AgentStep,
    Cancelled,
    ToolContext,
    ToolRequest,
    ToolResult,
)
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion


class CooperativeCancellingAgent:
    """Agent that observes a cooperative ``Cancelled`` signal and exits cleanly."""

    def __init__(self) -> None:
        self.saw_cancel = False

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, ToolResult | None | Cancelled, AgentResult]:
        for i in range(step_budget):
            sent: Any = yield AgentStep(
                description=f"cooperative step {i}", detail={"n": i}
            )
            if isinstance(sent, Cancelled):
                self.saw_cancel = True
                yield AgentStep(description="observed cancellation; exiting cleanly")
                return AgentResult(output="should-not-verify")
        return AgentResult(output="finished")


class SlowCooperativeCancellingAgent:
    """Slow cooperative agent that sleeps so the timeout fires before step limit."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, ToolResult | None | Cancelled, AgentResult]:
        for i in range(step_budget):
            time.sleep(0.01)
            sent: Any = yield AgentStep(
                description=f"slow cooperative step {i}", detail={"n": i}
            )
            if isinstance(sent, Cancelled):
                yield AgentStep(description="observed cancellation; exiting cleanly")
                return AgentResult(output="should-not-verify")
        return AgentResult(output="finished")


class IgnoringCancellationAgent:
    """Agent that ignores the cooperative cancellation signal and keeps going."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, ToolResult | None | Cancelled, AgentResult]:
        while True:
            time.sleep(0.01)
            yield AgentStep(description="ignoring cancellation; looping forever")


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


def create_cooperative_cancel() -> CooperativeCancellingAgent:
    return CooperativeCancellingAgent()


def create_slow_cooperative_cancel() -> SlowCooperativeCancellingAgent:
    return SlowCooperativeCancellingAgent()


def create_ignoring_cancel() -> IgnoringCancellationAgent:
    return IgnoringCancellationAgent()


def create_wrong() -> WrongOutputAgent:
    return WrongOutputAgent()


def create_sleepy() -> SleepyAgent:
    return SleepyAgent()


def create_slow_step() -> SlowStepAgent:
    return SlowStepAgent()


def create_unguarded_tool() -> UnguardedToolAgent:
    return UnguardedToolAgent()


COOPERATIVE_CANCEL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="cooperative_cancel",
    entry_point="tests.fake_agent:create_cooperative_cancel",
    description="Agent that observes a cooperative cancellation signal and exits.",
)

SLOW_COOPERATIVE_CANCEL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="slow_cooperative_cancel",
    entry_point="tests.fake_agent:create_slow_cooperative_cancel",
    description="Slow agent that observes a cooperative cancellation signal and exits.",
)

IGNORING_CANCEL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="ignoring_cancel",
    entry_point="tests.fake_agent:create_ignoring_cancel",
    description="Agent that ignores cooperative cancellation and loops forever.",
)

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