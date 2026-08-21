"""End-to-end demo of the Agent-centric FBP subsystem via the high-level driver.

The ``FbpDriver`` is the *easy UX*: a synchronous API over the directive/
response protocol. It hides the ZeroMQ transport so you can build and drive a
tree of agents in a few lines, without touching sockets or an event loop.

This shows the core of the agent-centric model:
- registry-as-agent (register / resolve a passive capability catalog),
- configure (parent provides the child's context: task allowlist, verifier),
- run a verified task locally,
- spawn a real child agent and delegate a run down to it — the parent
  re-verifies the child's response on the way up (the correctness spine),
- fail-closed: a run through an unknown delegation target is an explicit
  failure, never a silent drop or a wrong route.

No network, no daemons — everything runs over ``inproc://``.
"""

from __future__ import annotations

from typing import Any

from agent_centric.fbp import FbpDriver


def double(value: int) -> int:
    """A domain task: double a number."""
    return value * 2


def even(value: Any) -> bool:
    """A verifier: only even results are verified correct."""
    return isinstance(value, int) and value % 2 == 0


def odd(value: Any) -> bool:
    """A verifier: only odd results are verified correct."""
    return isinstance(value, int) and value % 2 == 1


def show(label: str, resp) -> None:
    """Pretty-print a driver response."""
    if resp.verified:
        print(f"{label}: VERIFIED value={resp.value!r} node={resp.node!r} "
              f"source={resp.source!r}")
    else:
        print(f"{label}: FAILED  error={resp.error!r}")


def main() -> None:
    with FbpDriver() as driver:
        # 1. Registry-as-agent: register capabilities (passive catalog).
        driver.register("double", double, source_url="file:///tasks/double")
        driver.register("even", even)
        driver.register("odd", odd)
        resolved = driver.resolve("double")
        print(f"registry resolves 'double' -> {resolved.value}")

        # 2. Configure the root agent: task allowlist + verifier.
        driver.configure(tasks=("double",), verifiers=("even", "odd"))

        # 3. Run a task locally; the even-verifier verifies the result.
        show("double(21) locally", driver.run("double", {"value": 21}))

        # 4. Spawn a real child agent and delegate work down to it. The parent
        #    re-verifies the child's value on the way up with ITS own verifier.
        driver.spawn("child")
        driver.configure_child("child", tasks=("double",))

        # The child returns 42 (even) — the root's even-verifier accepts it.
        show("double(21) via child (even)", driver.run("double", {"value": 21}, child="child"))

        # 5. Correctness spine: switch the root's verifier to odd-only; the
        #    child's even result is now demoted to an explicit failure.
        driver.configure(verifier="odd")
        show("double(21) via child (odd)", driver.run("double", {"value": 21}, child="child"))

        # 6. Fail closed: delegating to an unknown child is an explicit error,
        #    not a silent wrong-route.
        driver.configure(verifier="even")
        show("delegate to ghost", driver.run("double", {"value": 21}, child="ghost"))


if __name__ == "__main__":
    main()