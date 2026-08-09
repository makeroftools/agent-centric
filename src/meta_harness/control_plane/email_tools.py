"""Allowlisted, Manager-mediated email tools (Volley 024).

This module exposes the read-only email operations as mediated tools
(``email_list``, ``email_fetch``) bound to a configured ``EmailGateway``. Every
tool is deterministic with respect to the gateway, enforces an explicit limit
(so a list is always bounded), enforces an optional folder allowlist, and maps
any ``EmailGatewayError`` to a fail-closed ``ToolExecutionError`` so a failed or
disallowed operation is an explicit, audited failure — never a verified success.

Email is sensitive. Credentials never enter a tool argument or a task payload;
they are held by the gateway and redacted from any error it raises. The tools
carry only message metadata (id, folder, subject, from, date) and, for fetch, the
body text, exactly as the gateway returns them. Read-only only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts.email import EmailList, EmailMessage
from ..providers.email import EmailGateway, EmailGatewayError
from .tools import ToolExecutionError


class EmailTools:
    """A bundle of read-only email tools bound to a gateway."""

    def __init__(
        self,
        gateway: EmailGateway,
        *,
        default_folders: tuple[str, ...] = (),
        max_list_limit: int = 50,
    ) -> None:
        if max_list_limit <= 0:
            raise ValueError("max_list_limit must be positive.")
        self._gateway = gateway
        self._folders = default_folders
        self._max_list_limit = max_list_limit

    @property
    def default_folders(self) -> tuple[str, ...]:
        return self._folders

    @property
    def max_list_limit(self) -> int:
        return self._max_list_limit

    def _parse_list(self, args: dict[str, Any]) -> tuple[str, int]:
        folder = args.get("folder")
        if not isinstance(folder, str) or not folder:
            raise ToolExecutionError("email_list requires a non-empty 'folder'.")
        if self._folders and folder not in self._folders:
            raise ToolExecutionError(f"Folder {folder!r} is not allowed.")
        limit = args.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ToolExecutionError("email_list requires a positive integer 'limit'.")
        limit = min(limit, self._max_list_limit)
        return folder, limit

    def email_list(self, **args: Any) -> dict[str, Any]:
        folder, limit = self._parse_list(args)
        try:
            result: EmailList = self._gateway.list_messages(folder, limit)
        except EmailGatewayError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return result.as_mapping()

    def email_fetch(self, **args: Any) -> dict[str, Any]:
        folder = args.get("folder")
        message_id = args.get("message_id")
        if not isinstance(folder, str) or not folder:
            raise ToolExecutionError("email_fetch requires a non-empty 'folder'.")
        if not isinstance(message_id, str) or not message_id:
            raise ToolExecutionError("email_fetch requires a non-empty 'message_id'.")
        if self._folders and folder not in self._folders:
            raise ToolExecutionError(f"Folder {folder!r} is not allowed.")
        try:
            message: EmailMessage = self._gateway.fetch_message(folder, message_id)
        except EmailGatewayError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return message.as_mapping()


def email_tool_impls(email_tools: EmailTools) -> dict[str, Callable[..., Any]]:
    """Return the email tool implementations bound to ``email_tools``.

    Each is a callable ``(**args) -> mapping`` suitable for registration in the
    ``ToolRegistry``, so the tools are grantable, policy-bound, envelope-bound,
    recorded, and verified exactly like every other tool.
    """
    return {
        "email_list": email_tools.email_list,
        "email_fetch": email_tools.email_fetch,
    }