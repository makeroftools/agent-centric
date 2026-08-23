"""Tests for the post-return determinism pipeline (``fbp/chat_pipeline.py``).

These are pure, deterministic, offline tests of the "model proposes, code
accepts" seam: repair → schema-parse → canonicalize → request-key → pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_centric.fbp.chat_pipeline import (
    PinCache,
    canonical_json,
    canonicalize,
    collapse_ws,
    parse_json_object,
    repair_json,
    request_key,
    schema_parse,
)

# A bill-scheduling schema used across tests.
_BILL_SCHEMA = {
    "fields": {
        "vendor": {"type": "str", "required": True},
        "amount_cents": {"type": "int", "required": True},
        "due_date": {"type": "str", "required": True},
        "paid": {"type": "bool", "default": False, "enum": [True, False]},
    }
}


class TestRepairJson:
    def test_strips_fenced_json(self) -> None:
        raw = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps.'
        assert repair_json(raw) == '{"a": 1}'
        # Brevity: also no-language fence.
        assert repair_json("```\n{\"b\": 2}\n```\n") == '{"b": 2}'

    def test_clips_to_last_complete_object(self) -> None:
        raw = 'prefix {"a": 1, "b": {"c": 2}} trailing explanation'
        assert json.loads(repair_json(raw)) == {"a": 1, "b": {"c": 2}}

    def test_no_object_returns_empty(self) -> None:
        assert repair_json("just prose, no brace") == ""
        assert repair_json("") == ""

    def test_non_string_returns_empty(self) -> None:
        assert repair_json(5) == ""  # a non-string input yields empty


class TestParseJson:
    def test_parses_object(self) -> None:
        assert parse_json_object('{"x": 1}') == {"x": 1}

    def test_rejects_non_object(self) -> None:
        with pytest.raises(ValueError):
            parse_json_object("[1, 2]")

    def test_rejects_invalid(self) -> None:
        with pytest.raises(ValueError):
            parse_json_object("{not json")


class TestSchemaParse:
    def test_parses_and_coerces_types(self) -> None:
        parsed = schema_parse(
            '{"vendor": "GasCo", "amount_cents": "12345", "due_date": "2026-10-01"}',
            _BILL_SCHEMA,
        )
        assert parsed["vendor"] == "GasCo"
        assert parsed["amount_cents"] == 12345  # coerced str -> int
        assert parsed["due_date"] == "2026-10-01"
        assert parsed["paid"] is False  # default applied

    def test_required_missing_fails(self) -> None:
        with pytest.raises(ValueError):
            schema_parse('{"vendor": "GasCo"}', _BILL_SCHEMA)

    def test_unknown_keys_dropped(self) -> None:
        parsed = schema_parse(
            '{"vendor": "A", "amount_cents": 1, "due_date": "2026-10-01", '
            '"hint": "ignore me"}',
            _BILL_SCHEMA,
        )
        assert "hint" not in parsed

    def test_enum_synonym_normalized(self) -> None:
        parsed = schema_parse(
            '{"vendor": "A", "amount_cents": 1, "due_date": "2026-10-01", "paid": "yes"}',
            _BILL_SCHEMA,
        )
        assert parsed["paid"] is True

    def test_enum_out_of_set_fails_when_not_normalizing(self) -> None:
        with pytest.raises(ValueError):
            schema_parse(
                '{"vendor": "A", "amount_cents": 1, "due_date": "2026-10-01", "paid": "maybe"}',
                _BILL_SCHEMA,
                normalize_enums=False,
            )


class TestCanonicalize:
    def test_sorts_keys_and_drops_none_keys(self) -> None:
        # Canonical JSON omits None-valued keys entirely (None = absent).
        out = canonicalize({"b": 2, "a": None, "c": {"z": 1, "y": None}})
        assert out == {"b": 2, "c": {"z": 1}}
        assert "a" not in out
        assert "y" not in out["c"]

    def test_collapses_whitespace_in_strings(self) -> None:
        out = canonicalize({"s": "  hello   world  "})
        assert out["s"] == "hello world"

    def test_canonical_json_is_stable(self) -> None:
        a = canonical_json({"b": 2, "a": [3, 1]})
        b = canonical_json({"a": [3, 1], "b": 2})
        assert a == b

    def test_collapse_ws(self) -> None:
        assert collapse_ws("  a   b\t c\n") == "a b c"


class TestRequestKey:
    def test_identical_inputs_same_key(self) -> None:
        k1 = request_key(model_id="m", prompt="p", params={"t": 0.0})
        k2 = request_key(model_id="m", prompt="p", params={"t": 0.0})
        assert k1 == k2

    def test_different_inputs_different_key(self) -> None:
        k1 = request_key(model_id="m", prompt="p", params={"t": 0.0})
        k2 = request_key(model_id="m", prompt="p", params={"t": 0.1})
        assert k1 != k2

    def test_model_or_template_change_changes_key(self) -> None:
        a = request_key(model_id="m1", prompt="p")
        b = request_key(model_id="m2", prompt="p")
        assert a != b


class TestPinCache:
    def test_pin_and_hit(self) -> None:
        cache = PinCache()
        key = request_key(model_id="m", prompt="p")
        assert cache.has(key) is False
        cache.put(key, {"a": 1})
        assert cache.has(key) is True
        assert cache.get(key) == {"a": 1}

    def test_miss_returns_none(self) -> None:
        cache = PinCache()
        assert cache.get("missing") is None

    def test_on_disk_survives_restart(self, tmp_path: Path) -> None:
        path = str(tmp_path / "pins.json")
        c1 = PinCache(path)
        c1.put("k", {"answer": 42})
        c2 = PinCache(path)
        assert c2.get("k") == {"answer": 42}
        assert c2.size() == 1

    def test_absent_file_is_empty(self, tmp_path: Path) -> None:
        c = PinCache(str(tmp_path / "nope.json"))
        assert c.size() == 0