"""Tests for the high-level FBP driver (the easy-UX layer).

These prove that ``FbpDriver`` makes the directive/response protocol usable
without touching ZeroMQ frames or an event loop:

- register / resolve (registry-as-agent, passive catalog),
- configure (parent provides context: rules, verifiers, task allowlist),
- run a task locally (verified result or explicit failure),
- spawn a real child and delegate a run down to it (verified response up),
- the correctness spine: a parent re-verifies a child's value on the way up,
- fail-closed on an unknown delegation target.

No network, no daemons — the driver is deterministic and offline-testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_centric.fbp import FbpDriver, register_callable


def _double(value: int) -> int:
    return value * 2


def _even(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 0


def _odd(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 1


class TestRegistryAsAgent:
    def test_register_then_resolve(self) -> None:
        with FbpDriver() as driver:
            driver.register("double", _double, source_url="file:///tasks/double.py")
            resp = driver.resolve("double")
            assert resp.verified is True
            assert resp.value["name"] == "double"
            assert resp.value["source_url"] == "file:///tasks/double.py"

    def test_resolve_unknown_fails_closed(self) -> None:
        with FbpDriver() as driver:
            resp = driver.resolve("ghost")
            assert resp.verified is False
            assert resp.error is not None


class TestRunLocal:
    def test_run_verified_task(self) -> None:
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21})
            assert resp.verified is True
            assert resp.value == 42

    def test_run_fails_verification(self) -> None:
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.register("odd", _odd)
            driver.configure(tasks=("double",), verifiers=("odd",), verifier="odd")
            # 21*2 = 42 is even; the odd-verifier rejects it.
            resp = driver.run("double", {"value": 21})
            assert resp.verified is False
            assert resp.error is not None

    def test_run_unknown_task_fails_closed(self) -> None:
        with FbpDriver() as driver:
            resp = driver.run("ghost", {})
            assert resp.verified is False
            assert resp.error is not None


class TestRunPlan:
    """run_plan executes a deterministic sequence of run steps, failing closed
    on the first unverified one."""

    def test_all_verified(self) -> None:
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            result = driver.run_plan(
                [
                    {"task": "double", "args": {"value": 21}},
                    {"task": "double", "args": {"value": 5}},
                ]
            )
            assert result["ok"] is True
            assert result["completed"] == 2
            assert [r["value"] for r in result["results"]] == [42, 10]

    def test_fails_closed_on_first_unverified(self) -> None:
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.register("odd", _odd)
            driver.configure(tasks=("double",), verifiers=("odd",), verifier="odd")
            result = driver.run_plan(
                [
                    {"task": "double", "args": {"value": 21}},  # 42 even -> fails
                    {"task": "double", "args": {"value": 1}},
                ]
            )
            assert result["ok"] is False
            assert result["completed"] == 0
            assert result["failed"]["verified"] is False
            assert result["failed"]["error"] is not None

    def test_plan_is_replayable(self) -> None:
        """A plan's runs are normal run directives, recorded in the ledger and
        replayed by replay_session."""
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run_plan(
                [
                    {"task": "double", "args": {"value": 21}},
                    {"task": "double", "args": {"value": 5}},
                ]
            )
            result = driver.replay_session()
            assert result["ok"] is True, result["failed"]
            assert result["runs"] == 2

    def test_empty_plan_fails_closed(self) -> None:
        with FbpDriver() as driver, pytest.raises(ValueError, match="non-empty"):
            driver.run_plan([])



class TestMediatedSpawnDelegation:
    def test_spawn_and_delegate(self) -> None:
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            spawn = driver.spawn("child")
            assert spawn.verified is True
            driver.configure_child("child", tasks=("double",))
            resp = driver.run("double", {"value": 21}, child="child")
            assert resp.verified is True
            assert resp.value == 42
            assert resp.node == "child"

    def test_unknown_delegation_target_fails_closed(self) -> None:
        with FbpDriver() as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21}, child="ghost")
            # The parent cannot delegate to an unknown child; it must produce a
            # terminal response rather than hang or silently drop.
            assert resp.verified is False
            assert resp.error is not None

    def test_parent_reverifies_child_on_upward_path(self) -> None:
        with FbpDriver() as driver:
            register_callable("double", _double)
            register_callable("odd", _odd)
            driver.configure(tasks=("double",), verifiers=("odd",), verifier="odd")
            driver.spawn("child")
            driver.configure_child("child", tasks=("double",))
            # Child returns 42 (even); the parent's odd-verifier rejects it.
            resp = driver.run("double", {"value": 21}, child="child")
            assert resp.verified is False
            assert resp.error is not None


class TestStoreAgent:
    """A StoreAgent is a single-writer registry over a StateStore, reached
    through the parent's mediated delegation, bounded by a key allowlist."""

    def test_store_agent_serves_read_and_write(self, tmp_path: Path) -> None:
        with FbpDriver() as driver:
            driver.spawn("store", kind="store")
            driver.configure_child(
                "store",
                state=str(tmp_path / "store.db"),
                store_keys=("bill-b3", "bill-b4"),
            )
            ok = driver.run(
                "store_set",
                {"key": "bill-b3", "value": {"status": "paid"}},
                child="store",
            )
            assert ok.verified is True
            ok2 = driver.run(
                "store_set",
                {"key": "bill-b3", "value": {"status": "paid"}},
                child="store",
            )  # idempotent replay
            assert ok2.verified is True
            got = driver.run("store_get", {"key": "bill-b3"}, child="store")
            assert got.verified is True
            assert got.value["status"] == "paid"
            assert got.node == "store"

    def test_store_agent_rejects_ungranted_key(self, tmp_path: Path) -> None:
        with FbpDriver() as driver:
            driver.spawn("store", kind="store")
            driver.configure_child(
                "store",
                state=str(tmp_path / "store.db"),
                store_keys=("bill-b3",),
            )
            # bill-b9 is not on the grant allowlist -> fail closed.
            resp = driver.run(
                "store_set",
                {"key": "bill-b9", "value": {"status": "open"}},
                child="store",
            )
            assert resp.verified is False
            assert "not granted" in (resp.error or "")

    def test_store_agent_is_durable_across_reopen(self, tmp_path: Path) -> None:
        state_path = tmp_path / "store.db"
        with FbpDriver() as driver:
            driver.spawn("store", kind="store")
            driver.configure_child(
                "store", state=str(state_path), store_keys=("bill-b3",)
            )
            driver.run(
                "store_set",
                {"key": "bill-b3", "value": {"amount_cents": 12345}},
                child="store",
            )
        # Durable: reopen the StoreAgent's file directly.
        from agent_centric.fbp import store

        st = store.open_state(state_path)
        assert st.get("bill-b3")["amount_cents"] == 12345
        st.close()


