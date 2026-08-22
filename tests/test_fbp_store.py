"""Tests for durable, deterministic FBP storage (state + trajectory/audit).

These prove the on-demand persistence layer and how it fits the agent core:

- **state**: an idempotent, single-writer, deterministic key/value store —
  replay applies the same row, a genuine update (a distinct directive) updates;
  read-only grants close the write path.
- **trajectory/audit**: an append-only, write-once local record — the local
  start of *chain* audit. Recorded under the directive fingerprint, ordered,
  and failing-closed on correlation-id reuse.

The store itself is stdlib SQLite and offline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_centric.fbp import store
from agent_centric.fbp.store import StoreError


@pytest.fixture
def tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="fbp-store-"))


class TestStateStore:
    def test_set_get_count(self, tmp: Path) -> None:
        st = store.open_state(tmp / "s.db")
        st.set("b3", {"status": "paid"}, fingerprint="fp-b3")
        assert st.get("b3")["status"] == "paid"
        st.set("b3", {"status": "paid"}, fingerprint="fp-b3")  # idempotent
        assert st.count() == 1
        st.close()

    def test_duplicate_fingerprint_is_idempotent(self, tmp: Path) -> None:
        st = store.open_state(tmp / "s.db")
        st.set("k", {"v": 1}, fingerprint="fp")
        st.set("k", {"v": 1}, fingerprint="fp")  # replay -> no-op
        st.set("k", {"v": 1}, fingerprint="fp")  # replay -> still no-op
        assert st.get("k") == {"v": 1}
        st.close()

    def test_distinct_directive_is_a_real_update(self, tmp: Path) -> None:
        st = store.open_state(tmp / "s.db")
        st.set("k", {"v": 1}, fingerprint="fp1")
        st.set("k", {"v": 2}, fingerprint="fp2")
        assert st.get("k") == {"v": 2}
        assert st.count() == 1
        st.close()

    def test_read_only_rejects_write(self, tmp: Path) -> None:
        path = tmp / "s.db"
        w = store.open_state(path)
        w.set("k", 1, fingerprint="fp")
        w.close()
        r = store.open_state(path, read_only=True)
        assert r.get("k") == 1
        with pytest.raises(StoreError, match="read-only"):
            r.set("k", 2, fingerprint="fp")
        r.close()

    def test_read_only_missing_store_fails_closed(self, tmp: Path) -> None:
        with pytest.raises(StoreError):
            store.open_state(tmp / "does-not-exist.db", read_only=True)

    def test_persists_across_close_open(self, tmp: Path) -> None:
        path = tmp / "s.db"
        st = store.open_state(path)
        st.set("k", {"v": 1}, fingerprint="fp")
        st.close()
        st2 = store.open_state(path)
        assert st2.get("k") == {"v": 1}
        st2.close()


class TestTrajectoryStore:
    def test_append_and_ordered(self, tmp: Path) -> None:
        tr = store.open_trajectory(tmp / "t.db")
        tr.record(correlation_id="r1", kind="result", node="a", verified=True,
                  value=42, fingerprint="fp1")
        tr.record(correlation_id="r2", kind="error", node="a", verified=False,
                  error="nope", fingerprint="fp2")
        rows = tr.all()
        assert [r["correlation_id"] for r in rows] == ["r1", "r2"]
        assert rows[0]["value"] == 42
        assert rows[1]["error"] == "nope"
        tr.close()

    def test_write_once_fails_closed(self, tmp: Path) -> None:
        tr = store.open_trajectory(tmp / "t.db")
        tr.record(correlation_id="r1", kind="result", node="a", verified=True,
                  value=1, fingerprint="fp1")
        with pytest.raises(StoreError, match="already recorded"):
            tr.record(correlation_id="r1", kind="result", node="a", verified=True,
                      value=2, fingerprint="fp2")
        tr.close()

    def test_persists_across_close_open(self, tmp: Path) -> None:
        path = tmp / "t.db"
        tr = store.open_trajectory(path)
        tr.record(correlation_id="r1", kind="result", node="a", verified=True,
                  value=42, fingerprint="fp1")
        tr.close()
        tr2 = store.open_trajectory(path)
        assert tr2.count() == 1
        assert tr2.all()[0]["value"] == 42
        tr2.close()

    def test_fingerprint_and_parent_are_captured(self, tmp: Path) -> None:
        tr = store.open_trajectory(tmp / "t.db")
        tr.record(correlation_id="r1", kind="result", node="a", verified=True,
                  value=1, fingerprint="fp|run|{\"a\": 1}", parent="tcp://root")
        row = tr.all()[0]
        assert row["fingerprint"] == "fp|run|{\"a\": 1}"
        assert row["parent"] == "tcp://root"
        tr.close()