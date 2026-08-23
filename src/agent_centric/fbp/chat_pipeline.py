"""Post-return determinism: validate → canonicalize → pin a model's output.

This is the operational core of the "model proposes, code accepts" rule. When
free-text output comes back from an LLM, ordinary code parses it into a typed,
canonical artifact and **pins** the first accepted result under a stable key
derived from the inputs — so identical requests replay the pin instead of
re-invoking the model. That is the only widely available path to byte-identical
results across hosted APIs (a *cache*, not a sampler setting).

The module is pure, deterministic, and offline:

- ``repair_json`` — deterministic isolation of a JSON payload from free text
  (strip fences / clip to the last complete object), never a model.
- ``schema_parse`` — coerce raw text to a typed structure, fail-closed.
- ``canonicalize`` — fixed normalization into one canonical JSON form (sorted
  keys, coerced types, enum synonyms, collapsed whitespace, ``None`` dropped).
- ``request_key`` — stable input-identity key for the pin cache.
- ``PinCache`` — first accepted result is pinned; a hit serves it directly.

Everything here is ordinary deterministic software — the layer the FBP spine can
audit, replay, and constrain with an envelope.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# A JSON payload fenced in markdown: ```json ... ``` or ``` ... ```.
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_WS_RUN = re.compile(r"\s+")

# Synonym sets for enum coercion (the doc's ``yes``/``true``/``Y`` -> ``true``).
_TRUE_SET = {"true", "yes", "y", "1", "ok", "accept", "accepted"}
_FALSE_SET = {"false", "no", "n", "0", ""}


# ---------------------------------------------------------------------------
# Text → canonical typed structure (deterministic)
# ---------------------------------------------------------------------------


def repair_json(text: str) -> str:
    """Isolate a JSON object payload from free text (deterministic).

    Strips a fenced ````` ```json ... ``` `````` block if present; otherwise
    clips ``text`` to the last balanced ``{...}`` object so trailing prose,
    markdown fences, and explanations are dropped. Returns ``""`` when no JSON
    object is present. This is a repair *without a model*; it never raises.
    """
    if not isinstance(text, str):
        return ""
    fence = _JSON_FENCE.search(text)
    if fence:
        return fence.group(1).strip()

    start = -1
    end = -1
    depth = 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    end = i  # last complete object end
    if start != -1 and end >= start:
        return text[start : end + 1].strip()
    return ""


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse (after repair) ``text`` into a JSON dict; raise ValueError if invalid.

    Fail-closed: only a JSON object is accepted. Arrays/scalars and unparseable
    text raise ``ValueError`` so the caller can choose repair → retry → reject.
    """
    cleaned = repair_json(text).strip()
    if not cleaned:
        raise ValueError("no JSON payload found")
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object, got " + type(data).__name__)
    return data


# ---------------------------------------------------------------------------
# Schema parse (typed, fail-closed)
# ---------------------------------------------------------------------------

# Scalar types we can coerce deterministically.
_INT = "int"
_FLOAT = "float"
_BOOL = "bool"
_STR = "str"
_SCALARS = {_INT, _FLOAT, _BOOL, _STR}


def _coerce_scalar(value: Any, type_: str) -> Any:
    """Coerce ``value`` to a named scalar type, or raise ``ValueError``.

    Numeric strings are parsed into numbers (the doc's "parse dates and
    numbers into typed values"); synonyms are NOT handled here (that is
    enum normalization, done separately).
    """
    if type_ == _BOOL:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in _TRUE_SET:
            return True
        if s in _FALSE_SET:
            return False
        raise ValueError(f"cannot coerce {value!r} to bool")
    if type_ == _INT:
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
                raise ValueError(f"cannot coerce {value!r} to int") from exc
        raise ValueError(f"cannot coerce {value!r} to int")
    if type_ == _FLOAT:
        if isinstance(value, bool):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cannot coerce {value!r} to float") from exc
    # str
    return str(value)


def _normalize_enum(value: Any, enum: list[Any]) -> Any:
    """Map ``value`` onto ``enum`` by exact, then case-insensitive, equality.

    Raises ``ValueError`` if no member matches (fail-closed).
    """
    if value in enum:
        return value
    lowered = str(value).strip().lower()
    for member in enum:
        if str(member).strip().lower() == lowered:
            return member
    raise ValueError(f"{value!r} not in enum {enum}")


def schema_parse(
    text: str,
    schema: dict[str, Any],
    *,
    coerce_types: bool = True,
    normalize_enums: bool = True,
) -> dict[str, Any]:
    """Parse raw model text into a typed dict against ``schema`` (fail-closed).

    ``schema``: ``{"fields": {"name": {"type": "int|float|bool|str",
    "default"?, "enum"?}} , ...}``. Rules (deterministic):

    - Unknown keys in the raw object are dropped (only declared fields pass).
    - A ``required`` field that is absent raises ``ValueError``.
    - Type is coerced when ``coerce_types`` (the default).
    - An ``enum`` constrains the value; synonyms normalize when
      ``normalize_enums`` (the default), else an out-of-set value raises.
    - An absent optional field with a ``default`` takes the default; one
      without a default is omitted (the canonical object has no ``None``).
    """
    fields = schema.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("schema must define a 'fields' dict")
    raw = parse_json_object(text)

    out: dict[str, Any] = {}
    for name, spec in fields.items():
        spec = spec if isinstance(spec, dict) else {}
        type_ = spec.get("type", _STR)
        required = bool(spec.get("required", False))
        default = spec.get("default")
        present = name in raw and raw[name] is not None
        if present:
            value = raw[name]
            if type_ in _SCALARS and coerce_types:
                value = _coerce_scalar(value, type_)
            enum = spec.get("enum")
            if enum is not None:
                value = _normalize_enum(value, [e for e in enum]) if normalize_enums else value
                if value not in enum:
                    raise ValueError(f"{name}: {value!r} not in enum {enum}")
            out[name] = value
        else:
            if required:
                raise ValueError(f"missing required field: {name}")
            if default is not None:
                out[name] = default
            # else: optional & absent & no default -> omitted (None canonical = absent)
    return out


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def collapse_ws(text: str) -> str:
    """Collapse internal whitespace runs and strip edges (canonical string)."""
    return _WS_RUN.sub(" ", text.strip())


def canonicalize(
    obj: Any,
    *,
    sort_keys: bool = True,
    strip_ws: bool = True,
) -> Any:
    """Return a canonical copy of ``obj`` (JSON-sort-stable).

    Recursively sorts dict keys, collapses whitespace in string leaves, and
    drops ``None`` values (canonical representation: ``None`` means absent).
    """

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            items = sorted(node.items(), key=lambda kv: kv[0]) if sort_keys else list(node.items())
            out: dict[str, Any] = {}
            for k, v in items:
                if v is None:
                    continue
                out[k] = _walk(v)
            return out
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str) and strip_ws:
            return collapse_ws(node)
        return node

    return _walk(obj)


def canonical_json(value: Any) -> str:
    """Deterministic JSON string (sorted keys, no extra whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Input-identity keying + pinning
# ---------------------------------------------------------------------------


def request_key(
    *,
    model_id: str,
    prompt: str,
    template_version: str = "v0",
    context: str = "",
    params: dict[str, Any] | None = None,
) -> str:
    """A stable key identifying the logical request (the doc's §6 key).

    Every input that changes the answer — model id, prompt, template version,
    retrieved context, decoding params — is folded deterministically into the
    hash, so a pin hit is only served for truly identical requests.
    """
    material = canonical_json(
        {
            "model": model_id,
            "template_version": template_version,
            "prompt": prompt,
            "context": context,
            "params": params or {},
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class PinCache:
    """A deterministic pin-cache keyed by input identity.

    The first accepted (parsed + canonicalized) artifact is pinned; a later
    identical request returns the pinned artifact without re-invoking the model.
    In-memory by default, with an optional on-disk JSON backing file so a pin
    survives restarts. Keys derive deterministically from ``request_key`` —
    there are no auto-generated ids.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._store: dict[str, Any] = {}
        if path is not None:
            self._load()

    def _load(self) -> None:
        import os

        if not self._path or not os.path.exists(self._path):
            return
        try:
            raw = canonical_load_json(self._path)
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._store = dict(raw)

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def has(self, key: str) -> bool:
        return key in self._store

    def put(self, key: str, value: Any) -> None:
        self._store[key] = value
        if self._path is not None:
            self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        import os
        import tempfile

        directory = os.path.dirname(os.path.abspath(self._path)) or "."
        os.makedirs(directory, exist_ok=True)
        (fd, tmp_path) = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(canonical_json(self._store))
            os.replace(tmp_path, self._path)
        except BaseException:
            from contextlib import suppress

            with suppress(OSError):
                os.unlink(tmp_path)
            raise

    def size(self) -> int:
        return len(self._store)


def canonical_load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)