class TestCpmCapability:
    """CPM is a read-only, deterministic *capability* (a registered callable),
    not an agent: it is a pure observation, not a unit of work with
    responsibility. It is reached as a local run task, not via delegation."""

    def test_cpm_capability_returns_analysis(self) -> None:
        from agent_centric.fbp.critical_path import cpm_from_dict

        with FbpDriver() as driver:
            driver.register("cpm", lambda nodes: cpm_from_dict(nodes).to_dict())
            driver.configure(tasks=("cpm",))
            resp = driver.run(
                "cpm",
                {
                    "nodes": [
                        {"id": "a", "duration": 3},
                        {"id": "b", "duration": 2, "depends_on": ["a"]},
                        {"id": "c", "duration": 1, "depends_on": ["a"]},
                        {"id": "d", "duration": 2, "depends_on": ["b", "c"]},
                    ]
                },
            )
            assert resp.verified is True
            assert resp.value["duration"] == 7
            assert set(resp.value["critical_path"]) == {"a", "b", "d"}
            assert resp.value["slack"]["c"] == 1

    def test_cpm_capability_fails_closed_on_cycle(self) -> None:
        from agent_centric.fbp.critical_path import cpm_from_dict

        with FbpDriver() as driver:
            driver.register("cpm", lambda nodes: cpm_from_dict(nodes).to_dict())
            driver.configure(tasks=("cpm",))
            resp = driver.run(
                "cpm",
                {
                    "nodes": [
                        {"id": "a", "duration": 1, "depends_on": ["b"]},
                        {"id": "b", "duration": 1, "depends_on": ["a"]},
                    ]
                },
            )
            assert resp.verified is False
            assert "cycle" in (resp.error or "")

    def test_cpm_capability_is_read_only(self) -> None:
        from agent_centric.fbp.critical_path import cpm_from_dict

        # A pure capability needs no state grant and never writes anything.
        with FbpDriver() as driver:
            driver.register("cpm", lambda nodes: cpm_from_dict(nodes).to_dict())
            driver.configure(tasks=("cpm",))
            resp = driver.run("cpm", {"nodes": [{"id": "x", "duration": 2}]})
            assert resp.verified is True
            assert resp.value["duration"] == 2


