"""Model providers: a deterministic stub and an optional real adapter.

This module holds concrete ``ModelProvider`` implementations. The stub provider
is deterministic and is the only provider the test suite uses, so trajectories
replay identically and no network access or API key is ever required. The real
adapter is optional, disabled by default, and never required to run the tests.
"""

from __future__ import annotations

from collections.abc import Callable

from ..contracts.model import (
    ModelProviderError,
    ModelProviderVersion,
    ModelResponse,
)


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


class OptionalRealModelProvider:
    """An optional real-provider adapter behind the same interface.

    This is a thin, swappable adapter that is **disabled by default**. It is
    only instantiated when a caller explicitly supplies a backend callable and
    opts in. It is never required to run the test suite, which uses the stub
    provider exclusively. No credentials are stored here; the caller supplies
    the backend and any configuration it needs.
    """

    version: ModelProviderVersion = ModelProviderVersion.V1

    def __init__(self, backend: Callable[[str], str]) -> None:
        self._backend = backend

    def __call__(self, prompt: str) -> ModelResponse:
        # The backend is expected to return the generated text (or raise
        # ModelProviderError). Token estimation is delegated to the backend if
        # it provides one; otherwise it is omitted.
        text = self._backend(prompt)
        if not isinstance(text, str):
            raise ModelProviderError("Real provider backend must return a string.")
        return ModelResponse(text=text)