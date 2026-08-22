"""A coordinating BillsAgent: drives the bills loop over a store child.

Topology: ``root -> bills -> store``. The ``BillsAgent`` is the parent of a
single-writer ``StoreAgent`` (the durable registry). It serves the loop as
``run`` tasks:

- ``bills_intake`` — turn an intake row into an unverified draft (fail-closed).
- ``bills_accept`` — the human-gated accept: promote a draft to a registry bill
  (the only path that writes the registry), persisted through the store child.
- ``bills_calendar`` — project a deterministic agenda from the registry.

The BillsAgent never touches the store file directly; it reads/writes *through*
its store child (single-writer mediation). Every step is a directive, recorded
in the local audit, and re-verified on the way up. Nothing auto-accepts.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, cast

from .agent import Agent
from .bills import (
    BillsError,
    accept_draft,
    draft_from_intake,
    project_calendar,
)
from .intake import draft_from_file
from .message import (
    DIRECTIVE_RUN,
    DIRECTIVE_SPAWN,
    RESPONSE_OK,
    RESPONSE_RESULT,
    Directive,
    Response,
)
from .store_agent import StoreAgent

# Run-task names this agent serves.
TASK_INTAKE = "bills_intake"
TASK_INTAKE_FILE = "bills_intake_file"
TASK_INTAKE_EMAIL = "bills_intake_email"
TASK_INTAKE_PDF = "bills_intake_pdf"
TASK_ACCEPT = "bills_accept"
TASK_CALENDAR = "bills_calendar"
TASK_SETUP = "bills_setup"


# The pure intake capabilities (ported from main), imported lazily to avoid a
# heavy import at module load in the base agent path.
def _draft_from_email(message: dict[str, Any]) -> dict[str, Any]:
    from .intake import draft_from_email

    return draft_from_email(message)


def _draft_from_pdf_text(pdf: bytes, source_path: str = "") -> dict[str, Any]:
    from .intake import draft_from_file

    return draft_from_file(pdf, source_path=source_path)


def _b64decode(text: str) -> bytes:
    """Decode a base64 string into bytes (transport-safe for the PDF intake)."""
    return base64.b64decode(text, validate=False)

# The store child's identity (spawned by this agent).
_STORE_CHILD = "store"


class BillsAgent(Agent):
    """A coordinating agent that drives the bills loop over its store child."""

    def _handle(self, directive: Directive) -> Response:
        if directive.kind == DIRECTIVE_RUN:
            task = directive.payload.get("task")
            if task == TASK_INTAKE:
                return self._op_intake(directive)
            if task in (TASK_INTAKE_FILE, TASK_INTAKE_EMAIL, TASK_INTAKE_PDF):
                return self._op_intake_source(directive, task)
            if task == TASK_ACCEPT:
                return self._op_accept(directive)
            if task == TASK_CALENDAR:
                return self._op_calendar(directive)
            if task == TASK_SETUP:
                return self._op_setup(directive)
        return super()._handle(directive)

    def _run_args(self, directive: Directive) -> dict[str, Any]:
        args = directive.payload.get("args")
        return args if isinstance(args, dict) else dict(directive.payload)

    # -- setup: provision + configure the durable store child --------------

    def _op_setup(self, directive: Directive) -> Response:
        """Provision and configure the single-writer store child.

        The parent grants the registry path and the set of bill ids the store
        may serve; this agent spawns a ``StoreAgent`` child and configures it
        with that grant (a hard key allowlist). Idempotent: a store child that
        already exists is reused, not duplicated.
        """
        args = self._run_args(directive)
        state_path = args.get("state")
        store_keys = tuple(args.get("store_keys", ()))
        if not isinstance(state_path, str) or not state_path:
            return self._error(directive, "bills_setup requires a 'state' path")
        if self._child_agents.get(_STORE_CHILD) is None:
            spawn_resp = self._spawn(
                Directive(
                    correlation_id=f"{directive.correlation_id}:spawn-store",
                    kind=DIRECTIVE_SPAWN,
                    payload={
                        "identity": _STORE_CHILD,
                        "endpoint": self._child_endpoint(_STORE_CHILD),
                        "kind": "store",
                    },
                )
            )
            if not spawn_resp.verified:
                return self._error(directive, f"store spawn failed: {spawn_resp.error}")
        configure_resp = self.configure_child(
            _STORE_CHILD,
            state=state_path,
            store_keys=tuple(store_keys),
        )
        if not configure_resp.verified:
            return self._error(directive, f"store configure failed: {configure_resp.error}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            verified=True,
            node=self.identity,
        )

    # -- intake -------------------------------------------------------------

    def _op_intake(self, directive: Directive) -> Response:
        """Turn an intake row into an unverified draft (fail-closed)."""
        args = self._run_args(directive)
        raw = args.get("draft")
        if not isinstance(raw, dict):
            return self._error(directive, "bills_intake requires a 'draft' dict")
        try:
            draft = draft_from_intake(raw)
        except BillsError as exc:
            return self._error(directive, f"bills_intake rejected: {exc}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=draft,
            verified=True,
            node=self.identity,
        )

    def _op_intake_source(self, directive: Directive, task: str) -> Response:
        """Intake a file / email / PDF source into an **unverified** draft.

        Uses the pure intake capabilities (ported from main):
        ``draft_from_file`` / ``draft_from_email`` / ``draft_from_pdf_text``.
        The produced draft is unverified and still requires the human
        ``bills_accept`` gate; a malformed or incomplete source fails closed.
        """
        args = self._run_args(directive)
        try:
            if task == TASK_INTAKE_FILE:
                source_path = args.get("source_path") or ""
                content = args.get("content")
                if not isinstance(source_path, str) or not source_path:
                    return self._error(directive, "bills_intake_file requires 'source_path'")
                if not isinstance(content, str):
                    return self._error(
                        directive, "bills_intake_file requires 'content' (text)"
                    )
                draft = draft_from_file(content, source_path=source_path)
            elif task == TASK_INTAKE_EMAIL:
                message = args.get("message")
                if not isinstance(message, dict):
                    return self._error(directive, "bills_intake_email requires a 'message' dict")
                draft = _draft_from_email(message)
            else:  # TASK_INTAKE_PDF
                source_path = args.get("source_path") or ""
                b64 = args.get("pdf_b64")
                if not isinstance(b64, str) or not b64:
                    return self._error(
                        directive, "bills_intake_pdf requires 'pdf_b64' (base64 string)"
                    )
                try:
                    pdf = _b64decode(b64)
                except (ValueError, binascii.Error):
                    return self._error(directive, "bills_intake_pdf has invalid base64")
                draft = _draft_from_pdf_text(pdf, source_path=source_path or "")
        except BillsError as exc:
            return self._error(directive, f"bills {task} rejected: {exc}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=draft,
            verified=True,
            node=self.identity,
        )

    # -- accept (human-gated, the only registry write) ----------------------

    def _op_accept(self, directive: Directive) -> Response:
        """Promote a draft to a registry bill, persisted via the store child.

        This is the only path that writes the registry. It is explicit and
        never automatic. The draft must be well-formed (came from intake).
        """
        args = self._run_args(directive)
        draft = args.get("draft")
        if not isinstance(draft, dict):
            return self._error(directive, "bills_accept requires a 'draft' dict")
        try:
            bill = accept_draft(draft)
        except BillsError as exc:
            return self._error(directive, f"bills_accept rejected: {exc}")
        # Persist through the store child (single-writer mediation).
        store_child = self._child_agents.get(_STORE_CHILD)
        if store_child is None:
            return self._error(directive, "bills agent has no store child")
        store = cast(StoreAgent, store_child)
        key = bill["id"]
        # The store child must be granted this key. We write via a run directive
        # to the store child; the store enforces its own allowlist.
        store_resp = store._op_store_set(
            Directive(
                correlation_id=f"{directive.correlation_id}:store",
                kind=DIRECTIVE_RUN,
                payload={"task": "store_set", "args": {"key": key, "value": bill}},
            )
        )
        if not store_resp.verified:
            return self._error(directive, f"registry write failed: {store_resp.error}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_OK,
            value=key,
            verified=True,
            node=self.identity,
        )

    # -- calendar projection (read-only) ------------------------------------

    def _op_calendar(self, directive: Directive) -> Response:
        """Project a deterministic agenda from the registry (via the store)."""
        args = self._run_args(directive)
        from_date = args.get("from_date")
        to_date = args.get("to_date")
        if not isinstance(from_date, str) or not isinstance(to_date, str):
            return self._error(directive, "bills_calendar requires from_date/to_date")
        store_child = self._child_agents.get(_STORE_CHILD)
        if store_child is None:
            return self._error(directive, "bills agent has no store child")
        store = cast(StoreAgent, store_child)
        # Read the whole registry through the store child.
        registry: dict[str, dict[str, Any]] = {}
        state_store = store._state_store
        if state_store is not None:
            try:
                for key in state_store.keys():  # noqa: SIM118 - StateStore is not a dict
                    value = state_store.get(key)
                    if isinstance(value, dict):
                        registry[key] = value
            except Exception as exc:  # noqa: BLE001 - fail closed, never silent
                return self._error(directive, f"registry read failed: {exc}")
        try:
            agenda = project_calendar(registry, from_date, to_date)
        except BillsError as exc:
            return self._error(directive, f"bills_calendar rejected: {exc}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=agenda,
            verified=True,
            node=self.identity,
        )