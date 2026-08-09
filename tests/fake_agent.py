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


class UnguardedModelAgent:
    """Agent that blindly requests the ``llm_complete`` tool.

    Used to prove the *Manager* (not the agent) enforces the grant for model
    calls: an ungranted ``llm_complete`` request is rejected by the control
    plane, recorded, and handed back to the agent as a failed ToolResult.
    """

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        sent: Any = yield ToolRequest(name="llm_complete", args={"prompt": payload})
        yield AgentStep(
            description="received model tool result",
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


def create_unguarded_model() -> UnguardedModelAgent:
    return UnguardedModelAgent()


class JoinConsumerAgent:
    """Agent that consumes a parallel-group join payload ``{"stages": [...]}``.

    It inspects the handed-off join and returns a deterministic summary string
    derived from the joined branch outputs. Used to prove the group -> sequential
    hand-off end-to-end: a later sequential stage receives the join dict as its
    input payload.
    """

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        if not isinstance(payload, dict) or "stages" not in payload:
            raise TypeError("JoinConsumerAgent payload must be a join mapping.")
        stages = payload["stages"]
        if not isinstance(stages, (list, tuple)):
            raise TypeError("JoinConsumerAgent payload['stages'] must be a list.")
        yield AgentStep(
            description="validated join payload",
            detail={"branches": len(stages)},
        )
        # Deterministic summary: concatenate the branch outputs in join order.
        parts = [str(s[2]) for s in stages]
        return AgentResult(output="|".join(parts))


class CrashAfterStepAgent:
    """Agent that yields a step, then raises inside its generator.

    Simulates a child crash mid-run: the subprocess fails, and the Manager must
    report an explicit, audited ``AGENT_ERROR`` (fail-closed), never a success.
    """

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        yield AgentStep(description="about to crash")
        raise RuntimeError("simulated agent crash")


class UnsupportedYieldAgent:
    """Misbehaving agent that yields a value the IPC codec cannot encode.

    This simulates a protocol violation on the child side. The child reports it
    back to the Manager as an error, and the Manager must fail closed with an
    explicit ``AGENT_ERROR``.
    """

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep, None, AgentResult]:
        yield {"not": "a step"}  # type: ignore[return-value]
        return AgentResult(output="ignored")


def create_join_consumer() -> JoinConsumerAgent:
    return JoinConsumerAgent()


def create_crash_after_step() -> CrashAfterStepAgent:
    return CrashAfterStepAgent()


def create_unsupported_yield() -> UnsupportedYieldAgent:
    return UnsupportedYieldAgent()


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

UNGUARDED_MODEL_AGENT_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="unguarded_model",
    entry_point="tests.fake_agent:create_unguarded_model",
    description="Agent that blindly requests the llm_complete tool regardless of grant.",
)

JOIN_CONSUMER_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="join_consumer",
    entry_point="tests.fake_agent:create_join_consumer",
    description="Agent that consumes a parallel-group join payload.",
)

CRASH_AFTER_STEP_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="crash_after_step",
    entry_point="tests.fake_agent:create_crash_after_step",
    description="Agent that crashes mid-run after yielding a step.",
)

UNSUPPORTED_YIELD_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="unsupported_yield",
    entry_point="tests.fake_agent:create_unsupported_yield",
    description="Agent that yields an un-encodable value (protocol violation).",
)
