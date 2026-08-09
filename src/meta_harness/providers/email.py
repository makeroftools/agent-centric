"""Read-only email gateway providers (Volley 024).

A narrow read-only email gateway interface: list messages in a folder and fetch
a message by id (headers + body text). Two implementations are provided:

- ``FakeEmailGateway`` — an in-memory, deterministic gateway used by all CI
  tests. No network, no credentials.
- ``ImapEmailGateway`` — an optional real backend behind explicit opt-in that
  speaks IMAP against a Mail-in-a-Box style mailbox. It is **disabled by
  default** and fail-closed: constructing it without credentials raises an
  explicit error, and every error message is redacted of the configured
  secrets before it can be surfaced.

Email is sensitive. Credentials come only from environment/configuration (the
caller reads env vars), never from a task payload or an agent prompt. Every
error from the real path is scrubbed of secrets. Read-only only — this module
never sends, deletes, or mutates mail.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..contracts.email import EmailList, EmailMessage, MessageSummary


class EmailGatewayError(Exception):
    """Raised when an email gateway operation fails (always fail-closed)."""


class EmailGateway(Protocol):
    """The narrow read-only email gateway interface.

    Implementations are injected into the mediated tools; the Manager executes,
    records, and verifies. Only list and fetch are exposed. No send/delete.
    """

    def list_messages(self, folder: str, limit: int) -> EmailList: ...

    def fetch_message(self, folder: str, message_id: str) -> EmailMessage: ...


# ---------------------------------------------------------------------------
# Fake gateway (deterministic, used by all CI tests).
# ---------------------------------------------------------------------------


@dataclass
class FakeEmailGateway:
    """An in-memory, deterministic read-only email gateway for tests.

    The mailbox is a mapping of ``folder -> tuple of message payloads``. Listing
    returns the first ``limit`` messages of a folder; fetching returns the
    message with the matching id in a folder, failing closed if it is unknown.
    """

    mailbox: dict[str, tuple[dict[str, str], ...]] = field(default_factory=dict)

    def list_messages(self, folder: str, limit: int) -> EmailList:
        if folder not in self.mailbox:
            raise EmailGatewayError(f"Unknown folder {folder!r}.")
        return EmailList(
            folder=folder,
            limit=limit,
            messages=tuple(
                MessageSummary(
                    id=entry["id"],
                    folder=folder,
                    subject=entry["subject"],
                    from_address=entry["from"],
                    date=entry["date"],
                )
                for entry in self.mailbox[folder][:limit]
            ),
        )

    def fetch_message(self, folder: str, message_id: str) -> EmailMessage:
        if folder not in self.mailbox:
            raise EmailGatewayError(f"Unknown folder {folder!r}.")
        for entry in self.mailbox[folder]:
            if entry["id"] == message_id:
                return EmailMessage(
                    id=entry["id"],
                    folder=folder,
                    subject=entry["subject"],
                    from_address=entry["from"],
                    date=entry["date"],
                    body=entry["body"],
                )
        raise EmailGatewayError(f"Unknown message id {message_id!r} in folder {folder!r}.")


# ---------------------------------------------------------------------------
# Optional real IMAP backend (opt-in, disabled by default, fail-closed).
# ---------------------------------------------------------------------------

# A real IMAP transport: connects with host/user/password and returns a small
# read-only client object exposing ``list_messages`` and ``fetch_message``. It
# may raise on connection/auth/list/fetch errors; the gateway maps those to an
# explicit EmailGatewayError and redacts secrets.
ImapClientFactory = Callable[..., object]

# Default bound for a single real IMAP call. The Manager's per-step and overall
# envelope timeouts still apply independently.
_DEFAULT_EMAIL_TIMEOUT = 10.0


def _no_imap_client(*_args: object, **_kwargs: object) -> object:
    """Default transport: never reaches the network.

    A real gateway built without an injected IMAP client fails closed on use
    rather than attempting a connection, so the harness can never make an
    accidental external network call.
    """
    raise EmailGatewayError(
        "No IMAP transport configured for the real email gateway. "
        "Inject an explicit imap_client to enable real use."
    )


@dataclass
class OptionalRealEmailGateway:
    """An optional real-IMAP gateway behind the same interface.

    This path is **disabled by default**: constructing it does not enable real
    use. The caller supplies an ``imap_client`` factory plus the connection
    parameters; the credentials used are also passed as ``secret_values`` so any
    error that would otherwise embed them is scrubbed to ``[REDACTED]`` before
    it can be recorded.

    No credentials are stored on this wrapper beyond what is needed for the
    connection parameters; the concrete secrets are only referenced for error
    redaction.
    """

    imap_client: ImapClientFactory
    host: str
    user: str
    password: str
    enabled: bool = False
    folder_allowlist: tuple[str, ...] = ()
    timeout_seconds: float = _DEFAULT_EMAIL_TIMEOUT
    secret_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if not self.host or not self.user or not self.password:
            raise EmailGatewayError(
                "Real email gateway requested but missing host/user/password "
                "(supply them via environment configuration)."
            )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise EmailGatewayError(
                "Optional real email gateway is not enabled (opt-in required)."
            )

    def _redact(self, text: str) -> str:
        out = text
        for secret in self.secret_values:
            if secret:
                out = out.replace(secret, "[REDACTED]")
        return out

    def _client(self) -> object:
        return self.imap_client(self.host, self.user, self.password)

    def _run(self, operation: str, **op_args: object) -> object:
        """Invoke a client operation, mapping and redacting any failure.

        The injected client may raise on connect, auth, list, or fetch; every
        error (which may embed a secret) is scrubbed before it can be recorded.
        """
        try:
            client = self._client()
            method = getattr(client, operation, None)
            if method is None:
                raise EmailGatewayError(
                    f"Injected IMAP client does not implement {operation!r}."
                )
            return method(**op_args)
        except EmailGatewayError as exc:
            raise EmailGatewayError(self._redact(str(exc))) from exc
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted error
            raise EmailGatewayError(
                self._redact(f"{operation} failed: {exc}")
            ) from exc

    def list_messages(self, folder: str, limit: int) -> EmailList:
        self._require_enabled()
        # Ensure the folder is on the allowlist (if one is configured) so the
        # agent can never list an arbitrary mailbox folder.
        if self.folder_allowlist and folder not in self.folder_allowlist:
            raise EmailGatewayError(f"Folder {folder!r} is not allowed.")
        result = self._run("list_messages", folder=folder, limit=limit)
        if not isinstance(result, EmailList):
            raise EmailGatewayError(
                "Real email gateway list_messages must return an EmailList."
            )
        return result

    def fetch_message(self, folder: str, message_id: str) -> EmailMessage:
        self._require_enabled()
        if self.folder_allowlist and folder not in self.folder_allowlist:
            raise EmailGatewayError(f"Folder {folder!r} is not allowed.")
        result = self._run("fetch_message", folder=folder, message_id=message_id)
        if not isinstance(result, EmailMessage):
            raise EmailGatewayError(
                "Real email gateway fetch_message must return an EmailMessage."
            )
        return result


def build_optional_email_gateway(
    *,
    host: str | None,
    user: str | None,
    password: str | None,
    enabled: bool = False,
    folder_allowlist: tuple[str, ...] = (),
    timeout_seconds: float = _DEFAULT_EMAIL_TIMEOUT,
    imap_client: ImapClientFactory | None = None,
) -> OptionalRealEmailGateway:
    """Build an optional real email gateway, validating configuration fail-closed.

    Missing ``host`` / ``user`` / ``password`` produces an explicit
    :class:`EmailGatewayError` with a clear message (never a silent fallback).
    The optional ``imap_client`` is the real IMAP transport; if omitted the
    resulting gateway fails closed on use and never reaches the network. The
    credentials are captured for error redaction, never logged.
    """
    if not host or not user or not password:
        raise EmailGatewayError(
            "Real email gateway requested but missing 'host', 'user', or "
            "'password' (supply them via environment configuration)."
        )
    client = imap_client or _no_imap_client
    secrets = tuple(s for s in (host, user, password) if s)
    return OptionalRealEmailGateway(
        imap_client=client,
        host=host,
        user=user,
        password=password,
        enabled=enabled,
        folder_allowlist=folder_allowlist,
        timeout_seconds=timeout_seconds,
        secret_values=secrets,
    )