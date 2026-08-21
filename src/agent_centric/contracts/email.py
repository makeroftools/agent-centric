"""Email read-only contracts (versioned).

This module defines the structured contracts for the read-only email specialty
agent (Volley 024). The design is deliberately narrow and fail-closed:

- ``MessageSummary`` — the metadata returned by ``email_list``: id, folder,
  subject, sender, and date.
- ``EmailMessage`` — a fetched message: the summary fields plus the body text.
- ``EmailList`` — a bounded list result (folder, requested limit, and the
  summaries).

Email is sensitive. These contracts carry only the metadata used for structured
results. Credentials never belong in a task payload or a trajectory; they come
only from environment/configuration and are redacted from any error that may be
recorded. Read-only only — no send, delete, or mailbox-destructive operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EmailVersion(StrEnum):
    """Version of the email contract."""

    V1 = "email.v1"


def _require_nonempty_str(value: Any, name: str) -> str:
    """Coerce ``value`` to a non-empty string, rejecting bad/missing data.

    Raises:
        ValueError: If ``value`` is not a non-empty string.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}.")
    return value


@dataclass(frozen=True)
class MessageSummary:
    """Metadata for a single message returned by a list operation.

    Attributes:
        id: Stable message identifier (echoed back for a fetch).
        folder: The folder the message lives in.
        subject: The message subject (may be empty).
        from_address: The sender address. Non-empty.
        date: The message date header (may be empty).
    """

    id: str
    folder: str
    subject: str
    from_address: str
    date: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_nonempty_str(self.id, "id"))
        object.__setattr__(self, "folder", _require_nonempty_str(self.folder, "folder"))
        if not isinstance(self.from_address, str) or not self.from_address:
            raise ValueError("from_address must be a non-empty string.")
        if not isinstance(self.subject, str):
            raise ValueError("subject must be a string.")
        if not isinstance(self.date, str):
            raise ValueError("date must be a string.")

    def as_mapping(self) -> dict[str, str]:
        """Return the summary as a plain mapping (JSON-serialisable)."""
        return {
            "id": self.id,
            "folder": self.folder,
            "subject": self.subject,
            "from_address": self.from_address,
            "date": self.date,
        }


@dataclass(frozen=True)
class EmailMessage(MessageSummary):
    """A fetched message: summary fields plus the body text.

    Attributes:
        id: Stable message identifier.
        folder: The folder the message lives in.
        subject: The message subject (may be empty).
        from_address: The sender address. Non-empty.
        date: The message date header (may be empty).
        body: The message body text (may be empty).
    """

    body: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.body, str):
            raise ValueError("body must be a string.")

    def as_mapping(self) -> dict[str, str]:
        """Return the message as a plain mapping (JSON-serialisable)."""
        mapping = super().as_mapping()
        mapping["body"] = self.body
        return mapping


@dataclass(frozen=True)
class EmailList:
    """A bounded list result.

    Attributes:
        folder: The folder that was listed.
        limit: The requested (and enforced) maximum list size.
        messages: The message summaries, at most ``limit``.
    """

    folder: str
    limit: int
    messages: tuple[MessageSummary, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "folder", _require_nonempty_str(self.folder, "folder")
        )
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError("limit must be a positive integer.")
        if len(self.messages) > self.limit:
            raise ValueError("The list result exceeds its enforced limit.")

    def as_mapping(self) -> dict[str, Any]:
        """Return the list result as a plain mapping (JSON-serialisable)."""
        return {
            "folder": self.folder,
            "limit": self.limit,
            "count": len(self.messages),
            "messages": [m.as_mapping() for m in self.messages],
        }