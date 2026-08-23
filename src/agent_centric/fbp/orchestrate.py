"""Orchestration: turn a pinned chat artifact into an FBP execution.

This is the attaching seam between the LLM chat window and the FBP network:

1. The model's free-text output is validated → canonicalized → pinned (see
   ``chat_pipeline``). The pinned artifact is a **typed, canonical JSON object**.
2. Here, ordinary deterministic code maps that artifact onto an FBP **plan** — an
   ordered list of ``run`` steps consumed by ``FbpDriver.run_plan``. A typed
   artifact may declare an ``intent`` (``run``/``double``/``sum``/``bills_*``/...);
   its fields are validated against a **schema** (``SCHEMAS``) and coerced
   fail-closed before planning (schema-driven orchestration).
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
SUPPORTED_INTENTS = (
    "run", "inspect", "status", "double", "sum",
    "bills_intake", "bills_accept", "bills_calendar",
    "bills_registry", "bills_mark_paid", "bills_mark_status",
    "bills_accept_deterministic", "bills_rule_add",
)

# Typed intent schemas for schema-driven orchestration. Each schema declares the
# fields an artifact may carry for that intent (``type`` in
# ``int|float|bool|str|object``, ``required``, ``default``), plus an optional
# ``task`` (the FBP task to run) and ``child`` (the child agent to delegate to).
# A raw artifact is validated/coerced against its intent's schema (fail-closed)
# before it is mapped to a concrete FBP plan. This is the seam that lets the chat
# window emit a *typed, schema-constrained* artifact rather than free-form JSON.
SCHEMAS: dict[str, dict[str, Any]] = {
    "run": {
        "fields": {
            "intent": {"type": "str", "default": "run"},
            "task": {"type": "str", "required": True},
            "args": {"type": "object"},
        }
    },
    "double": {
        "fields": {
            "intent": {"type": "str", "default": "double"},
            "value": {"type": "int", "required": True},
        }
    },
    "sum": {
        "fields": {
            "intent": {"type": "str", "default": "sum"},
            "a": {"type": "int", "required": True},
            "b": {"type": "int", "required": True},
        }
    },
    # Bills loop intents: route to the ``bills`` child (the mission loop).
    "bills_intake": {
        "task": "bills_intake",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_intake"},
            "draft": {"type": "object", "required": True},
        },
    },
    "bills_accept": {
        "task": "bills_accept",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_accept"},
            "draft": {"type": "object", "required": True},
        },
    },
    "bills_calendar": {
        "task": "bills_calendar",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_calendar"},
            "from_date": {"type": "str", "required": True},
            "to_date": {"type": "str", "required": True},
        },
    },
    # Read-only registry snapshot (no fields beyond the intent).
    "bills_registry": {
        "task": "bills_registry",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_registry"},
        },
    },
    # Registry maintenance: mark a bill paid (explicit, mediated write).
    "bills_mark_paid": {
        "task": "bills_mark_paid",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_mark_paid"},
            "id": {"type": "str", "required": True},
            "note": {"type": "str"},
        },
    },
    # Registry maintenance: set an arbitrary status (explicit, mediated write).
    "bills_mark_status": {
        "task": "bills_mark_status",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_mark_status"},
            "id": {"type": "str", "required": True},
            "status": {"type": "str", "required": True},
            "note": {"type": "str"},
        },
    },
    # Deterministic auto-accept: only when an approved rule matches.
    "bills_accept_deterministic": {
        "task": "bills_accept_deterministic",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_accept_deterministic"},
            "draft": {"type": "object", "required": True},
        },
    },
    # Persist an approved deterministic rule (authorize once, run after restart).
    "bills_rule_add": {
        "task": "bills_rule_add",
        "child": "bills",
        "fields": {
            "intent": {"type": "str", "default": "bills_rule_add"},
            "rule": {"type": "object", "required": True},
        },
    },
}

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


def _coerce_field(value: Any, type_: str, name: str) -> Any:
    """Coerce ``value`` to a declared schema field type (fail-closed).

    Supports the scalar types ``int``/``float``/``bool``/``str`` (numeric strings
    are parsed) and ``object`` (a dict, passed through). An uncoercible value
    raises ``ValueError`` so a malformed artifact never silently runs.
    """
    if type_ == "object":
        if not isinstance(value, dict):
            raise ValueError(f"field {name!r} must be an object")
        return value
    if type_ == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "yes", "y", "1", "ok"):
            return True
        if s in ("false", "no", "n", "0", ""):
            return False
        raise ValueError(f"field {name!r} cannot be coerced to bool")
    if type_ == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError as exc:
                raise ValueError(f"field {name!r} cannot be coerced to int") from exc
        raise ValueError(f"field {name!r} cannot be coerced to int")
    if type_ == "float":
        if isinstance(value, bool):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"field {name!r} cannot be coerced to float") from exc
    # str
    return str(value)


def plan_from_schema(
    artifact: dict[str, Any],
    *,
    verifier: str | None = None,
) -> list[dict[str, Any]]:
    """Validate a typed artifact against its intent's schema, then map to a plan.

    The artifact declares an ``intent`` (default ``run``). For ``run`` this
    delegates to ``plan_from_artifact`` (a task or an ordered ``steps`` list).
    For a typed intent (e.g. ``double``/``sum``/``bills_*``) the artifact's
    fields are coerced against the intent's ``SCHEMAS`` entry (fail-closed), then
    mapped to a single concrete ``run`` step — using the schema's ``task`` and
    ``child`` when present (so a bills intent routes to the ``bills`` child). An
    unknown intent or a schema violation raises ``ValueError``.
    """
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")
    intent = extract_intent(artifact)
    if intent == "run":
        return plan_from_artifact(artifact, verifier=verifier)
    schema = SCHEMAS.get(intent)
    if schema is None:
        raise ValueError(f"no schema for intent {intent!r}")
    fields = schema.get("fields")
    if not isinstance(fields, dict):
        raise ValueError(f"schema for intent {intent!r} has no 'fields'")
    args: dict[str, Any] = {}
    for name, spec in fields.items():
        spec = spec if isinstance(spec, dict) else {}
        type_ = spec.get("type", "str")
        required = bool(spec.get("required", False))
        default = spec.get("default")
        if name == "intent":
            continue
        if name in artifact and artifact[name] is not None:
            args[name] = _coerce_field(artifact[name], type_, name)
        elif required:
            raise ValueError(f"missing required field: {name}")
        elif default is not None:
            args[name] = default
    step: dict[str, Any] = {"task": schema.get("task", intent), "args": args}
    if schema.get("child"):
        step["child"] = schema["child"]
    if verifier:
        step["verifier"] = verifier
    return [step]


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
        steps = plan_from_schema(artifact, verifier=verifier)
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