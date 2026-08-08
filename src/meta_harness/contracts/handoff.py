"""Schema-constrained stage hand-off validation (versioned).

Volley 007: the verified output of a pipeline stage is validated against a
declared output schema (of the producing stage) and/or a declared input schema
(of the consuming stage) before it is accepted as the next stage's input. This
makes inter-stage data contracts first-class and prevents schema-invalid data
from flowing between stages.

The schema format is intentionally minimal and consistent with the existing tool
contract: a schema is either a single expected type name (for a scalar payload)
or a mapping of field name -> expected type name (for an object payload).
Validation is deterministic, side-effect free, and easy to reason about.
"""

from __future__ import annotations

from typing import Any

# A hand-off schema is either a single expected type name (scalar payload) or a
# mapping of field name -> expected type name (object payload).
HandoffSchema = str | dict[str, str]

_TYPE_CHECKERS: dict[str, Any] = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, float),
    "bool": lambda v: isinstance(v, bool),
    "dict": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
    "any": lambda v: True,
}


def is_valid_schema(schema: Any) -> bool:
    """Return True if ``schema`` is a well-formed hand-off schema."""
    if isinstance(schema, str):
        return schema in _TYPE_CHECKERS
    if isinstance(schema, dict):
        return all(
            isinstance(k, str) and isinstance(v, str) and v in _TYPE_CHECKERS
            for k, v in schema.items()
        )
    return False


def validate_handoff(payload: Any, schema: HandoffSchema) -> tuple[bool, str]:
    """Validate ``payload`` against ``schema``.

    Returns ``(True, message)`` on success or ``(False, message)`` on failure.
    Deterministic and side-effect free.
    """
    if isinstance(schema, str):
        checker = _TYPE_CHECKERS.get(schema)
        if checker is None:
            return False, f"Unknown schema type {schema!r}."
        if not checker(payload):
            return False, f"Payload is not of expected type {schema!r}."
        return True, f"Payload matches scalar type {schema!r}."

    if not isinstance(payload, dict):
        return False, "Payload is not a mapping; expected an object schema."
    for field, typ in schema.items():
        if field not in payload:
            return False, f"Missing required field {field!r}."
        checker = _TYPE_CHECKERS.get(typ)
        if checker is None:
            return False, f"Unknown schema type {typ!r} for field {field!r}."
        if not checker(payload[field]):
            return False, f"Field {field!r} is not of expected type {typ!r}."
    return True, "Payload matches the declared object schema."
