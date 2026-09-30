"""A ModelAgent: an LLM as an ordinary, first-class agent in the tree.

This is the concrete realization of the "LLMs have a place — as ordinary
agents" design. A ``ModelAgent`` is a spawned child (kind ``"model"``) that
serves a ``model`` run-task. Other agents **delegate to it through the normal
directive/response protocol** when a task warrants judgment, and its output is
treated like any other child's value: re-verified by each parent's verifier,
audited, and **not treated as conclusive on its own word**.

Determinism is preserved by construction:

- By default the agent returns a **deterministic stub** response (offline,
  testable, deterministic in CI) — the platform never relies on a live,
  non-deterministic model unless a provider is explicitly wired.
- Every response carries ``sources`` (the model id) so a non-deterministic
  result is auditable with citations.
- A real provider is an opt-in hook (``ModelProvider``); enabling one does not
  relax the correctness spine — the parent still re-verifies the output.
"""

from __future__ import annotations

from typing import Any, Protocol

from .agent import Agent
from .message import (
    DIRECTIVE_RUN,
    RESPONSE_RESULT,
    Directive,
    Response,
)

# The run-task this agent serves.
TASK_MODEL = "model"

# The default model identity (deterministic stub).
_STUB_MODEL_ID = "stub-model"


class ModelProvider(Protocol):
    """An opt-in, pluggable model backend.

    A provider takes a prompt (and optional structured args) and returns a
    string. It is the *only* place a real, non-deterministic model may be
    reached; the default is a deterministic stub.

    Two shapes are accepted so the existing hardened real provider (which is a
    callable ``__call__(prompt) -> str``) works directly:

    - a ``.complete(prompt, **kwargs) -> str`` method, or
    - a plain callable ``provider(prompt, **kwargs) -> str``.
    """

    def __call__(self, prompt: str, **kwargs: Any) -> str: ...


def _stub_complete(prompt: str, **_kwargs: Any) -> str:
    """A deterministic stub: echoes a stable, offline response.

    This keeps the platform deterministic and CI-safe by default. It is not a
    real model — it is the fail-closed default that a real provider replaces.
    """
    return f"stub response to: {prompt[:80]}"


class ModelAgent(Agent):
    """An agent that serves a ``model`` run-task (an LLM as an ordinary agent).

    Attributes:
        _model_id: The model identity attached to responses as a source.
        _provider: An optional pluggable backend; defaults to the deterministic
            stub.
    """

    def __init__(self, config: Any, *, model_id: str = _STUB_MODEL_ID) -> None:
        super().__init__(config)
        self._model_id = model_id
        self._provider: ModelProvider | None = None

    def _handle(self, directive: Directive) -> Response:
        if directive.kind == DIRECTIVE_RUN:
            task = directive.payload.get("task")
            if task == TASK_MODEL:
                return self._op_model(directive)
        return super()._handle(directive)

    def set_provider(
        self, provider: ModelProvider, *, model_id: str | None = None
    ) -> None:
        """Wire an opt-in model backend (never relaxes verification).

        Accepts either a callable ``__call__(prompt, **kwargs) -> str`` (e.g.
        the hardened ``OptionalRealModelProvider``) or an object with a
        ``.complete(prompt, **kwargs) -> str`` method. The correctness spine is
        untouched: the parent still re-verifies the output on the upward path.

        ``model_id`` is optional and additive: when supplied it overrides the
        source identity attributed to responses, so an audited result names the
        real model rather than the default stub label. When omitted the current
        id is kept.
        """
        self._provider = provider
        if model_id:
            self._model_id = model_id

    @staticmethod
    def _invoke_provider(provider: Any, prompt: str, kwargs: dict[str, Any]) -> str:
        """Call a provider supporting either ``.complete`` or ``__call__``.

        Provider results may be a plain ``str`` or a ``ModelResponse``; both are
        normalised to the text so the platform's value is the answer, not an
        object repr.
        """
        complete = getattr(provider, "complete", None)
        result = (
            complete(prompt, **kwargs) if callable(complete) else provider(prompt, **kwargs)
        )
        text = getattr(result, "text", result)
        return str(text)

    def _op_model(self, directive: Directive) -> Response:
        """Serve a model completion, attaching the model id as a source.

        The output is a normal child value: it bubbles up and is re-verified by
        the parent's verifier. ``sources`` records the model id so the result is
        auditable with citations.
        """
        args = directive.payload.get("args")
        args = args if isinstance(args, dict) else {}
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._error(directive, "model requires a non-empty 'prompt'")
        provider = self._provider
        if provider is not None:
            output = self._invoke_provider(
                provider, prompt, {k: v for k, v in args.items() if k != "prompt"}
            )
        else:
            output = _stub_complete(prompt)
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=output,
            verified=True,
            node=self.identity,
            sources=[{"kind": "model", "id": self._model_id}],
        )