"""End-to-end demo of durable state + local audit in the FBP subsystem.

Shows the two purpose-separated stores the user chose (state + trajectory),
both on-demand, persistent, and deterministic:

- **state**: a mutable, single-writer key/value store an agent owns. Writes
  are idempotent (keyed by the directive fingerprint); re-opening the store
  and reading back proves durability across process boundaries.
- **trajectory/audit**: an append-only, write-once local record — the local
  start of chain audit. Chain audit begins *here*, locally, on the agent.

Everything stays deterministic: no auto-generated keys, idempotent replays,
sorted order in the audit. No network, no daemons — inproc.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent_centric.fbp import FbpDriver, store


def double(value: int) -> int:
    return value * 2


def even(v) -> bool:
    return isinstance(v, int) and v % 2 == 0


def main() -> None:
    d = Path(tempfile.mkdtemp(prefix="fbp-demo-"))
    state_path = d / "registry.state.db"
    trail_path = d / "trajectory.audit.db"

    print(f"state file:     {state_path}")
    print(f"trajectory file:{trail_path}\n")

    with FbpDriver() as driver:
        # 1. Grant the durable state + trajectory stores on demand.
        driver.configure(state=str(state_path), trajectory=str(trail_path))

        # 2. Run a verified task; it is recorded in the local audit.
        driver.register("double", double)
        driver.register("even", even)
        driver.configure(tasks=("double",), verifiers=("even",), verifier="even")
        driver.run("double", {"value": 21})  # -> 42 (even, verified)

        # 3. Persist state idempotently (single-writer, deterministic keys).
        driver.state_set("bill-b3", {"status": "paid", "amount_cents": 12345})
        driver.state_set("bill-b3", {"status": "paid", "amount_cents": 12345})  # replay
        driver.state_set("bill-b4", {"status": "open", "amount_cents": 999})

        # 4. Read the durable state back.
        got = driver.state_get("bill-b3")
        print(f"state get bill-b3 -> {got.value}")

        # 4b. Delegate a run to a child; the parent records a 'relay' hop that
        #     completes the chain audit for that run.
        driver.spawn("child")
        driver.configure_child("child", tasks=("double",))
        driver.run("double", {"value": 10}, child="child")  # -> 20, relayed up

        # 5. Local audit is the start of the chain (the parent records relay
        #    hops for the delegated child's responses too).
        audit = driver.audit()
        print(f"\nlocal audit ({len(audit.value)} events):")
        for row in audit.value:
            print(
                f"  {row['correlation_id']:<14} {row['kind']:<8} "
                f"verified={row['verified']} value={row['value']!r}"
            )

    # 6. Durability: reopen the files after the driver (and its state) is gone.
    print("\nreopening stores after the driver is gone...")
    st = store.open_state(state_path)
    print(f"  state bill-b3  -> {st.get('bill-b3')}")
    print(f"  state bill-b4  -> {st.get('bill-b4')}")
    st.close()
    tr = store.open_trajectory(trail_path)
    print(f"  trajectory rows -> {tr.count()}")
    tr.close()


if __name__ == "__main__":
    main()