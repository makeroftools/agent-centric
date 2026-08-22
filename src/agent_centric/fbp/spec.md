# Agent-Centric FBP Architecture — Spec (draft)

**Status:** Foundation design for the `agent-centric-fbp` branch.
**Authority:** Lead Architect (via this session).
**Classification:** Mission-Critical.

This spec locks in the model refined in session and is the contract the
foundation code implements. It is deliberately a *draft* — the first concrete
step of a pivot, not the finished system.

## 1. The model

A rooted, recursive **tree of agents**. There is no central `AgentManager`;
the topology *is* the governance.

This is an **abstract, general-purpose agent system** — not a trading system.
Trading / automated-trading concerns are **non-relevant** to this project.

- **Root = the shell.** The shell is an agent (not an external orchestrator). It
  bootstraps the tree and is the origin of work and the final owner of
  responsibility.
- **Work travels down.** A node delegates work to its children.
- **Responses and responsibility bubble up.** Each node consolidates its
  children's responses and is responsible for its subtree.
- **Every parent is responsible for its children and provides their context.**
  The parent hands down the *context* (domain, rules, verification constraints)
  the child operates within. This is recursive — the same contract holds at
  every level.
- **Each agent owns its domain and is the context provider to its children.**
  An agent receives a **callable** (its task/domain) as its input.
- **Everything stays local when possible.** A node resolves work itself; it
  only delegates down what it cannot resolve locally.
- **The context is hierarchical.** Context composes down the tree, deepening at
  each level.

## 2. Node contract (`init / run / kill`)

Every node implements three operations:

- `init(context)` — set up the node with the context provided by its parent.
- `run(work)` — process a unit of work. May resolve locally or delegate to
  children. Returns a `Response`.
- `kill()` — teardown.

## 3. The correctness spine: verification on the upward path

The property that preserves the mission-critical guarantee ("no unverified
money/dates") in a manager-less design:

- Work + context flow **down**.
- Responses + responsibility flow **up**, and **each parent verifies a child's
  response before accepting responsibility for it and bubbling it up**.
- A response that fails verification is an explicit, audited failure — never a
  verified success.

This makes the tree a **recursive verification hierarchy**: the same
verification rule composes down via context, and is enforced on the way up.

## 3a. The fractal principle: every task is an agent

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

## 3b. Critical Path Method (CPM)

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

## 3c. The Registry Is a Passive Metadata Catalog

The registry is not a module-level structure; it is an **agent**. But it is a
**passive catalog — nothing more**. It holds a list of records of metadata,
where each record describes an agent/capability: its name, its metadata, and
the **location** (URL) of its source code or executable. The registry does
**not** compile, does **not** run, and does **not** hold code.

Other agents take the *information* (the location) and act on it — fetch,
compile, run — as their own governed directives allow. Because agents are
self-contained executable processes (potentially different runtimes), the
system can accommodate **multiple programming languages simultaneously**,
communicating over the ZeroMQ protocol rather than by sharing memory.

This is the fractal principle applied to the registry itself: the registry is
an agent, the compile step is an agent, and each registered capability is an
agent. Registration, compilation, and resolution are all directives — never
module-level globals.

**The trust boundary (clamp down) applies at three distinct points:**

1. **Registry writes** — who may register a record (explicit, audited).
2. **Consumption** — who may act on a recorded location (allowlist/grant).
3. **Execution** — what a runtime agent may run, under what envelope, with
   what verification and audit.

The registry itself has a low trust surface: it only stores and serves
metadata. The power to compile and run lives in the agents that consume the
metadata, and that is where the clamp-down is hardest.

## 4. Scope of this foundation

Implemented here (pure, offline-testable, deterministic):

- `Context` — hierarchical context (the governance mechanism).
- `Node` — the `init/run/kill` contract.
- `Shell` — the root agent that builds the tree and runs work through it.
- Verification on the upward path (a `Verifier` gate).
- `Agent` — the abstract agent with a steppable async `zmq_poll` loop.
- `AgentConfig` — minimal bootstrap (identity + parent endpoint).
- `Directive` / `Ack` / `Response` — the message protocol (the crux).
- **Transport parity** — the protocol is proven to round-trip identically over
  `inproc://`, `tcp://`, and `ipc://`; the transport is a property of the
  channel, never of the protocol.
- `Registry` — a passive metadata catalog serving ``register``/``resolve`` over
  the directive protocol (registry-as-agent); it records location, never code.
- **Chain audit** — every response carries the ``source`` of the callable that
  produced it, so execution is auditable (which callable ran, from where).
- **Mediated spawn & delegation** — a parent provisions a real child ``Agent``
  (not just a socket) via ``spawn``, and routes ``run`` directives down to a
  named child via ``delegate``, relaying the child's verified response up and
  failing closed on an unknown delegation target.
- **Easy-UX driver** — `FbpDriver`, a synchronous driver over the
  directive/response protocol that hides sockets/frames/event loop and runs
  over `inproc`/`tcp`/`ipc` with transport-aware child addressing and bounded
  retry for async link establishment.
- **Durable, on-demand, deterministic state** — `StateStore` (a mutable,
  single-writer, fingerprint-idempotent key/value store) and `TrajectoryStore`
  (an append-only, write-once local audit). Separate SQLite files, opened only
  as an explicit `configure` grant; read-only grants close the write path.
- **Store/registry agent** — `StoreAgent`, the concrete realization of
  "state controlled by a domain-specific agent": a single-writer durable
  resource reached via delegation, bounded by a key-allowlist grant (requests
  outside it fail closed).
- **CPM capability** — Critical Path Method as a first-class, deterministic,
  **read-only** tool (a registered callable, not an agent): forward/backward
  pass, slack/float, critical path; fail-closed on cyclic or malformed input.
- **Bills loop (a real end-to-end FBP graph)** — `BillsAgent` + pure domain
  functions: intake -> human-gated accept -> durable registry -> verified
  calendar projection. No unverified money/dates; no auto-accept; money in
  integer cents and dates ISO.
- **Tree-audit reconstruction** — `reconstruct_chains` rebuilds the full causal
  chain per correlation id from the tree's trajectory stores (audit as
  proof): verified only if every hop on the way up is verified.
- **Deterministic replay** — `FbpDriver.replay` / `replay_session` re-run the
  recorded directive ledger (including delegated runs, with the tree topology
  rebuilt) and compare outcomes, so the system is re-verifiable after the fact.
  Full-tree replay **isolates on-disk state**: every store path (state *and*
  trajectory) granted to the replayed tree is remapped to a fresh temp path, so
  stateful trees (e.g. the bills loop) replay cleanly and replay never touches
  the original store files.

**Deferred (deliberate; optional adapters):**

- Compile-on-demand from a resolved source URL (the execution clamp behind the catalog).
- FastAPI UI/API layer (adds a dependency).
- Real concurrency / multi-channel routing.
- A durable git-backed directive ledger / cross-process directive queue.
- Multi-language runtimes over the ZeroMQ protocol.

These are deliberately deferred so the foundation can be proven correct,
deterministic, and fully tested before layering on transport and UI.

## 5. Non-goals

- Not a trading system; trading concerns are non-relevant.
- No unverified money/dates; no auto-accept.
- No silent registry writes.
- No network in CI.
- No SMTP / send / delete / move email.
- No recurrence engine, payments, delete-all, or broad "edit any JSON".
- No cloud OCR / cloud APIs.