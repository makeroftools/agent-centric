"""Tests for hard resource envelopes in the FBP tree (``fbp/envelopes.py``).

These prove the envelope is a *hard, enforced* bound, not advisory:

- the pure envelope/guard model (payload, step limit, size cap),
- enforcement at the agent's ``_run_task`` boundary (step limit),
- enforcement at the agent's ``_spawn`` boundary (child limit),
- size-cap enforcement on an oversized directive payload,
- backward compatibility: no envelope => unbounded (existing behaviour).

Offline and deterministic — no network, no daemons.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_centric.fbp import FbpDriver, register_callable
from agent_centric.fbp.envelopes import EnvelopeGuard, ResourceEnvelope


def _double(value: int) -> int:
    return value * 2


def _stub(_: Any) -> int:
    return 1


def _seed() -> None:
    register_callable("double_env", _double)
    register_callable("stub_env", _stub)


class TestResourceEnvelopeModel:
    def test_roundtrip_payload(self) -> None:
        env = ResourceEnvelope(step_limit=3, size_cap=100, latency_seconds=5.0, child_limit=2)
        back = ResourceEnvelope.from_payload(env.to_payload())
        assert back is not None
        assert back.step_limit == 3
        assert back.size_cap == 100
        assert back.latency_seconds == 5.0
        assert back.child_limit == 2

    def test_from_payload_none(self) -> None:
        assert ResourceEnvelope.from_payload(None) is None
        assert ResourceEnvelope.from_payload({}) is None

    def test_validate_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            ResourceEnvelope(step_limit=-1).validate()

    def test_validate_rejects_non_number(self) -> None:
        with pytest.raises(ValueError):
            ResourceEnvelope(size_cap="big").validate()  # type: ignore[arg-type]


class TestEnvelopeGuard:
    def test_no_envelope_is_unbounded(self) -> None:
        guard = EnvelopeGuard(None)
        assert guard.check_directive(kind="run", payload={"task": "x"}) is None
        guard.record_step()
        guard.record_step()
        assert guard.check_directive(kind="run", payload={"task": "x"}) is None

    def test_step_limit_enforced(self) -> None:
        guard = EnvelopeGuard(ResourceEnvelope(step_limit=2))
        assert guard.check_directive(kind="run", payload={"task": "x"}) is None
        guard.record_step()
        guard.record_step()
        assert "step limit" in (guard.check_directive(kind="run", payload={"task": "x"}) or "")

    def test_size_cap_enforced(self) -> None:
        guard = EnvelopeGuard(ResourceEnvelope(size_cap=10))
        err = guard.check_directive(
            kind="run", payload={"task": "x", "args": {"big": "y" * 100}}
        )
        assert err is not None

    def test_child_limit_enforced(self) -> None:
        guard = EnvelopeGuard(ResourceEnvelope(child_limit=1))
        assert guard.check_directive(kind="spawn", payload={}) is None
        guard.record_step(is_spawn=True)
        assert "child limit" in (guard.check_directive(kind="spawn", payload={}) or "")


class TestEnvelopeEnforcement:
    """End-to-end through the driver: the envelope actually bounds work."""

    def setup_method(self) -> None:
        _seed()

    def test_step_limit_blocks_runs_fail_closed(self) -> None:
        with FbpDriver() as driver:
            driver.register("double_env", _double)
            driver.configure(tasks=("double_env",), envelope=ResourceEnvelope(step_limit=2))
            assert driver.run("double_env", {"value": 2}).verified is True
            assert driver.run("double_env", {"value": 3}).verified is True
            third = driver.run("double_env", {"value": 4})
            assert third.verified is False
            assert third.error is not None and "step limit" in third.error

    def test_size_cap_rejects_oversized_directive(self) -> None:
        with FbpDriver() as driver:
            driver.register("double_env", _double)
            driver.configure(tasks=("double_env",), envelope=ResourceEnvelope(size_cap=200))
            # A small directive works.
            small = driver.run("double_env", {"value": 5})
            assert small.verified is True
            # An oversized args payload is rejected fail-closed.
            big = driver.run("double_env", {"value": 5, "blob": "x" * 10_000})
            assert big.verified is False
            assert big.error is not None and "exceeds" in big.error

    def test_no_envelope_is_unbounded(self) -> None:
        with FbpDriver() as driver:
            driver.register("double_env", _double)
            driver.configure(tasks=("double_env",))
            for i in range(5):
                assert driver.run("double_env", {"value": i}).verified is True

    def test_child_limit_blocks_spawn_fail_closed(self) -> None:
        with FbpDriver() as driver:
            driver.configure(envelope=ResourceEnvelope(child_limit=1))
            assert driver.spawn("a").verified is True
            second = driver.spawn("b")
            assert second.verified is False
            assert second.error is not None and "child limit" in second.error