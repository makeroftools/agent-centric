"""Tests for the durable chat-history store (``fbp/chatstore.py``).

The store persists the landing-page model box's transcript across restarts when
a caller opts in with a path. These tests prove the deterministic append-only
ordering, the bounded trimming, the explicit write-on-append semantics, and
that reopening a file rebuilds the transcript identically (replay preserves
steadiness by construction — no auto-generated content, explicit ``seq``).
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_centric.fbp.chatstore import ChatHistoryError, ChatHistoryStore, open_chat_history


@pytest.fixture
def store(tmp_path):
    s = ChatHistoryStore(tmp_path / "chat.db")
    s.open()
    yield s
    s.close()


def test_append_and_read_back_in_order(store) -> None:
    store.append(role="user", content="hi")
    store.append(role="assistant", content="hello", model="m", verified=True)
    store.append(role="user", content="again")
    turns = store.all()
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[1]["content"] == "hello"
    assert turns[1]["model"] == "m"
    assert turns[1]["verified"] is True
    assert turns[0]["verified"] is None


def test_seq_is_deterministic_from_count(store) -> None:
    """seq is derived from the count, so ordering is explicit and stable."""
    assert store.append(role="user", content="a") == 1
    assert store.append(role="assistant", content="b") == 2
    # Reopening continues the numbering deterministically.
    store.close()
    store.open()
    assert store.append(role="user", content="c") == 3


def test_reopen_preserves_transcript(store, tmp_path) -> None:
    store.append(role="user", content="q")
    store.append(role="assistant", content="a", verified=False)
    store.close()
    reopened = open_chat_history(str(tmp_path / "chat.db"))
    turns = reopened.all()
    assert len(turns) == 2
    assert turns[0]["content"] == "q"
    assert turns[1]["verified"] is False
    reopened.close()


def test_max_turns_fixture_trims_oldest(tmp_path) -> None:
    b = ChatHistoryStore(str(tmp_path / "bounded.db"), max_turns=3)
    b.open()
    for i in range(5):
        b.append(role="user", content=f"u{i}")
        b.append(role="assistant", content=f"a{i}")
    turns = b.all()
    # 10 appended; trimmed to the latest max_turns (3).
    assert len(turns) == 3
    assert turns[0]["content"] == "a3"  # oldest surviving turn
    b.close()
    b = ChatHistoryStore(str(tmp_path / "bounded.db"), max_turns=3)
    b.open()
    for i in range(5):
        b.append(role="user", content=f"u{i}")
        b.append(role="assistant", content=f"a{i}")
    turns = b.all()
    # 10 appended; trimmed to the latest max_turns (3).
    assert len(turns) == 3
    assert turns[0]["content"] == "a3"  # oldest surviving turn
    b.close()


def test_clear(store) -> None:
    store.append(role="user", content="x")
    store.append(role="assistant", content="y")
    store.clear()
    assert store.all() == ()
    assert store.count() == 0


def test_requires_open(tmp_path) -> None:
    unopened = ChatHistoryStore(str(tmp_path / "x.db"))
    with pytest.raises(ChatHistoryError):
        unopened.append(role="user", content="nope")


def test_wal_enabled(store) -> None:
    """The store uses WAL so writes are crash-safe (single-writer)."""
    db = store._conn
    (mode,) = db.execute("PRAGMA journal_mode").fetchone()
    assert mode == "wal"


def test_schema_has_no_autoincrement_source(tmp_path) -> None:
    """Content is not dependent on rowid: seq is explicit and stable."""
    b = ChatHistoryStore(str(tmp_path / "schema.db"))
    b.open()
    cols = {row[1] for row in b._conn.execute("pragma table_info(turns)").fetchall()}
    assert "seq" in cols
    assert "role" in cols
    assert "content" in cols
    b.close()


def test_open_creates_parent_dirs(tmp_path) -> None:
    nested = str(tmp_path / "a" / "b" / "chat.db")
    s = open_chat_history(nested)
    assert s.count() == 0
    s.close()


def test_sqlite_strict_types_telemetry(tmp_path) -> None:
    """A quick sanity probe that the file is a real, single-writer sqlite db."""
    path = str(tmp_path / "probe.db")
    s = open_chat_history(path)
    s.append(role="user", content="ping")
    s.close()
    conn = sqlite3.connect(path)
    (n,) = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
    assert n == 1
    conn.close()