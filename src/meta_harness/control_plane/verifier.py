"""The mandatory verification gate.

The Verifier performs real checks on an agent's output before the Manager is
allowed to return it as a verified result. It is not a stub: it re-derives the
expected output from the task payload and compares it to the agent's output.

The Verifier is task-specific. The default verifier in this module understands
the CounterAgent contract; additional verifiers can be registered for other
agent types.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..contracts.task import TaskSpecification

# A verifier is a callable that takes the task specification and the agent's
# output, and returns a VerificationResult.
Verifier = Callable[[TaskSpecification, Any], "VerificationResult"]


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a verification check.

    Attributes:
        passed: True if the output is verified correct.
        message: Explanation of the outcome.
    """

    passed: bool
    message: str


def verify_counter_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a CounterAgent output by re-deriving the expected count.

    This is a real, independent check: it recomputes the expected count from
    the payload and compares it to the agent's output.
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    text = payload.get("text")
    target = payload.get("target")
    if not isinstance(text, str) or not isinstance(target, str) or len(target) != 1:
        return VerificationResult(False, "Payload is malformed for counter verification.")
    expected = text.count(target)
    if output == expected:
        return VerificationResult(True, f"Output {output} matches expected count {expected}.")
    return VerificationResult(
        False, f"Output {output} does not match expected count {expected}."
    )


def verify_reverse_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a ReverseAgent output by re-deriving the expected reversed string.

    This is a real, independent check: it recomputes the reversed string from
    the payload and compares it to the agent's output.
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    text = payload.get("text")
    if not isinstance(text, str):
        return VerificationResult(False, "Payload is malformed for reverse verification.")
    expected = text[::-1]
    if output == expected:
        return VerificationResult(True, "Output matches the expected reversed string.")
    return VerificationResult(False, "Output does not match the expected reversed string.")


def verify_case_tool_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a CaseToolAgent output: it must equal the uppercased input string.

    The only way the agent can produce the correct output is if its ``to_upper``
    tool call succeeded. An ungranted tool (agent returns the original text) is
    therefore rejected here, proving a tool failure cannot produce an unverified
    success.
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    text = payload.get("text")
    if not isinstance(text, str):
        return VerificationResult(False, "Payload is malformed for case_tool verification.")
    expected = text.upper()
    if output == expected:
        return VerificationResult(True, "Output matches the expected uppercased string.")
    return VerificationResult(False, "Output does not match the expected uppercased string.")


def verify_model_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a ModelAgent output against the expected prompt response.

    The payload carries the expected response (``expected``) alongside the
    prompt. This is a real, independent check that the model's output (via the
    mediated ``llm_complete`` tool) matches the expected text. An ungranted or
    failed model call (agent returns ``UNVERIFIED``) is therefore rejected here,
    proving model output alone is never a verified success.
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    expected = payload.get("expected")
    if not isinstance(expected, str):
        return VerificationResult(False, "Payload is malformed for model verification.")
    if output == expected:
        return VerificationResult(True, "Output matches the expected model response.")
    return VerificationResult(False, "Output does not match the expected model response.")


def verify_unguarded_model_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify an UnguardedModelAgent output (passthrough of the prompt).

    This agent always returns its input unchanged; the interesting behaviour we
    test is the Manager's grant enforcement for the ``llm_complete`` tool, not
    the output itself.
    """
    payload = task.payload
    if not isinstance(payload, str):
        return VerificationResult(False, "Payload must be a string.")
    if output == payload:
        return VerificationResult(True, "Output matches the passthrough input.")
    return VerificationResult(False, "Output does not match the input.")


def verify_unguarded_tool_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify an UnguardedToolAgent output (passthrough of the input text).

    This agent always returns its input unchanged; the interesting behaviour we
    test is the Manager's grant enforcement, not the output itself.
    """
    payload = task.payload
    if not isinstance(payload, str):
        return VerificationResult(False, "Payload must be a string.")
    if output == payload:
        return VerificationResult(True, "Output matches the passthrough input.")
    return VerificationResult(False, "Output does not match the input.")


# Registry mapping agent name -> verifier. The Manager consults this to select
# the verification gate for a given agent.
DEFAULT_VERIFIERS: dict[str, Verifier] = {
    "counter": verify_counter_output,
    "reverse": verify_reverse_output,
    "case_tool": verify_case_tool_output,
    "unguarded_tool": verify_unguarded_tool_output,
    "unguarded_model": verify_unguarded_model_output,
    "model": verify_model_output,
}


class VerifierRegistry:
    """Resolves the verification gate for a given agent name.

    The registry is intentionally simple and explicit. It maps agent names to
    verifier callables and refuses to verify unknown agents (fail-closed).
    """

    def __init__(self, verifiers: dict[str, Verifier] | None = None) -> None:
        self._verifiers: dict[str, Verifier] = dict(verifiers or DEFAULT_VERIFIERS)

    def verify(self, agent_name: str, task: TaskSpecification, output: Any) -> VerificationResult:
        """Verify an agent's output using the gate registered for ``agent_name``.

        The agent name is passed explicitly because a task may select its agent
        by capability, in which case ``task.agent_name`` is None.
        """
        verifier = self._verifiers.get(agent_name)
        if verifier is None:
            return VerificationResult(
                False, f"No verifier registered for agent {agent_name!r}."
            )
        return verifier(task, output)
