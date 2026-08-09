"""Model providers: a deterministic stub and an optional, hardened real adapter.

The stub provider is deterministic and is the only provider the test suite
uses, so trajectories replay identically and no network access or API key is
ever required. The real provider path is optional, **disabled by default**, and
fail-closed: it requires explicit opt-in plus endpoint/credential configuration,
bounds calls with a timeout, maps HTTP/API errors to an explicit
``ModelProviderError``, and redacts secrets from anything it raises. It is
never invoked by the automated tests, which use fakes/doubles only.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from ..contracts.model import (
    ModelProviderError,
    ModelProviderVersion,
    ModelResponse,
)
from .email import (
    EmailGatewayError,
    FakeEmailGateway,
    OptionalRealEmailGateway,
    build_optional_email_gateway,
)

# Default bound for a single real-provider call. The Manager's per-step and
# overall envelope timeouts still apply independently; this bounds a genuinely
# hung or slow remote call so it cannot stall the Manager.
_DEFAULT_REAL_TIMEOUT = 10.0


def redact_secrets(text: str, secrets: tuple[str, ...]) -> str:
    """Replace concrete secret values in ``text`` with ``[REDACTED]``.

    Used so credentials or API keys can never leak into logs or raised error
    messages from the real provider path. ``secrets`` are the concrete secret
    values to scrub (e.g. an ``api_key`` or ``auth_header``).
    """
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return out


def _redact_exception(exc: Exception, secrets: tuple[str, ...]) -> str:
    return redact_secrets(str(exc), secrets)


def _call_with_timeout(
    call: Callable[[str], str], prompt: str, timeout: float
) -> str:
    """Run ``call(prompt)`` in a daemon thread with a bounded timeout.

    A genuinely hung remote provider cannot block the Manager: if it does not
    return within ``timeout`` this raises :class:`ModelProviderError` (fail
    closed). The worker is a daemon so a truly stuck transport cannot block
    process exit.
    """
    result_queue: queue.Queue[tuple[str, str | Exception]] = queue.Queue()

    def _worker() -> None:
        try:
            text = call(prompt)
        except Exception as exc:  # noqa: BLE001 - mapped below by the caller
            result_queue.put(("error", exc))
        else:
            result_queue.put(("ok", text))

    threading.Thread(target=_worker, daemon=True).start()
    try:
        kind, value = result_queue.get(timeout=timeout)
    except queue.Empty:
        raise ModelProviderError(
            f"Real model provider call timed out after {timeout}s."
        ) from None
    if kind == "error":
        # value is an Exception when kind == "error".
        raise value  # type: ignore[misc]
    # kind == "ok": value is the string text the backend produced.
    text = value
    if not isinstance(text, str):
        raise ModelProviderError("Real provider backend must return a string.")
    return text


class StubModelProvider:
    """A deterministic model provider for tests and local use.

    It returns a fixed response for every prompt, or a scripted per-prompt
    response when ``responses`` is supplied. It never touches the network and
    is fully deterministic, so a task that uses it produces a replayable
    trajectory.

    Attributes:
        version: The model-provider interface version.
        responses: Optional mapping of prompt -> response text. When a prompt
            is not present, ``default_text`` is returned.
        default_text: The response returned for any prompt not in ``responses``.
        calls: The ordered list of prompts received (for assertions).
    """

    version: ModelProviderVersion = ModelProviderVersion.V1

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_text: str = "stub response",
    ) -> None:
        self._responses = dict(responses or {})
        self._default_text = default_text
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> ModelResponse:
        self.calls.append(prompt)
        text = self._responses.get(prompt, self._default_text)
        return ModelResponse(text=text, estimated_tokens=len(prompt.split()))


class FailingStubModelProvider:
    """A deterministic provider that always raises ``ModelProviderError``.

    Used to prove that a provider failure is explicit, audited, and never
    produces an unverified success.
    """

    version: ModelProviderVersion = ModelProviderVersion.V1

    def __call__(self, prompt: str) -> ModelResponse:
        raise ModelProviderError("simulated provider failure")


# A real HTTP transport: endpoint, headers, payload -> response text. It may
# raise on HTTP/API/transport errors; the provider maps those to an explicit
# ModelProviderError. Never invoked by the test suite (fakes are used instead).
HttpClient = Callable[[str, dict[str, str], str], str]


def _no_http_client(endpoint: str, headers: dict[str, str], payload: str) -> str:
    """Default transport: never reaches the network.

    A real provider built without an injected ``http_client`` fails closed on
    call rather than attempting a network request, so the harness can never
    make an accidental external call.
    """
    raise ModelProviderError(
        "No HTTP transport configured for the real model provider. "
        "Inject an explicit http_client to enable real calls."
    )


class OptionalRealModelProvider:
    """An optional real-provider adapter behind the same interface.

    This path is **disabled by default**: constructing it does not enable real
    calls, and invoking a provider built without ``enabled=True`` raises an
    explicit :class:`ModelProviderError`. When enabled it bounds each call with
    a timeout, maps HTTP/API/transport errors to ``ModelProviderError``, and
    redacts known secrets from anything it raises.

    The caller supplies the ``backend`` (a callable ``prompt -> text``) which is
    normally produced by :func:`build_real_model_provider` with an explicit
    ``http_client``. No credentials are stored directly on this wrapper; the
    ``secret_values`` it holds are only used for error redaction.
    """

    version: ModelProviderVersion = ModelProviderVersion.V1

    def __init__(
        self,
        backend: Callable[[str], str],
        *,
        enabled: bool = False,
        timeout_seconds: float = _DEFAULT_REAL_TIMEOUT,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._backend = backend
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._secrets = secret_values

    def __call__(self, prompt: str) -> ModelResponse:
        if not self._enabled:
            raise ModelProviderError(
                "Optional real model provider is not enabled (opt-in required)."
            )
        try:
            text = _call_with_timeout(self._backend, prompt, self._timeout)
        except ModelProviderError as exc:
            # The provider's own error may embed a secret (e.g. an endpoint or
            # header echo); redact it before it can surface.
            raise ModelProviderError(_redact_exception(exc, self._secrets)) from exc
        except Exception as exc:  # noqa: BLE001 - mapped to an explicit failure
            raise ModelProviderError(
                f"Real model provider failed: {_redact_exception(exc, self._secrets)}"
            ) from exc
        if not isinstance(text, str):
            raise ModelProviderError("Real provider backend must return a string.")
        return ModelResponse(text=text)


def build_real_model_provider(
    *,
    endpoint: str | None,
    api_key: str | None = None,
    auth_header: str | None = None,
    timeout_seconds: float = _DEFAULT_REAL_TIMEOUT,
    http_client: HttpClient | None = None,
) -> OptionalRealModelProvider:
    """Build an enabled real provider, validating configuration fail-closed.

    Requires an ``endpoint`` and at least one of ``api_key`` / ``auth_header``;
    otherwise it raises an explicit :class:`ModelProviderError` with a clear
    message. The optional ``http_client`` is the real transport; if omitted the
    resulting provider fails closed on call (it never accidentally reaches the
    network). Secrets are captured for error redaction, never logged.
    """
    if not endpoint:
        raise ModelProviderError(
            "Real model provider requested but missing 'endpoint'."
        )
    if not api_key and not auth_header:
        raise ModelProviderError(
            "Real model provider requested but missing credentials "
            "(supply 'api_key' or 'auth_header')."
        )
    client = http_client or _no_http_client
    secrets = tuple(s for s in (api_key or "", auth_header or "") if s)
    effective_header = auth_header or f"Bearer {api_key}"

    def backend(prompt: str) -> str:
        headers = {
            "Authorization": effective_header,
            "Content-Type": "application/json",
        }
        return client(endpoint, headers, prompt)

    return OptionalRealModelProvider(
        backend,
        enabled=True,
        timeout_seconds=timeout_seconds,
        secret_values=secrets,
    )


__all__ = [
    "StubModelProvider",
    "FailingStubModelProvider",
    "OptionalRealModelProvider",
    "build_real_model_provider",
    "redact_secrets",
    "EmailGatewayError",
    "FakeEmailGateway",
    "OptionalRealEmailGateway",
    "build_optional_email_gateway",
]