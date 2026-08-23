"""Tests for the operator activity feed (``fbp/activity.py``).

The activity feed is the audit counterpart to the chat history and artifact
vault: a bounded, append-only, optionally-durable log of operator actions, each
carrying its verification status. These tests prove it is deterministic,
bounded, append-only, fail-closed, and durable-by-explicit-grant.
"""

from __future__ import annotations

import json
import os

import pytest

from agent_centric.fbp.activity import (
    ACTIVITY_KINDS,
    ActivityError,
    ActivityFeed,
)


class TestRecord:
    def test_record_appends_in_order(self) -> None:
        feed = ActivityFeed()
        feed.record(kind="model", action="model run", verified=True, model="stub-model")
        feed.record(kind="bills", action="bills_accept", detail={"id": "b1"}, verified=True)
        entries = feed.all()
        assert len(entries) == 2
        # Monotonic sequence numbers, oldest first.
        assert entries[0].seq == 0
        assert entries[1].seq == 1
        assert entries[0].kind == "model"
        assert entries[1].kind == "bills"

    def test_record_is_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        feed = ActivityFeed()
        entry = feed.record(kind="action", action="run demo", verified=True)
        assert entry.seq == 0
        # The returned entry is frozen (dataclass(frozen=True)).
        with pytest.raises(FrozenInstanceError):
            entry.seq = 99  # type: ignore[misc]

    def test_record_rejects_unknown_kind(self) -> None:
        feed = ActivityFeed()
        with pytest.raises(ActivityError):
            feed.record(kind="bogus", action="x")

    def test_record_rejects_empty_action(self) -> None:
        feed = ActivityFeed()
        with pytest.raises(ActivityError):
            feed.record(kind="model", action="   ")

    def test_verified_count(self) -> None:
        feed = ActivityFeed()
        feed.record(kind="model", action="a", verified=True)
        feed.record(kind="bills", action="b", verified=False)
        feed.record(kind="action", action="c", verified=True)
        assert feed.verified_count() == 2

    def test_kinds_are_a_closed_set(self) -> None:
        assert set(ACTIVITY_KINDS) == {
            "model", "orchestrate", "network", "bills", "provision", "action",
        }


class TestBounded:
    def test_feed_is_bounded(self) -> None:
        feed = ActivityFeed(max_entries=3)
        for i in range(10):
            feed.record(kind="action", action=f"a{i}", verified=True)
        entries = feed.all()
        assert len(entries) == 3
        # Oldest dropped first; the newest three remain.
        assert [e.action for e in entries] == ["a7", "a8", "a9"]
        assert feed.count() == 3

    def test_max_entries_must_be_positive(self) -> None:
        with pytest.raises(ActivityError):
            ActivityFeed(max_entries=0)


class TestDeterminism:
    def test_to_dict_is_deterministic(self) -> None:
        feed = ActivityFeed()
        feed.record(kind="model", action="m", detail={"x": 1}, verified=True)
        feed.record(kind="bills", action="b", verified=False)
        a = feed.to_dict()
        b = feed.to_dict()
        assert a == b
        assert a["count"] == 2
        assert a["verified"] == 1
        assert a["durable"] is False

    def test_ordering_ignores_timestamp(self) -> None:
        """A wall-clock timestamp is informational only; ordering is by seq."""
        feed = ActivityFeed()
        feed.record(kind="action", action="first", ts="2026-01-01")
        feed.record(kind="action", action="second", ts="2025-01-01")
        entries = feed.all()
        assert [e.action for e in entries] == ["first", "second"]


class TestClear:
    def test_clear_drops_everything(self) -> None:
        feed = ActivityFeed()
        feed.record(kind="action", action="a", verified=True)
        feed.clear()
        assert feed.count() == 0
        assert feed.all() == ()


class TestDurable:
    def test_durable_by_explicit_grant(self, tmp_path) -> None:
        """With a granted path the feed persists and reloads across opens."""
        path = str(tmp_path / "activity.json")
        f1 = ActivityFeed(path=path)
        f1.record(kind="model", action="m", verified=True, model="stub-model")
        f1.record(kind="bills", action="accept", detail={"id": "b1"}, verified=True)
        assert f1.to_dict()["durable"] is True

        f2 = ActivityFeed(path=path)
        assert f2.count() == 2
        entries = f2.all()
        assert entries[0].kind == "model"
        assert entries[1].kind == "bills"
        assert entries[1].detail == {"id": "b1"}
        # Sequence continues after reload (no duplicate seq).
        f2.record(kind="action", action="next", verified=True)
        assert f2.all()[-1].seq == 2

    def test_durable_clear_clears_file_too(self, tmp_path) -> None:
        path = str(tmp_path / "activity.json")
        f1 = ActivityFeed(path=path)
        f1.record(kind="action", action="a", verified=True)
        f1.clear()
        f2 = ActivityFeed(path=path)
        assert f2.count() == 0

    def test_corrupt_durable_file_degrades_to_empty(self, tmp_path) -> None:
        path = str(tmp_path / "activity.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        feed = ActivityFeed(path=path)
        assert feed.count() == 0
        # Still usable.
        feed.record(kind="action", action="ok", verified=True)
        assert feed.count() == 1

    def test_durable_file_is_valid_json(self, tmp_path) -> None:
        path = str(tmp_path / "activity.json")
        feed = ActivityFeed(path=path)
        feed.record(kind="action", action="a", verified=True)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["entries"][0]["action"] == "a"
        assert os.path.exists(path)


class TestFailClosed:
    def test_skips_corrupt_entries_on_reload(self, tmp_path) -> None:
        path = str(tmp_path / "activity.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "entries": [
                        {"seq": 0, "kind": "action", "action": "good", "verified": True},
                        {"seq": 1, "kind": "bogus", "action": "bad", "verified": True},
                        {"seq": 2, "kind": "action", "action": "", "verified": True},
                        "not-a-dict",
                    ]
                },
                fh,
            )
        feed = ActivityFeed(path=path)
        assert feed.count() == 1
        assert feed.all()[0].action == "good"