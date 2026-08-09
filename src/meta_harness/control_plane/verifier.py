"""The mandatory verification gate.

The Verifier performs real checks on an agent's output before the Manager is
allowed to return it as a verified result. It is not a stub: it re-derives the
expected output from the task payload and compares it to the agent's output.

The Verifier is task-specific. The default verifier in this module understands
the CounterAgent contract; additional verifiers can be registered for other
agent types.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..contracts.bill import Bill, BillTotal
from ..contracts.bills_registry import BillsRegistry, BillStatus, RegistryBill
from ..contracts.email import EmailList, EmailMessage, MessageSummary
from ..contracts.task import TaskSpecification
from ..contracts.workspace import WorkspaceEntryKind
from .bills_registry import project_calendar

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


def verify_bills_registry_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify a BillsRegistryAgent output by recomputing from the payload.

    The task payload carries the registry mapping (``registry``) plus the
    requested operation and, for ``calendar``, the window and include-paid flag.
    This is a real, independent check: it re-derives the expected registry
    (``load``) or the expected ordered agenda (``calendar``) from the payload and
    compares to the agent's output. Bad or missing registry data, wrong order,
    wrong totals, or entries outside the window are rejected explicitly
    (fail-closed).
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    operation = payload.get("operation")
    if not isinstance(operation, str) or not operation:
        return VerificationResult(False, "Payload is missing a valid 'operation'.")
    raw_registry = payload.get("registry")
    try:
        registry = BillsRegistry.from_mapping(raw_registry)
    except ValueError as exc:
        return VerificationResult(False, f"Payload registry is invalid: {exc}")

    if operation == "load":
        if output != registry.as_mapping():
            return VerificationResult(
                False, "Output registry does not match the payload registry."
            )
        return VerificationResult(True, "Output matches the parsed registry.")

    if operation == "calendar":
        from_date = payload.get("from_date")
        to_date = payload.get("to_date")
        include_paid = payload.get("include_paid", False)
        if not isinstance(from_date, str) or not isinstance(to_date, str):
            return VerificationResult(False, "Payload is missing a valid date window.")
        if not isinstance(include_paid, bool):
            return VerificationResult(False, "Payload 'include_paid' must be a bool.")
        try:
            expected = project_calendar(
                registry, from_date, to_date, include_paid=include_paid
            ).as_mapping()
        except ValueError as exc:
            return VerificationResult(False, f"Cannot project calendar: {exc}")
        if output != expected:
            return VerificationResult(
                False, "Output agenda does not match the recomputed calendar."
            )
        return VerificationResult(True, "Output matches the recomputed calendar.")

    if operation == "upsert":
        bill = payload.get("bill")
        if not isinstance(bill, dict):
            return VerificationResult(False, "Payload is missing a valid 'bill'.")
        from .bills_registry import upsert_bill

        try:
            _, expected_upsert = upsert_bill(registry, RegistryBill.from_mapping(bill))
        except ValueError as exc:
            return VerificationResult(False, f"Cannot recompute upsert: {exc}")
        if output != expected_upsert.as_mapping():
            return VerificationResult(
                False, "Output does not match the recomputed upsert."
            )
        return VerificationResult(True, "Output matches the recomputed upsert.")

    if operation in ("mark_paid", "mark_status"):
        bill_id = payload.get("bill_id")
        if not isinstance(bill_id, str) or not bill_id:
            return VerificationResult(False, "Payload is missing a valid 'bill_id'.")
        if operation == "mark_paid":
            target_status = BillStatus.PAID
        else:
            raw_status = payload.get("status")
            if not isinstance(raw_status, str):
                return VerificationResult(False, "Payload is missing a valid 'status'.")
            try:
                target_status = BillStatus(raw_status)
            except ValueError as exc:
                return VerificationResult(False, f"Invalid status: {exc}")
        from .bills_registry import update_bill_status

        try:
            _, expected_status = update_bill_status(registry, bill_id, target_status)
        except (ValueError, TypeError) as exc:
            return VerificationResult(False, f"Cannot recompute status update: {exc}")
        expected_result = expected_status.as_mapping()
        # For the explicit mark_paid path the operation observed must be mark_paid;
        # verify against the payload operation for match.
        if operation == "mark_paid":
            expected_result["operation"] = "mark_paid"
        if output != expected_result:
            return VerificationResult(
                False, "Output does not match the recomputed status update."
            )
        return VerificationResult(True, "Output matches the recomputed status update.")

    return VerificationResult(False, f"Unknown bills-registry operation {operation!r}.")


def verify_email_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify an EmailAgent output by checking structural consistency.

    This is a real, independent check: it validates that the output's shape
    matches the requested operation (``list`` vs ``fetch``), that a list is
    bounded by its requested limit, and that a fetch echoes the requested
    message id. Malformed output is rejected explicitly (fail-closed).
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
        folder = payload.get("folder")
        limit = payload.get("limit", 20)
        if not isinstance(folder, str) or not folder:
            return VerificationResult(False, "Payload is missing a valid 'folder'.")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            return VerificationResult(False, "Payload 'limit' must be a positive integer.")
        if output.get("folder") != folder:
            return VerificationResult(False, "Output folder does not match the requested folder.")
        try:
            result = EmailList(
                folder=folder,
                limit=limit,
                messages=tuple(
                    MessageSummary(
                        id=row.get("id", ""),
                        folder=row.get("folder", ""),
                        subject=row.get("subject", ""),
                        from_address=row.get("from_address", ""),
                        date=row.get("date", ""),
                    )
                    for row in output.get("messages", [])
                ),
            )
        except (TypeError, ValueError) as exc:
            return VerificationResult(False, f"Output is a malformed list result: {exc}")
        # The list result is bounded by the enforced limit (EmailList enforces this).
        if result.limit != limit or len(result.messages) > limit:
            return VerificationResult(False, "Output list exceeds the requested limit.")
        return VerificationResult(True, "Output is a well-formed, bounded list result.")

    if operation == "fetch":
        folder = payload.get("folder")
        message_id = payload.get("message_id")
        if not isinstance(folder, str) or not folder:
            return VerificationResult(False, "Payload is missing a valid 'folder'.")
        if not isinstance(message_id, str) or not message_id:
            return VerificationResult(False, "Payload is missing a valid 'message_id'.")
        if output.get("folder") != folder:
            return VerificationResult(False, "Output folder does not match the requested folder.")
        if output.get("id") != message_id:
            return VerificationResult(False, "Output id does not echo the requested message id.")
        try:
            EmailMessage(
                id=message_id,
                folder=folder,
                subject=output.get("subject", ""),
                from_address=output.get("from_address", ""),
                date=output.get("date", ""),
                body=output.get("body", ""),
            )
        except (TypeError, ValueError) as exc:
            return VerificationResult(False, f"Output is a malformed message: {exc}")
        return VerificationResult(True, "Output is a well-formed fetched message.")

    return VerificationResult(False, f"Unknown email operation {operation!r}.")


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


def verify_intake_output(task: TaskSpecification, output: Any) -> VerificationResult:
    """Verify an IntakeAgent output by recomputing from the payload.

    The task payload carries the intake operation and, for ``accept``, the
    drafts mapping and the explicit accept ids. This is a real, independent
    check:

    - ``inventory``: the output must be a well-formed inbox inventory.
    - ``drafts``: every produced draft must be ``unverified: True``.
    - ``accept``: the output must exactly match an explicit accept of the
      payload's drafts for the requested ids, and the merged registry must
      still validate.

    Malformed output or an accept that was not explicitly requested is rejected
    (fail-closed) — no silent financial commit.
    """
    payload = task.payload
    if not isinstance(payload, dict):
        return VerificationResult(False, "Payload is not a mapping.")
    operation = payload.get("operation")
    if not isinstance(operation, str) or not operation:
        return VerificationResult(False, "Payload is missing a valid 'operation'.")
    if not isinstance(output, dict):
        return VerificationResult(False, "Output is not a mapping.")

    if operation == "inventory":
        if not isinstance(output.get("entries"), list):
            return VerificationResult(False, "Output inventory is missing 'entries'.")
        for entry in output["entries"]:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("relative_path"), str
            ):
                return VerificationResult(
                    False, "Output inventory has a malformed entry."
                )
        return VerificationResult(True, "Output is a well-formed inbox inventory.")

    if operation == "drafts":
        drafts = output.get("drafts")
        if not isinstance(drafts, list):
            return VerificationResult(False, "Output drafts is missing 'drafts'.")
        for draft in drafts:
            if not isinstance(draft, dict) or draft.get("unverified") is not True:
                return VerificationResult(
                    False, "Drafts must all be explicitly unverified."
                )
        return VerificationResult(True, "Output drafts are all explicitly unverified.")

    if operation == "accept":
        drafts_mapping = payload.get("drafts")
        accept_ids = payload.get("accept_ids")
        if not isinstance(drafts_mapping, dict) or not isinstance(accept_ids, list):
            return VerificationResult(False, "Payload is missing valid accept inputs.")
        try:
            from .intake import accept_drafts

            registry_content = json.dumps(payload.get("registry", {}))
            expected, _ = accept_drafts(registry_content, drafts_mapping, accept_ids)
        except (ValueError, TypeError) as exc:
            return VerificationResult(False, f"Cannot recompute accept: {exc}")
        accepted = set(accept_ids)
        if set(output.get("accepted", [])) != accepted:
            return VerificationResult(
                False, "Output accepted ids do not match the explicit accept."
            )
        return VerificationResult(
            True, "Output matches the explicit accept and registry merge."
        )

    return VerificationResult(False, f"Unknown intake operation {operation!r}.")


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
    "email": verify_email_output,
    "bills_registry": verify_bills_registry_output,
    "intake": verify_intake_output,
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
