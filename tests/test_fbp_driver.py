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