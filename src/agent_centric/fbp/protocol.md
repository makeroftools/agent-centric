# Agent-Centric Directive/Response Protocol — Spec (draft)

**Status:** Contract for the `agent-centric-fbp` branch.
**Authority:** Lead Architect (via this session).
**Classification:** Mission-Critical.

This document is the **crux of the system**: the standard "language" that every
agent speaks. It is a versioned, enforced contract. Messages that do not conform
are rejected — never silently accepted.

This is an **abstract, general-purpose agent system** — not a trading system.
Trading / automated-trading concerns are **non-relevant** to this project.

## 1. Transport

The comms channel is **ZeroMQ** (`zmq_poll`), a first-class citizen. The same
protocol runs over any transport:

- `inproc://` — in-process, offline-testable, deterministic (tests, foundation).
- `tcp://` — real distribution across processes/machines.
- `ipc://` — local inter-process.

The transport is a property of the channel, not of the protocol. The protocol
is identical on every transport.

## 2. The envelope

A message is a sequence of frames on a DEALER/ROUTER socket. On a ROUTER, ZeroMQ
prepends the sender's routing identity as the first frame.

```
[identity]   [correlation_id]   [kind]   [payload]
```

- `identity` — the sender's routing identity (added by ROUTER; absent on DEALER).
- `correlation_id` — its own frame, echoed in every ack/response. This is what
  makes async matching deterministic and idempotency checkable.
- `kind` — `directive` | `ack` | `response`.
- `payload` — JSON. A directive carries the complete task specification; a
  response carries the outcome (value, verified, node, error, and source — the
  source location of the callable that produced it, for chain audit).

JSON payload is deliberate: it forces a uniform wire contract and makes
persistence and replay work.

## 2a. The three message kinds

There are three kinds of message, mirroring the classic transport distinction
between delivery and completion:

- **Directive** — a complete, self-contained unit of work sent down the tree.
- **Ack** — a dumb acknowledgment meaning "message received", sent back to the
  originator immediately upon receipt, before any work begins (like a TCP ACK).
  It carries no result and implies nothing about completion.
- **Response** — the outcome of a directive, sent when the directive is
  *completed*.

This separation matters: an Ack confirms delivery; a Response confirms
completion. A sender can distinguish "my directive was received" from "my
directive finished", which is essential for reliable, deterministic
asynchronous operation.

## 3. Directive kinds

A directive is a complete, self-contained specification. The agent is a pure
function of (directive, context): the same directive, same context → same
outcome, regardless of arrival order.

| kind | purpose |
|------|---------|
| `configure` | Parent configures the agent's domain, rules, verifier, and protocol version. |
| `run` | Execute a task (the core directive). Carries the full context. |
| `spawn` | Spawn a child agent, as prescribed by the directive. |
| `ping` | Liveness / health check. |
| `kill` | Teardown. |

## 4. Response kinds

| kind | meaning |
|------|---------|
| `ok` | Completed; no value. |
| `result` | A **verified** value. |
| `error` | An explicit, audited failure. Never a verified success. |
| `telemetry` | Observation / audit event. |

## 5. The correctness spine

- Work + context flow **down**.
- Responses + responsibility flow **up**, and each parent verifies a child's
  response before accepting responsibility for it.
- A response that fails verification is an explicit, audited failure — never a
  verified success.
- **No third state.** A task terminates in a verified result or an explicit,
  audited failure.

## 6. Idempotency

Processing the same directive twice yields the same result. A directive is
identified by its **full fingerprint** — correlation id + kind + canonical
payload — not by correlation id alone. A replayed directive (same fingerprint)
returns the cached result instead of re-executing, so retry and replay are safe.

**Side-effect safety.** Stateful directives are idempotent too: a replayed
``spawn`` reuses an already-provisioned child instead of re-binding the endpoint
or creating a duplicate. A correlation id that is reused for *different* work is
a protocol violation and **fails closed** (an explicit error, never a stale
result), so the system cannot silently serve an old outcome for new work.

## 7. Trust

Trust is clamped down. The parent is trusted by construction (it provides the
child's context), but *which* callable ran is always recorded, so the trajectory
is fully auditable. A child cannot name an arbitrary callable; it runs only what
its context grants.

**Chain audit.** Every response carries a ``source`` field — the source location
(URL) of the callable that produced it. This is the execution clamp: the
registry is a passive catalog, so the power to compile/run lives in the
consuming agent, and the trajectory records *which* callable ran and from where
(source URL → fetch → compile → run). A response with an empty ``source`` means
the callable was registered without a source location; the field is always
present on the wire so the audit is uniform.

**Consumption clamp.** An agent runs only what its parent explicitly granted it
via ``configure`` (the ``tasks``/``verifiers`` names). A directive cannot name an
arbitrary callable; it must name one the agent was configured with. This is the
allowlist/grant that bounds who may act on a recorded location.

## 8. Telemetry

Every directive, response, verification, determination, and spawn is emitted to
a telemetry channel. Telemetry is for *seeing* the system live; the trajectory
is the durable, replayable record of *what it did*. Both are first-class.

## 9. The fractal principle: every task is an agent

The architecture is **fractal and recursive**. There is one abstract concept —
the Agent — and everything is an instance of it. A task is not a special kind
of thing: **every task is itself an agent**, which may in turn delegate to
further agents, which may delegate further still. This recursion extends, in
principle, all the way down to individual instructions.

This is what "centricity" means: each agent is the center of its own little
universe, simultaneously a worker to its parent and a manager to its children.
The same contract holds at every level — there is no privileged "task" type
that is exempt from being an agent.

The practical consequence: a directive that names a task is really naming an
agent (or a callable that behaves as one). The system does not distinguish
"running a task" from "invoking an agent" — they are the same operation at
different scales. This keeps the model uniform, deterministic, and auditable
at every level of granularity.

## 10. Critical Path Method (CPM)

**Critical Path Method (CPM)** is a fundamental, first-class tool of the
architecture. It identifies the longest dependency chain through a graph of
work (the **critical path**) and the **slack/float** of every other element —
the amount by which an element can be delayed without delaying the whole.

Key concepts (from the classic CPM/PDM formulation):

- **Forward pass** — computes early start/finish of every activity.
- **Backward pass** — computes late start/finish of every activity.
- **Slack / float** — the difference between late and early times; an activity
  with zero slack lies on the critical path.
- **Critical path** — the chain of zero-slack activities that determines the
  minimum feasible duration of the whole.

In this architecture, CPM is a **deterministic, read-only observational aid**:

- It is a pure, side-effect-free function over a plan (and optionally recorded
  consumption). It never mutates tasks, envelopes, schedules, or accounting.
- It is used for **planning and observation** — identifying which agents/stages
  are on the critical path and where slack exists — not for driving execution.
- It is **deterministic**: identical inputs produce identical results.

CPM is especially valuable in the fractal, agent-centric model: at every level
of the tree, CPM reveals which path dominates the duration and where slack
allows flexibility. It is the tool that makes the system's timing transparent
and auditable.