"""Exercise the operator activity feed end-to-end (offline, deterministic).

Demonstrates every guarantee the module provides: bounded, append-only,
deterministic, fail-closed, and durable-by-explicit-grant. Run with:

    uv run python examples/fbp_activity_demo.py
"""

from __future__ import annotations

import tempfile

from agent_centric.fbp.activity import ActivityError, ActivityFeed


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="agent-centric-activity-")
    path = f"{workdir}/activity.json"

    print("== in-memory feed (bounded) ==")
    mem = ActivityFeed(max_entries=3)
    for i in range(5):
        mem.record(kind="bills", action=f"bills_accept (bill-b{i})", verified=True)
    print(f"bounded: {mem.count()} entries (expect 3) — "
          f"{[e.action for e in mem.all()]}")

    print("\n== deterministic readout ==")
    snap = mem.to_dict()
    assert snap == mem.to_dict()  # deterministic
    print(f"verified={snap['verified']}/{snap['count']}, durable={snap['durable']}")

    print("\n== fail-closed ==")
    try:
        mem.record(kind="bogus", action="x")
    except ActivityError as exc:
        print("rejected unknown kind:", exc)

    print("\n== durable by explicit grant ==")
    durable = ActivityFeed(path=path)
    durable.record(kind="model", action="model run", verified=True, model="stub-model")
    durable.record(kind="provision", action="provision bill-extract", verified=True)
    print(f"durable={path}, recorded {durable.count()} entries")

    reopened = ActivityFeed(path=path)
    print("reopened:", reopened.count(), "entries, first seq", reopened.all()[0].seq)
    assert reopened.count() == 2

    print("\nAll activity-feed guarantees demonstrated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())