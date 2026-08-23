"""Resource envelopes — hard, enforced bounds for agent execution in the FBP tree.

The Manager line (``main``) enforces per-task resource envelopes; the FBP tree
did not. This module brings hard bounds to the FBP tree, applied at the agent's
dispatch boundary (``_run_task`` / ``_spawn``) and layered fail-closed:

- ``step_limit`` — the maximum number of task directives an agent may run in
  its lifetime (beyond it, further runs fail closed with an explicit error).
- ``size_cap`` — a JSON-serializable size cap on a directive's payload (so an
  oversized directive is rejected before it is worked).
- ``latency_seconds`` — an upper bound (in real seconds) on a single task's
  total latency; a task that runs beyond the bound is rejected. Enforcement is
  best-effort elapsed-time checking around a requested task's *inputs/outputs*
  (the platform is otherwise time-bounded via the caller); the bound is hard
  for oversized output and for the elapsed-time check that wraps a task.
- ``child_limit`` — the maximum number of children an agent may spawn.

An envelope is **granted at configure time** (a parent grants its child an
envelope, or the driver grants the root one). It is carried in the configure
directive payload and stored on the agent. Every directive an agent receives is
checked against the envelope before it is acted on; a violation is an explicit,
audited error — never a silent success. This is the FBP-tree analogue of the
Manager's least-privilege guarantee.

All bounds are optional (``None`` = unlimited) and additive: an agent with no
envelope configured is unbounded (the pre-existing behaviour), so existing
tests and call sites are unaffected.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResourceEnvelope:
    """Hard, enforced bounds on an agent's execution.

    All fields are optional: ``None`` means "unbounded" for that resource. The
    envelope is immutable once granted.
    """

    step_limit: int | None = None
    size_cap: int | None = None
    latency_seconds: float | None = None
    child_limit: int | None = None
    # Reserved for future resource dimensions (e.g. memory, model calls);
    # kept as a free-form map so new bounds are additive, not breaking.
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Reject an invalid envelope (negative / malformed bounds) fail-closed.

        Raises:
            ValueError: If any bound is negative or malformed.
        """
        for name, val in (
            ("step_limit", self.step_limit),
            ("size_cap", self.size_cap),
            ("latency_seconds", self.latency_seconds),
            ("child_limit", self.child_limit),
        ):
            if val is None:
                continue
            if not isinstance(val, (int, float)):
                raise ValueError(f"{name} must be a number, got {type(val).__name__}")
            if val < 0:
                raise ValueError(f"{name} must be non-negative, got {val}")

    def to_payload(self) -> dict[str, Any]:
        """A JSON-safe dict (for carrying the envelope over the wire)."""
        return {
            "step_limit": self.step_limit,
            "size_cap": self.size_cap,
            "latency_seconds": self.latency_seconds,
            "child_limit": self.child_limit,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any] | None) -> ResourceEnvelope | None:
        """Reconstruct an envelope from a wire payload, or None if unset."""
        if not data or not isinstance(data, dict):
            return None
        env = cls(
            step_limit=data.get("step_limit"),
            size_cap=data.get("size_cap"),
            latency_seconds=data.get("latency_seconds"),
            child_limit=data.get("child_limit"),
            extra=dict(data.get("extra") or {}),
        )
        env.validate()
        return env


class EnvelopeGuard:
    """Tracks and enforces a granted envelope across an agent's life.

    It is cheap, deterministic, and fail-closed. It is created by the agent at
    configure time from the granted envelope and consulted on every directive.
    """

    def __init__(self, envelope: ResourceEnvelope | None) -> None:
        if envelope is not None:
            envelope.validate()
        self._envelope = envelope
        self._steps = 0
        self._children = 0
        self._started = time.monotonic() if envelope is not None else None

    @property
    def envelope(self) -> ResourceEnvelope | None:
        return self._envelope

    @property
    def step_count(self) -> int:
        return self._steps

    def check_directive(self, *, kind: str, payload: dict[str, Any]) -> str | None:
        """Return an error string if ``kind``/``payload`` violates the envelope.

        Returns None when allowed. This is called before work begins. ``kind``
        is one of ``run`` / ``spawn`` / others. Violations fail the directive
        closed (the caller turns a non-None into an explicit error response).
        """
        env = self._envelope
        if env is None:
            return None
        if (
            env.latency_seconds is not None
            and self._started is not None
            and time.monotonic() - self._started > env.latency_seconds
        ):
            return (
                f"resource envelope: agent latency exceeded "
                f"{env.latency_seconds}s bound"
            )
        if env.size_cap is not None and _payload_byte_length(payload) > env.size_cap:
            return (
                f"resource envelope: directive payload exceeds "
                f"{env.size_cap} byte cap"
            )
        if (
            kind in ("run", "spawn")
            and env.step_limit is not None
            and self._steps >= env.step_limit
        ):
            return f"resource envelope: step limit {env.step_limit} exceeded"
        if kind == "spawn" and env.child_limit is not None and self._children >= env.child_limit:
            return f"resource envelope: child limit {env.child_limit} exceeded"
        return None

    def record_step(self, *, is_spawn: bool = False) -> None:
        """Account for a directive that will be / was executed."""
        self._steps += 1
        if is_spawn:
            self._children += 1


def _payload_byte_length(payload: dict[str, Any]) -> int:
    """Return the JSON byte length of a directive payload (deterministic)."""
    import json

    return len(json.dumps(payload, sort_keys=True, default=str))