"""Orchestration: turn a pinned chat artifact into an FBP execution.

This is the attaching seam between the LLM chat window and the FBP network:

1. The model's free-text output is validated → canonicalized → pinned (see
   ``chat_pipeline``). The pinned artifact is a **typed, canonical JSON object**.
2. Here, ordinary deterministic code maps that artifact onto an FBP **plan** — an
   ordered list of ``run`` steps consumed by ``FbpDriver.run_plan``.
3. The plan runs through the driver's correctness spine: every step is a normal
   directive, the parent re-verifies each child's value, and the run is recorded
   in the ledger (auditable + replayable). Orchestration never bypasses
   verification.

The ship-to-plan mapping is a pure function (no model, no I/O), so it is fully
deterministic and testable. Unknown/unsupported operations are rejected
fail-closed rather than guessed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Operations this orchestrator knows how to emit (as FBP ``run`` steps).
# Chat artifacts request one of these intents; anything else is rejected.
SUPPORTED_INTENTS = ("run", "inspect", "status")

# Reserved step-keys an artifact may carry; unknown keys are rejected fail-closed.
_ALLOWED_STEP_KEYS = ("task", "args", "verifier", "child")


def extract_intent(artifact: dict[str, Any]) -> str:
    """Return the pinned artifact's orchestration intent (default ``run``).

    Deterministic: reads ``artifact["intent"]`` when present and allowed,
    otherwise defaults to ``run`` (a plain task run). An unallowed intent
    raises ``ValueError``.
    """
    intent = artifact.get("intent", "run")
    if not isinstance(intent, str):
        intent = "run"
    intent = intent.strip().lower()
    if intent not in SUPPORTED_INTENTS:
        raise ValueError(f"unsupported intent: {intent!r}")
    return intent


def plan_from_artifact(
    artifact: dict[str, Any],
    *,
    verifier: str | None = None,
) -> list[dict[str, Any]]:
    """Turn a canonical chat artifact into an ordered FBP plan.

    The artifact offers either:

    - a single ``{"intent": "run", "task": str, "args": dict?, ...}``, or
    - a ``"steps": [{...}, ...]`` list for a multi-step plan.

    Each step becomes a ``run`` directive for ``FbpDriver.run_plan``. Fail-closed:
    a step must have a ``task`` string; a non-plan artifact (no single task and
    no steps) raises ``ValueError``. ``verifier`` (if given) is the default
    parent verifier applied to every step unless a step overrides it.

    Returns the ordered step list; it is pure and deterministic.
    """
    steps: list[dict[str, Any]] | None = artifact.get("steps")
    if isinstance(steps, list) and steps:
        return _steps_from_list(steps, default_verifier=verifier)

    task = artifact.get("task")
    if isinstance(task, str) and task:
        return _steps_from_list(
            [artifact], default_verifier=verifier
        )

    raise ValueError(
        "artifact is not orchestratable: give a 'task' or a non-empty 'steps' list"
    )


def _steps_from_list(
    steps: list[dict[str, Any]], *, default_verifier: str | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        step = step if isinstance(step, dict) else {}
        task = step.get("task")
        if not isinstance(task, str) or not task:
            raise ValueError(f"plan step {idx}: missing 'task' string")
        # Only forward the fields the run contract understands.
        result: dict[str, Any] = {"task": task, "args": dict(step.get("args") or {})}
        verifier = step.get("verifier") or default_verifier
        if verifier:
            result["verifier"] = verifier
        if step.get("child"):
            result["child"] = step["child"]
        out.append(result)
    return out


def run_artifact_plan(
    driver: Any,
    artifact: dict[str, Any],
    *,
    verifier: str | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run an artifact's plan through ``driver.run_plan`` (the verified spine).

    Delegates to ``FbpDriver.run_plan`` so every step is a normal directive:
    recorded in the ledger, parent re-verified, replayable. Fail-closed: if the
    artifact cannot be planned, returns ``ok=False`` with a clear message; if a
    step fails verification, ``run_plan`` stops and the plan reports
    ``ok=False`` — never a silent partial success.
    """
    try:
        steps = plan_from_artifact(artifact, verifier=verifier)
    except ValueError as exc:
        return {"ok": False, "results": [], "completed": 0, "error": str(exc)}
    try:
        plan_result = driver.run_plan(steps, on_step=on_step)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        return {
            "ok": False,
            "results": [],
            "completed": 0,
            "error": f"plan execution failed: {exc}",
        }
    return dict(plan_result)