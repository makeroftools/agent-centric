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
| `state_get(key)` | `state_get` | read from the agent's durable state store |
| `state_set(key, value)` | `state_set` | idempotently write to the durable state store |
| `audit()` | `audit` | return the agent's local audit record |
| `ping()` | `ping` | liveness |
| `kill()` | `kill` | teardown |

### Durable state + audit (on-demand, separate files)

An agent may opt into persistence via `configure`, each as its own SQLite file,
created on demand (an explicit grant — never a silent write):

```python
with FbpDriver() as d:
    d.configure(
        state="registry.state.db",      # mutable, single-writer key/value
        trajectory="agent.audit.db",   # append-only, write-once local audit
    )
    d.state_set("bill-b3", {"status": "paid"})
    got = d.state_get("bill-b3")
    audit = d.audit()                  # the local start of chain audit
```

- **State** is a **mutable, authoritative, single-writer** key/value store the
  agent owns (a resource). Writes are **idempotent, keyed by the directive
  fingerprint** — replaying a directive reapplies the same row; a distinct
  directive is a real update. **No auto-generated keys** (keys arrive in the
  directive), so replay rebuilds identical content.
- **Trajectory/audit** is **append-only and write-once**, keyed by the
  correlation id. It is the **local start of chain audit**: each agent records
  its own activity (configure, every run outcome, every state op).
- **Chain audit starts locally and is completed by each parent.** When a parent
  accepts a delegated child's verified response, it records a **`relay` hop**
  in its own audit naming the child — so an operator can reconstruct the full
  parent→child chain (child's `result` + parent's `relay` share the
  correlation id).
- **Read-only grants** close the write path (a store-opened read-only
  refuses writes fail-closed).

Both state and trajectory can be controlled by a domain-specific agent (they're
just directives over the protocol), so a store/registry agent can own a
resource and serve it to others under grant.

### Store/registry agent (single-writer resource)

`StoreAgent` (`store_agent.py`) is the concrete case: a domain agent that
**owns** a `StateStore` and serves it to others over `run` operations
(`STORE_SET`/`STORE_GET`) through the parent's mediated delegation. Only it
writes its store; others reach it instead of the file directly, so there is no
ungoverned concurrent access. Grants arrive via `configure`:

```python
with FbpDriver() as d:
    d.spawn("store", kind="store")               # a real StoreAgent child
    d.configure_child(
        "store",
        state="registry.db",
        store_keys=("bill-b3", "bill-b4"),   # key allowlist (hard grant)
    )
    d.run("store_set", {"key": "bill-b3", "value": {"status": "paid"}}, child="store")
    got = d.run("store_get", {"key": "bill-b3"}, child="store")
```

- **Single-writer**: only the store agent writes its state store.
- **Grant via key allowlist**: a key not in `store_keys` fails closed (the
  store does not serve arbitrary keys).
- **Idempotent + audited**: writes are fingerprint-idempotent; every served
  operation is recorded in the store agent's local audit, and the parent
  re-verifies each relayed response.

### CPM agent (read-only critical-path service)

`CpmAgent` (`cpm_agent.py`) makes Critical Path Method a first-class,
deterministic, **read-only** service over the directive bus. It takes a network
of activities (ids, durations, dependencies) and returns the critical path,
per-node slack, and the minimum project duration — via a classic forward/backward
pass. It never mutates anything and needs no state grant:

```python
with FbpDriver() as d:
    d.spawn("cpm", kind="cpm")
    r = d.run("cpm", {"nodes": [
        {"id": "a", "duration": 3},
        {"id": "b", "duration": 2, "depends_on": ["a"]},
        {"id": "c", "duration": 1, "depends_on": ["a"]},
        {"id": "d", "duration": 2, "depends_on": ["b", "c"]},
    ]}, child="cpm")
    # r.value["duration"] == 7; critical_path == ["a", "b", "d"]; slack["c"] == 1
```

- **Deterministic**: identical input → identical output (stable topological
  order, sorted tie-breaks).
- **Read-only**: a pure function over the network; never writes a store.
- **Fail-closed**: a cyclic, self-referential, or malformed network is an
  explicit error, never an ambiguous result.

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