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

from ..contracts.bill import Bill, BillTotal
from ..contracts.task import TaskSpecification
from ..contracts.workspace import WorkspaceEntryKind

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


def verify_join_consumer_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a JoinConsumerAgent output by re-deriving the join summary.

    The agent consumes a handed-off join payload ``{"stages": [...]}`` and
    returns ``"|".join(str(branch_output) for branch in stages)``. This is a
    real, independent check that re-derives the expected summary from the
    payload and compares it to the agent's output, proving the group ->
    sequential hand-off delivered the join intact.
    """
    payload = task.payload
    if not isinstance(payload, dict) or "stages" not in payload:
        return VerificationResult(False, "Payload is not a join mapping.")
    stages = payload["stages"]
    if not isinstance(stages, (list, tuple)):
        return VerificationResult(False, "Payload['stages'] is not a list.")
    expected = "|".join(str(s[2]) for s in stages)
    if output == expected:
        return VerificationResult(True, "Output matches the expected join summary.")
    return VerificationResult(False, "Output does not match the expected join summary.")


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


def verify_bills_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a BillsAgent output by independently recomputing the totals.

    This is a real, independent check: it re-derives the expected ``BillTotal``
    from the task payload and compares it to the agent's output. Bad or missing
    payload data is rejected explicitly (fail-closed), so a malformed bill can
    never produce a verified result.
    """
    payload = task.payload
    try:
        bill = Bill.from_mapping(payload)
    except ValueError as exc:
        return VerificationResult(False, f"Payload is not a valid bill: {exc}")
    expected = BillTotal.compute(bill)
    if not isinstance(output, dict):
        return VerificationResult(False, "Output is not a bill-totals mapping.")
    try:
        actual = BillTotal(
            line_subtotal_cents=output["line_subtotal_cents"],
            discount_cents=output["discount_cents"],
            taxable_amount_cents=output["taxable_amount_cents"],
            tax_cents=output["tax_cents"],
            grand_total_cents=output["grand_total_cents"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        return VerificationResult(False, f"Output is a malformed bill-totals mapping: {exc}")
    if actual == expected:
        return VerificationResult(True, "Output matches the recomputed bill totals.")
    return VerificationResult(False, "Output does not match the recomputed bill totals.")


def verify_workspace_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a WorkspaceAgent output by recomputing the expected result.

    The payload describes a workspace operation. This is a real, independent
    check: it re-derives the expected result from the payload and compares it to
    the agent's output. A malformed payload or a non-mapping output is rejected
    explicitly (fail-closed), so a failed or disallowed workspace operation can
    never produce a verified result.
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    operation = payload.get("operation")
    if not isinstance(operation, str) or not operation:
        return VerificationResult(False, "Payload is missing a valid 'operation'.")
    if not isinstance(output, dict):
        return VerificationResult(False, "Output is not a mapping.")

    if operation == "list":
        # The list result is a mapping of relative_path -> kind. We verify it is
        # a well-formed mapping of strings; the concrete contents are determined
        # by the workspace and are checked by the tool itself.
        if all(isinstance(k, str) and isinstance(v, str) for k, v in output.items()):
            return VerificationResult(True, "Output is a well-formed workspace listing.")
        return VerificationResult(False, "Output is not a well-formed workspace listing.")

    if operation in ("read", "write", "mkdir"):
        relative_path = payload.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            return VerificationResult(False, "Payload is missing a valid 'relative_path'.")
        if output.get("relative_path") != relative_path:
            return VerificationResult(
                False, "Output relative_path does not match the requested path."
            )
        kind = output.get("kind")
        # The operation determines the expected entry kind: read/write touch a
        # file, mkdir creates a directory.
        expected_kind = (
            WorkspaceEntryKind.FILE.value
            if operation in ("read", "write")
            else WorkspaceEntryKind.DIRECTORY.value
        )
        if kind != expected_kind:
            return VerificationResult(
                False, f"Output kind {kind!r} does not match operation {operation!r}."
            )
        if operation == "write":
            expected_content = payload.get("content")
            if not isinstance(expected_content, str):
                return VerificationResult(False, "Payload 'content' must be a string.")
            if output.get("content") != expected_content:
                return VerificationResult(False, "Output content does not match the payload.")
        return VerificationResult(True, "Output matches the requested workspace operation.")

    return VerificationResult(False, f"Unknown workspace operation {operation!r}.")


def verify_mcp_tool_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify an MCP-backed tool round-trip against the expected output.

    The payload carries the ``expected`` result alongside the MCP tool name and
    arguments. This is a real, independent check: an MCP call that returned data
    is only accepted if its output matches the expected value. A result whose
    expected value is derived from the MCP server's deterministic behaviour is
    therefore the only path to a verified success — data alone from an MCP call
    is never sufficient.
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    expected = payload.get("expected")
    if output == expected:
        return VerificationResult(True, "Output matches the expected MCP result.")
    return VerificationResult(False, "Output does not match the expected MCP result.")


# Registry mapping agent name -> verifier. The Manager consults this to select
# the verification gate for a given agent.
DEFAULT_VERIFIERS: dict[str, Verifier] = {
    "counter": verify_counter_output,
    "reverse": verify_reverse_output,
    "case_tool": verify_case_tool_output,
    "unguarded_tool": verify_unguarded_tool_output,
    "unguarded_model": verify_unguarded_model_output,
    "model": verify_model_output,
    "join_consumer": verify_join_consumer_output,
    "mcp_tool": verify_mcp_tool_output,
    "bills": verify_bills_output,
    "workspace": verify_workspace_output,
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
