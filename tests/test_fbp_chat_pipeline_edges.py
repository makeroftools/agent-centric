"""Fail-closed persistence edges of the post-return determinism pipeline.

``PinCache`` persists the "model proposes, code accepts" pin atomically and
loads it fail-closed: a corrupt or unreadable pin file must never crash a run
or poison the deterministic spine — it silently yields an empty cache (a pin
cache is always a performance/lexical-determinism aid, never an authority). The
module-level ``canonical_load_json`` and the atomic ``_save`` ``os.replace``
path are the durability seams exercised here. All tests are pure, offline, and
deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.fbp.chat_pipeline import (
    PinCache,
    canonical_load_json,
    request_key,
)


class TestCanonicalLoadJson:
    "Direct reader used by PinCache: returns the JSON value from a file."

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "pin.json"
        path.write_text('{"k": {"value": 42}}', encoding="utf-8")
        assert canonical_load_json(str(path)) == {"k": {"value": 42}}

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        # The reader itself surfaces an OSError; PinCache._load turns that into
        # an empty (fail-closed) cache.
        with pytest.raises(OSError):
            canonical_load_json(str(tmp_path / "nope.json"))


class TestPinCacheFailClosedLoad:
    "A corrupt or non-JSON pin file must load fail-closed as an empty cache."

    def test_corrupt_pin_file_yields_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json [[[", encoding="utf-8")
        cache = PinCache(str(path))
        # No crash; no poisoned entries; a later put still works.
        assert cache.size() == 0
        cache.put("k", {"value": 1})
        assert cache.get("k") == {"value": 1}

    def test_absent_pin_file_is_empty(self, tmp_path: Path) -> None:
        cache = PinCache(str(tmp_path / "missing.json"))
        assert cache.size() == 0

    def test_load_non_object_ignored(self, tmp_path: Path) -> None:
        # A JSON file that parses but is not a dict (e.g. a list) is ignored
        # fail-closed rather than raising.
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        cache = PinCache(str(path))
        assert cache.size() == 0

    def test_save_uses_atomic_replace(self, tmp_path: Path) -> None:
        # The pin is persisted via temp-write + os.replace (no in-place write),
        # so a crash mid-save never leaves a torn file.
        path = tmp_path / "pin.json"
        cache = PinCache(str(path))
        cache.put(request_key(model_id="m", prompt="p"), {"answer": 42})
        assert cache.size() == 1
        # Reloaded from disk survives restart.
        again = PinCache(str(path))
        assert again.get(request_key(model_id="m", prompt="p")) == {"answer": 42}