class TestBillsLoop:
    """The bills loop end-to-end on the foundation: intake -> human-gated
    accept -> durable single-writer registry -> verified calendar projection.
    Topology: root -> bills -> store. Nothing auto-accepts; money stays
    integer cents and dates ISO; malformed intake fails closed."""

    def _setup(self, driver: Any, tmp_path: Path) -> None:
        driver.spawn("bills", kind="bills")
        driver.run(
            "bills_setup",
            {"state": str(tmp_path / "registry.db"), "store_keys": ["b1", "b2"]},
            child="bills",
        )

    def test_full_loop(self, tmp_path: Path) -> None:
        from agent_centric.fbp.bills_agent import (
            TASK_ACCEPT,
            TASK_CALENDAR,
            TASK_INTAKE,
        )

        with FbpDriver() as driver:
            self._setup(driver, tmp_path)

            # 1. Intake an unverified draft.
            draft = driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b1",
                        "vendor": "GasCo",
                        "amount_cents": 12345,
                        "due_date": "2026-10-01",
                    }
                },
                child="bills",
            )
            assert draft.verified is True
            assert draft.value["id"] == "b1"
            # An intake draft is unverified: it has no registry 'status' yet.
            assert "status" not in draft.value

            # 2. Human-gated accept -> persisted to the registry via the store.
            accepted = driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            assert accepted.verified is True

            # 3. Calendar projection from the durable registry (b1 due 2026-10-01).
            cal = driver.run(
                TASK_CALENDAR,
                {"from_date": "2026-10-01", "to_date": "2026-10-31"},
                child="bills",
            )
            assert cal.verified is True
            assert [e["id"] for e in cal.value["entries"]] == ["b1"]
            assert cal.value["total_cents"] == 12345

            # 4. The registry is durable on disk.
            from agent_centric.fbp import store

            st = store.open_state(tmp_path / "registry.db")
            assert st.get("b1")["status"] == "open"
            assert st.get("b1")["amount_cents"] == 12345
            st.close()

    def test_no_auto_accept(self, tmp_path: Path) -> None:
        """Intake alone never writes the registry; only accept does."""
        from agent_centric.fbp import store
        from agent_centric.fbp.bills_agent import TASK_INTAKE

        with FbpDriver() as driver:
            self._setup(driver, tmp_path)
            driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b2",
                        "vendor": "PostCo",
                        "amount_cents": 999,
                        "due_date": "2026-09-15",
                    }
                },
                child="bills",
            )
        st = store.open_state(tmp_path / "registry.db")
        assert st.get("b2") is None, "intake alone must not write the registry"
        st.close()

    def test_malformed_intake_fails_closed(self, tmp_path: Path) -> None:
        from agent_centric.fbp.bills_agent import TASK_INTAKE

        with FbpDriver() as driver:
            self._setup(driver, tmp_path)
            resp = driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b3",
                        "vendor": "X",
                        "amount_cents": "NaN",
                        "due_date": "not-a-date",
                    }
                },
                child="bills",
            )
            assert resp.verified is False
            assert resp.error is not None


class TestAuditReconstruction:
    """The driver reconstructs the full audit chain per correlation id across
    the tree (audit as proof), including delegated parent-child relay hops."""

    def test_reconstructs_local_chain(self, tmp_path: Path) -> None:
        with FbpDriver() as driver:
            driver.configure(trajectory=str(tmp_path / "root.db"))
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            r = driver.run("double", {"value": 21})
            assert r.verified is True

            chains = driver.reconstruct_audit()
            # The run chain (kind result, value 42) is recovered.
            run_chains = [
                c for c in chains if c["terminal"] == "result" and c["terminal_value"] == 42
            ]
            assert run_chains, "should reconstruct the verified run chain"

    def test_reconstructs_delegated_parent_child_chain(self, tmp_path: Path) -> None:
        with FbpDriver() as driver:
            driver.configure(trajectory=str(tmp_path / "root_traj.db"))
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.spawn("child")
            driver.configure_child(
                "child", tasks=("double",), trajectory=str(tmp_path / "child_traj.db")
            )
            r = driver.run("double", {"value": 21}, child="child")
            assert r.verified is True

            chains = driver.reconstruct_audit()
            # Find the chain that ended in the child's verified 42 result.
            hit = None
            for c in chains:
                if c["terminal_value"] == 42 and c["verified"]:
                    hit = c
                    break
            assert hit is not None, "should reconstruct the delegated chain"
            nodes = [(e["node"], e["kind"]) for e in hit["events"]]
            # child produced the result; root recorded the relay hop.
            assert ("child", "result") in nodes and ("root", "relay") in nodes


class TestLifecycle:
    def test_ping(self) -> None:
        with FbpDriver() as driver:
            resp = driver.ping()
            assert resp.verified is True
            assert resp.kind == "ok"

    def test_children_view(self) -> None:
        with FbpDriver() as driver:
            driver.spawn("child")
            assert "child" in driver._root.children


