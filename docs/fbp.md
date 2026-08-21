# Agent-centric FBP subsystem — easy-UX driver

This is the **user-facing layer** for the agent-centric, flow-based-programming
(FBP) architecture on the `agent-centric-fbp` branch. It sits on top of the
raw directive/response protocol (`fbp.agent`, `fbp.message`) and turns it into
a plain, synchronous, deterministic API — no sockets, no frames, no event loop.

The deep architectural spec lives in [`spec.md`](../src/agent_centric/fbp/spec.md)
and the wire contract in [`protocol.md`](../src/agent_centric/fbp/protocol.md).
This document is the "how do I drive it" companion.

## The model in one line

A rooted, recursive **tree of agents**. Work travels **down**; responses and
responsibility bubble **up**, each parent **verifying a child's response**
before accepting it. A task terminates in a **verified result or an explicit,
audited failure** — never a silent third state.

Practical consequences, all enforced:

- **registry-as-agent** — capabilities are registered/resolved as directives;
  the registry is a passive catalog of *locations*, never code.
- **parent provides context** — a parent configures its child's task allowlist
  and verifier (`configure_child`).
- **correctness spine** — a parent re-verifies a child's value on the way up;
  a child's own `verified` claim is never trusted.
- **fail-closed** — a malformed directive or an unknown delegation target is an
  explicit error, never a silent route.

## Usage

`FbpDriver` is the easy-UX entry point. It manages a root agent and every
channel; you call methods and get `Response` objects.

```python
from agent_centric.fbp import FbpDriver


def double(x: int) -> int:            # a task callable
    return x * 2

def even(v) -> bool:                  # a verifier (predicate)
    return isinstance(v, int) and v % 2 == 0

with FbpDriver() as driver:          # inproc, offline, deterministic
    driver.register("double", double, source_url="file:///tasks/double")
    driver.register("even", even)

    driver.configure(tasks=("double",), verifiers=("even",), verifier="even")

    local = driver.run("double", {"value": 21})
    # local.verified True, local.value 42

    driver.spawn("child")            # provision a real child Agent
    driver.configure_child("child", tasks=("double",))

    delegated = driver.run("double", {"value": 21}, child="child")
    # routed down to the child, re-verified up; .node == "child"
```

### Method map

| Method | Directive | Meaning |
|--------|-----------|---------|
| `register(name, fn, source_url=)` | — | make a callable directive-resolution-safe |
| `resolve(name)` | `resolve` | return a capability's recorded location |
| `configure(...)` | `configure` | set the root's rules, task allowlist, verifier |
| `configure_child(id, ...)` | — | parent provides a spawned child's context |
| `run(task, args, child=)` | `run` | execute, or delegate to a named child |
| `spawn(id, endpoint=)` | `spawn` | provision a real child agent |
| `ping()` | `ping` | liveness |
| `kill()` | `kill` | teardown |

### Transports

`FbpDriver(transport=...)` runs the exact same protocol over:

- `inproc://` — in-process, offline, deterministic (the default).
- `tcp://` — real distribution across processes/machines.
- `ipc://` — local inter-process.

The driver hides transport asynchrony: over `tcp`/`ipc` it retries a directive
until the ack/response returns (the retry is idempotency-safe — a replayed
directive returns the cached result rather than re-executing).

## CLI

`agent-centric fbp [--transport inproc|tcp|ipc]` drives a deterministic demo
tree that exercises every property above and exits non-zero on any failure.

## Guarantees

- **No unverified success.** `Response.verified` is True only if the value
  passed every verifier on the way up.
- **Fail-closed.** Bad messages and unknown delegation targets become explicit
  errors; the poll loop never crashes.
- **Idempotent.** Replaying a directive returns the cached result (keyed by the
  full directive fingerprint) rather than re-executing.
- **Deterministic.** Same directives, same tree, same context → same results,
  regardless of transport.