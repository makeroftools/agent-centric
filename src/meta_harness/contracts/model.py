"""Model-provider contract (versioned).

A model provider is a swappable backend that turns a prompt into text. It is
the *only* place a language model is invoked, and it is reached exclusively
through the Manager-mediated ``llm_complete`` tool. The contract is deliberately
thin: prompt in, text out, plus basic metadata (a token estimate) when the
provider can supply it.

A model call is an untrusted, stochastic step. Nothing in this contract implies
trust: the Manager mediates the call, bounds it, records it, and the final
verification gate still applies to whatever the agent produces. Model output
alone is never sufficient for a verified success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ModelProviderVersion(StrEnum):
    """Version of the model-provider interface contract."""

    V1 = "model.v1"


class ModelProviderError(Exception):
    """Raised when a model provider fails to produce a response.

    This is an explicit, auditable failure: the Manager records it and the
    agent receives a failed ``ToolResult``. It never yields an unverified
    success.
    """


@dataclass(frozen=True)
class ModelResponse:
    """The outcome of a single model-provider call.

    Attributes:
        text: The model's generated text.
        estimated_tokens: An optional estimate of tokens consumed by the call.
            Providers that cannot estimate leave it as None; callers must not
            depend on it being present.
    """

    text: str
    estimated_tokens: int | None = None


class ModelProvider(Protocol):
    """The minimal, versioned model-provider interface.

    A provider is a callable that takes a prompt string and returns a
    ``ModelResponse``. It may raise ``ModelProviderError`` on failure. It must
    be deterministic when a deterministic backend (such as the stub provider)
    is used, so that trajectories replay identically.
    """

    version: ModelProviderVersion

    def __call__(self, prompt: str) -> ModelResponse: ...