class TestDurableStateAndAudit:
    """The driver exposes durable state and local audit over the wire:
    state is persisted idempotently and read back; the audit records the
    agent's local activity as the start of chain audit."""

    def test_state_set_and_get(self, tmp_path: Path) -> None:
        state_path = tmp_path / "state.db"
        with FbpDriver() as driver:
            driver.configure(state=str(state_path))
            ok = driver.state_set("b3", {"status": "paid"})
            assert ok.verified is True
            got = driver.state_get("b3")
            assert got.verified is True
            assert got.value["status"] == "paid"
        # Durable: re-open and read the same state.
        from agent_centric.fbp import store

        st = store.open_state(state_path)
        assert st.get("b3")["status"] == "paid"
        st.close()

    def test_state_get_ungranted_fails_closed(self) -> None:
        with FbpDriver() as driver:
            resp = driver.state_get("b3")
            assert resp.verified is False
            assert resp.error is not None

    def test_audit_records_local_activity(self, tmp_path: Path) -> None:
        traj_path = tmp_path / "traj.db"
        with FbpDriver() as driver:
            driver.configure(trajectory=str(traj_path))
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.run("double", {"value": 21})
            audit = driver.audit()
            assert audit.verified is True
            # The run's verified result is recorded locally — the chain's start.
            expected = ("result", 42)
            assert any(
                (row["kind"], row["value"]) == expected for row in audit.value
            )

    def test_parent_records_relay_hop_for_delegated_child(self, tmp_path: Path) -> None:
        """Chain audit is reconstructible end-to-end: a parent records the
        child-response it accepted (a ``relay`` hop), sharing the correlation
        id, so an operator can follow child "result" + parent "relay"."""
        traj_path = tmp_path / "traj.db"
        with FbpDriver() as driver:
            driver.configure(trajectory=str(traj_path))
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            driver.spawn("child")
            driver.configure_child("child", tasks=("double",))
            delegated = driver.run("double", {"value": 21}, child="child")
            assert delegated.verified is True
            assert delegated.value == 42

            # The parent's local audit includes a relay hop naming the child.
            audit = driver.audit()
            assert audit.verified is True
            relays = [row for row in audit.value if row["kind"] == "relay"]
            assert relays, "parent should record a relay hop for the delegated child"
            assert relays[0]["node"] == "root"
            assert relays[0]["parent"] == "child"
            assert relays[0]["value"] == 42


class TestTransportParity:
    """The driver (and its tree) must run the whole directive/response flow
    identically over ``tcp`` and ``ipc``, not just ``inproc``. This is the
    easy-UX layer proving transport parity end-to-end, including delegate."""

    @pytest.mark.parametrize(
        ("transport", "endpoint"),
        [("tcp", "127.0.0.1:5599"), ("ipc", "/tmp/agent-centric-fbp-driver-test")],
    )
    def test_full_flow_over_transport(self, transport: str, endpoint: str) -> None:
        with FbpDriver(transport=transport, endpoint=endpoint) as driver:
            driver.register("double", _double)
            driver.register("even", _even)
            driver.configure(tasks=("double",), verifiers=("even",))

            local = driver.run("double", {"value": 21})
            assert local.verified is True
            assert local.value == 42

            driver.spawn("child")
            driver.configure_child("child", tasks=("double",))
            delegated = driver.run("double", {"value": 21}, child="child")
            assert delegated.verified is True
            assert delegated.value == 42
            assert delegated.node == "child"

    @pytest.mark.parametrize(
        ("transport", "endpoint"),
        [
            ("tcp", "127.0.0.1:5599"),
            ("ipc", "/tmp/agent-centric-fbp-driver-bills-test"),
        ],
    )
    def test_nested_bills_loop_over_transport(
        self, transport: str, endpoint: str, tmp_path: Path
    ) -> None:
        """The bills loop — which spawns its own store child internally — must
        run identically over every transport. This is the regression that broke
        ``tcp``: the store child's endpoint must be transport-resolved, not a
        bare ``tcp://<name>`` address."""
        from agent_centric.fbp.bills_agent import (
            TASK_ACCEPT,
            TASK_CALENDAR,
            TASK_INTAKE,
        )

        with FbpDriver(transport=transport, endpoint=endpoint) as driver:
            driver.spawn("bills", kind="bills")
            driver.run(
                "bills_setup",
                {"state": str(tmp_path / "registry.db"), "store_keys": ["b1"]},
                child="bills",
            )
            draft = driver.run(
                TASK_INTAKE,
                {
                    "draft": {
                        "id": "b1",
                        "vendor": "GasCo",
                        "amount_cents": 12345,
                        "due_date": "2026-10-01",
                    }
                },
                child="bills",
            )
            assert draft.verified is True
            accepted = driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            assert accepted.verified is True
            cal = driver.run(
                TASK_CALENDAR,
                {"from_date": "2026-10-01", "to_date": "2026-10-31"},
                child="bills",
            )
            assert cal.verified is True
            assert [e["id"] for e in cal.value["entries"]] == ["b1"]
            assert cal.value["total_cents"] == 12345