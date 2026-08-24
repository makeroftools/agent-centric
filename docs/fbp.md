# Agent-centric FBP subsystem — easy-UX driver

This is the **user-facing layer** for the agent-centric, flow-based-programming
(FBP) architecture on the `agent-centric-fbp` branch. It sits on top of the
raw directive/response protocol (`fbp.agent`, `fbp.message`) and turns it into
a plain, synchronous, deterministic API — no sockets, no frames, no event loop.

The deep architectural spec lives in [`spec.md`](../src/agent_centric/fbp/spec.md)
and the wire contract in [`protocol.md`](../src/agent_centric/fbp/protocol.md).
This document is the "how do I drive it" companion.

## Network of Experts AI (the axiom)

> **The model is not the expert. The network is.**

An AI is a **network of domain experts** — components, each an expert in its own
narrow domain, wired by data-flow edges into a directed graph. No single model
(however large) is the authority; each component is an expert *within* its
contract, and its output is only trusted when a **deterministic verifier**
confirms it. A general model is just one (fallible) kind of expert — used, never
fully trusted, and only where a deterministic method cannot cover the domain.
The network is the intelligence; the experts are its parts. **Network of Experts
AI** is the coined name for this.

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
  a child's own `verified` claim is not conclusive on its own.
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

    # Run a deterministic plan (sequence of run steps); fails closed on the
    # first unverified step.
    plan = driver.run_plan([
        {"task": "double", "args": {"value": 21}},
        {"task": "double", "args": {"value": 5}, "child": "child"},
    ])
    # plan["ok"] True; plan["results"][i]["value"] 42 / 10
```

### Method map

| Method | Directive | Meaning |
|--------|-----------|---------|
| `register(name, fn, source_url=)` | — | make a callable directive-resolution-safe |
| `resolve(name)` | `resolve` | return a capability's recorded location |
| `configure(...)` | `configure` | set the root's rules, task allowlist, verifier |
| `configure_child(id, ...)` | — | parent provides a spawned child's context |
| `configure_provider(child, provider)` | — | attach an opt-in model backend to a `model` child (composition-time, in-process) |
| `run(task, args, child=)` | `run` | execute, or delegate to a named child |
| `run_plan(steps)` | `run` (each) | run a deterministic sequence of `run` steps, failing closed on the first unverified one |
| `spawn(id, endpoint=)` | `spawn` | provision a real child agent |
| `state_get(key)` | `state_get` | read from the agent's durable state store |
| `state_set(key, value)` | `state_set` | idempotently write to the durable state store |
| `audit()` | `audit` | return the agent's local audit record |
| `summary()` | — | deterministic, operator-facing summary of the session's ledger |
| `status()` | — | flat operator snapshot: `{tree, summary}` for dashboards |
| `tree()` | — | read-only snapshot of the live agent tree (identity, kind, grants, capabilities/verifier/rules); operator discovery |
| `store_keys(child)` | `store_keys` | enumerate a spawned `StoreAgent`'s granted, existing keys (mediated, audited) |
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

### CPM capability (read-only critical-path tool)

Critical Path Method is a first-class, deterministic, **read-only** tool — a
**capability** (a registered callable), **not an agent**. It is a pure
observation, not a unit of work with responsibility, so it has no state to own,
no children to manage, and nothing to verify. Any agent can `run` it as a
registered task. It takes a network of activities (ids, durations, dependencies)
and returns the critical path, per-node slack, and the minimum project duration
via a classic forward/backward pass:

```python
from agent_centric.fbp.critical_path import cpm_from_dict

with FbpDriver() as d:
    d.register("cpm", lambda nodes: cpm_from_dict(nodes).to_dict())
    d.configure(tasks=("cpm",))
    r = d.run("cpm", {"nodes": [
        {"id": "a", "duration": 3},
        {"id": "b", "duration": 2, "depends_on": ["a"]},
        {"id": "c", "duration": 1, "depends_on": ["a"]},
        {"id": "d", "duration": 2, "depends_on": ["b", "c"]},
    ]})
    # r.value["duration"] == 7; critical_path == ["a", "b", "d"]; slack["c"] == 1
```

- **Deterministic**: identical input → identical output (stable topological
  order, sorted tie-breaks).
- **Read-only**: a pure function over the network; never writes a store.
- **Fail-closed**: a cyclic, self-referential, or malformed network is an
  explicit error, never an ambiguous result.

### Bills loop (real end-to-end FBP graph)

The bills loop is the first *real* demonstration of the foundation on a
mission-relevant workflow. Topology: `root -> bills -> store`.

- **`BillsAgent`** (`bills_agent.py`) is a coordinating agent that drives the
  loop over a single-writer `StoreAgent` child (the durable registry). It
  serves `bills_setup`, `bills_intake`, `bills_accept`, `bills_calendar`,
  `bills_registry` (a read-only snapshot of the durable registry).
- **Pure domain functions** (`bills.py`) are registered capabilities:
  `bill_total`, `draft_from_intake`, `accept_draft`, `project_calendar`.
- **Agent-level intake tasks** — `bills_intake_file` (json/csv/txt text),
  `bills_intake_email` (a fetched message), and `bills_intake_pdf` (base64 PDF
  bytes) produce **unverified** drafts over the protocol, all requiring the
  human `bills_accept` gate.
- **Human-gated accept**: a draft becomes a registry bill only via an explicit
  `bills_accept`; nothing auto-accepts. Amounts are integer cents; dates are
  ISO; malformed intake fails closed (no invented facts).
- **Single-writer registry**: only the store child writes the registry file,
  under a key allowlist; the BillsAgent reads/writes *through* it. A granted
  key ending in `*` is a **prefix grant** (e.g. `bill-*` authorises every key
  under the namespace), so a parent can grant a namespace of ids while still
  failing closed on anything outside it.

```python
with FbpDriver() as d:
    d.spawn("bills", kind="bills")
    d.run("bills_setup", {"state": "registry.db", "store_keys": ["b1"]}, child="bills")
    draft = d.run("bills_intake", {"draft": {"id": "b1", "vendor": "GasCo",
        "amount_cents": 12345, "due_date": "2026-10-01"}}, child="bills")
    d.run("bills_accept", {"draft": draft.value}, child="bills")   # human-gated
    cal = d.run("bills_calendar", {"from_date": "2026-10-01", "to_date": "2026-10-31"}, child="bills")
```

This exercises the correctness spine where it matters most — **no unverified
money/dates** — end to end, deterministically and auditably.

**Registry maintenance.** `bills_mark_paid` / `bills_mark_status` are explicit,
mediated, verified status updates through the single-writer store child
(closed status set `open`/`paid`/`void`/`overdue`). They keep the calendar
correct: a bill marked paid drops out of the open agenda. `mark_bill_status` is
a pure, deterministic merge — it never changes money/dates and never implicitly
re-accepts an intake draft.

### Intake capabilities (ported from the Manager line)

Pure, deterministic, offline intake capabilities (registered callables) that
feed **unverified** drafts into the human-gated accept — a malformed or
incomplete source fails closed, so nothing is invented and no money/date ever
auto-enters the registry:

```python
from agent_centric.fbp import draft_from_file, draft_from_email, draft_from_pdf_text

# json / csv / txt / pdf -> an unverified draft
file_draft = draft_from_file(content, source_path="inbox/gasco.pdf")

# embedded PDF text -> an unverified draft (offline, deterministic)
pdf_draft = draft_from_pdf_text(pdf_bytes, source_path="inbox/b.pdf")

# a fetched email (subject + body) -> an unverified draft (read-only)
email_draft = draft_from_email({"folder": "inbox", "id": "m1",
    "subject": "Invoice", "body": "from GasCo amount 123.45 due 2026-10-01"})
```

All three preserve the mission invariant: the produced draft is **unverified**
until a human calls `bills_accept`.

### Allowlisted workspace capability (ported from the Manager line)

`WorkspaceFS` (`workspace.py`) mediates file access strictly under an **explicit
allowlist** (exact files, directories, and prefix directories):

```python
from agent_centric.fbp import WorkspaceFS, WorkspaceLayout

ws = WorkspaceFS("./data", WorkspaceLayout(
    files=("bills/registry.json",), directories=("bills",), prefixes=("inbox/",),
))
ws.create_dir("bills")
ws.write_text("bills/registry.json", '{"bills": []}')   # parent must exist
content = ws.read_text("bills/registry.json").content     # -> the JSON
listing = ws.list_prefix("inbox/")                        # files in inbox/
```

- **Fail-closed**: any path not on the allowlist — including any traversals that
escape the workspace root — is an explicit `WorkspaceError`. **No deletion**
and **no implicit directory creation** (a missing parent). This is the
trust/security boundary for a managed agent environment.

### Model agent (LLM as an ordinary agent)

A `ModelAgent` (`model_agent.py`, spawn kind `model`) serves a `model` run-task:

```python
with FbpDriver() as d:
    d.spawn("model", kind="model")
    r = d.run("model", {"prompt": "hello"}, child="model")  # deterministic stub
    # r.verified True; r.sources[0] == {"kind": "model", "id": "stub-model"}
```

- **An ordinary child**: other agents delegate to it over the normal protocol;
  its output is re-verified by the parent (correctness spine) and audited.
- **Source references**: every response carries `sources` (the model id), so a
  non-deterministic result is auditable with citations.
- **Deterministic by default**: the stub provider is offline and CI-safe. A real
  provider (callable `provider(prompt, **kwargs)` or a `.complete(...)` object,
  e.g. the hardened `OptionalRealModelProvider`) is an opt-in hook — wired via
  `d.configure_provider(child, provider)` at composition time — that never
  relaxes verification.

### Post-return determinism + chat → FBP orchestration

The attaching seam between the LLM chat window and the FBP network, per the
"model proposes, code accepts" rule:

- **`chat_pipeline.py`** — deterministic repair → schema-parse → canonicalize →
  request-key → pin. `schema_parse(text, schema)` coerce raw text to a typed
  dict (fail-closed: required fields, enums, type coercion); `canonicalize`
  sorts keys / collapses whitespace / drops `None`; `request_key` hashes the
  inputs (model id, prompt, template version, context, params); `PinCache`
  serves the first accepted artifact for identical inputs — lexical determinism
  by caching.
- **`orchestrate.py`** — `plan_from_artifact(artifact)` maps a canonical JSON
  artifact (a single `task` or an ordered `steps` list) to an FBP `run` plan;
  `run_artifact_plan(driver, artifact)` executes it through `driver.run_plan`,
  so every step is a normal directive (parent re-verified, ledgered,
  replayable). Unsupported intents / unorchestrable artifacts fail closed.
- **Schema-driven orchestration** — `plan_from_schema(artifact)` validates a
  typed artifact against its intent's schema (`SCHEMAS`: `run`/`double`/`sum`/`bills_intake`/`bills_accept`/`bills_calendar`)
  and coerces fields fail-closed before planning. This is the seam that lets a
  chat window (or a scripted client) emit a *typed, schema-constrained*
  artifact — `{"intent": "sum", "a": 2, "b": 3}` — instead of free-form JSON,
  and still run through the exact same verified spine. Unknown intents, missing
  required fields, and uncoercible types fail closed.

```python
from agent_centric.fbp import plan_from_artifact, run_artifact_plan
steps = plan_from_artifact({"task": "double", "args": {"value": 21}})
result = run_artifact_plan(driver, {"task": "double", "args": {"value": 21}})
# result == {"ok": True, "results": [{"step": 0, "task": "double",
#            "verified": True, "value": 42, "error": None}], "completed": 1}
```

The landing page exposes this as an **Orchestrate → FBP** box (`/orchestrate`
route): paste a JSON artifact and it runs as a verified FBP plan. A
**schema-driven** form (`/orchestrate/schema` serves the intent schemas) lets you
pick a typed intent and fill its fields — same verified spine, typed form
instead of free-form JSON.

The model box also folds **prior transcript turns** into the next prompt as
deterministic chat-context (oldest-first, bounded, auditable): the model answers
with continuity, and the same context prefix feeds both the audited `/model`
path and the streaming preview.

### Expert selection + cost ledger (Network of Experts AI)

`experts.py` is the deterministic core of the **"Network of Experts AI"** axiom:
*the model is not the expert; the network is.* For each component (a **domain**)
it decides *which kind of expert* should serve it, and it accounts the **cost**
and **irreducible residue** per run:

- `Domain` — a component's contract (input schema, output fields, measured
  determinism/residue). A domain is *achievable* (deterministically serviceable)
  iff it has a verifiable output; an unverifiable domain fails closed to human.
- `select_expert(domain, ...)` — a pure, deterministic decision: **deterministic**
  method when the residue is low; **learned** (a per-domain SLM) only when a
  real residue remains and the operator asks for it, or the domain is below a
  determinism ceiling (never bypassing any verifier); **human** for the
  irreducible residue no method or SLM can cover safely.
- `CostLedger` / `CostAccount` — an append-only accounting record of each run's
  cost + residue, filterable per domain (the deterministic foundation for
  showing "what does this network cost" — and, later, opt-in micro-payment
  settlement via an external adapter).

The landing page exposes a read-only **Network of Experts** card (`/experts`):
for each domain in the live tree it shows the chosen expert kind and running
cost. `FbpDriver` now also surfaces its configured **verifier names** (read-only)
so the readout knows which domains are verifiable.

### Domain-expert SLM provider contract (the learned tier)

`fbp/slm.py` is the opt-in external seam that turns a domain corpus into a trained per-domain expert — the **learned** tier `select_expert` recommends when a deterministic method can't cover the residue. `SlmProvider` (protocol), `SlmSpec` (recipe), `SlmExpert` (full provenance: base model, corpus, method, dataset size, benchmark, artifact), `StubSlmProvider` (offline, deterministic, CI-safe), and `build_domain_expert` (fail-closed entry point). Training stays external; the core selects and verifies, never bypassing a verifier. The landing page has a read-only **Domain SLM** card (`/slm`) showing which domains warrant a learned expert. `docs/domain_slm.md` is the 2026 engineering reference.

### Domain Registry + artifact vault

`domainrepo.py` is the **observability + provenance** layer that completes the
network-of-experts model (*catalog → decision → accounting → evidence*):

- `DomainRegistry` — a **read-only catalog** of domains (id, name, contract,
  measured determinism/residue, chosen expert kind, provenance), **tenant-aware**
  and queryable by id too. It is a *passive catalog, never an authority* — it
  records what and how, it never decides.
- `ArtifactVault` — an **append-only, write-once** store of domain run
  artifacts (verified output keyed by (tenant, domain, run), with residue, cost,
  and source refs). Re-recording a key fails closed; it is *evidence*, never
  mutable.

The landing page exposes read-only **Domain Registry** and **Artifact
repository** cards (`/domains`, `/artifacts`); the write-once artifact evidence
persists across restarts via `fbp-web --registry <path>` (explicit grant).
Tenant-awareness from the start is what makes a future paid, multi-tenant web
service an additive layer over this core rather than a rewrite — the service is
a hosted, shared observability + provenance layer, never the governance layer.

### Component Networks (visual programming)

A component network is a directed graph of components wired by data-flow edges
(`network.py`): place components (each an FBP `task`), connect an output field
of one to an input arg of another, and the network compiles to an ordered FBP
`run` plan that runs through the verified spine.

```python
from agent_centric.fbp import Component, ComponentNetwork, Edge, run_network

net = ComponentNetwork()
net.add_component(Component(id="a", task="double", args={"value": 21}))
net.add_component(Component(id="b", task="even", args={}))
net.add_edge(Edge(source="a", source_field="value", target="b", target_arg="value"))
result = run_network(driver, net)  # double(21) -> even(42); both verified
```

- **Deterministic by construction**: compile order is a topological sort
  (ties by id), so the same graph always yields the same ordered plan.
- **True dataflow**: each component runs in topological order and its *computed*
  verified output is threaded into downstream args — a `sum` can consume two
  `double`s, and the result is the real computed value, not a constant.
- **Fail-closed**: cycles, self-loops, edges to unknown components/output
  fields, and unverified steps are rejected — a half-wired or failing network
  never silently succeeds.
- The landing page has a **Component Network** visual editor: a dependency-free
  drag-and-drop node-and-wire canvas (palette buttons, draggable nodes,
  click-to-connect **and drag-and-drop** ports with a live preview wire, SVG
  edges, double-click to remove, live JSON). No third-party/CDN library — it
  stays stdlib-only and fail-closed.
- **Agent composition**: the palette exposes the real **demo agents** (`child`,
  `store`, `model`, `bills`) as draggable nodes that delegate via the network's
  `child` field — so a network can compose actual spawned agents (e.g. `child`
  double → `store` write, `model` → `store` write, or a full `bills`
  intake → accept → calendar chain) through the verified spine. The arithmetic
  primitives remain as a secondary **Primitives** group for building simple test
  networks that feed the agents. A **Load agent demo** button builds a canonical
  agent chain in one click.
- **Edge value labels**: after a run, each edge shows the computed value that
  flowed along it (from the verified source output), so you can see the data
  moving through the network.
- **Durable save/load**: with `fbp-web --networks <path>`, named networks can be
  saved and reloaded across restarts (`/network/save|/list|/load`); a network is
  validated before it is persisted (fail-closed).

The landing page is **card-based** with a **Chat / Designer** mode switch: Chat
holds the model box, chat history, and the run-an-artifact box (general-purpose
chat); Designer holds the Component Network editor. The two are clearly divided.

### Determinism rating + approved rules (determinize-then-decide)

`determinism.py` is a pure capability that makes the "never rely on a
non-deterministic output directly" rule operational:

```python
from agent_centric.fbp import Rule, RuleSet, resolve_with_rules, score_determinism

ambiguous = {"vendor": "GasCo"}                    # few fields -> low score
score = score_determinism(ambiguous)              # reserves human judgment

rules = RuleSet([Rule(id="r1", domain="vendor", method="from_vendor",
                      matcher={"vendor": "GasCo"})])
resolved, rule = resolve_with_rules(
    {"vendor": "GasCo", "amount_cents": 12345, "due_date": "2026-10-01"}, rules)
# resolved is not None, rule.id == "r1"  -> auto-resolve deterministically
```

- `score_determinism` rates how reproducibly a draft's extraction could be
  determined (0..1), purely as a function of the draft — never a live model.
- A human (or analyser) authorizes a `Rule` once; `resolve_with_rules` then
  auto-resolves matching intake deterministically (attributable to the rule),
  and only non-matching (irreducible) residue reaches the human.
- **Bills integration:** a `BillsAgent` serves `bills_accept_deterministic`,
  which auto-accepts a draft only when an approved rule matches (recording the
  rule id as the source); a non-matching draft fails closed back to human
  `bills_accept`. Rules are granted via `configure_child(rules=...)` **and/or**
  persisted durably via `bills_rule_add` (stored grant-bound in the registry's
  single-writer store) — so an authorized rule keeps auto-accepting matching
  intake across restarts without re-granting.

### Operator activity feed (audit of what an operator did)

`activity.py` is the observability layer for *what an operator did* through the
easy-UX surface. It is the audit counterpart to the chat history (the transcript
of model turns) and the artifact vault (the write-once evidence of domain runs):
a bounded, append-only, optionally-durable log of operator actions, each
carrying its verification status.

```python
from agent_centric.fbp import ActivityFeed

feed = ActivityFeed()
feed.record(kind="model", action="model run", verified=True, model="stub-model")
feed.record(kind="bills", action="bills_accept", detail={"id": "bill-b1"}, verified=True)
feed.to_dict()   # {"entries": [...], "count": 2, "verified": 2, "durable": False}

# Durable by explicit grant: persists atomically and reloads on open.
durable = ActivityFeed(path="activity.json")
feed.record(kind="provision", action="provision bill-extract", verified=True)
```

- **Append-only & immutable**: entries are never mutated once recorded; each
  carries a monotonic `seq` (insertion order, oldest first). `clear()` is the
  only destructive operation (an explicit operator action).
- **Bounded**: capped at `MAX_ACTIVITY_ENTRIES` (500); the oldest entries drop
  first, so the feed never grows without bound.
- **Deterministic**: ordering is by sequence number, never a wall-clock
  timestamp, so identical action sequences yield identical readouts.
- **Fail-closed**: an unknown `kind` or an empty `action` is rejected; a corrupt
  durable file degrades to an empty feed rather than crashing the caller.
- **Durable by explicit grant**: passing a `path` persists the feed (temp file +
  atomic `os.replace`) and reloads it on open. Without a path it stays
  in-memory only — persistence is always an explicit grant.

The kinds form a closed set (`model` / `orchestrate` / `network` / `bills` /
`provision` / `action`), so the feed is self-describing and auditable. A runnable
demo lives at `examples/fbp_activity_demo.py`.

### Tree-audit reconstruction (audit as proof)

`reconstruct_chains` (`audit.py`) is a read-only **capability** that turns the
chain-audit machinery into something you can *prove* with. Each agent records
its local activity; each parent records a `relay` hop when it accepts a child's
verified response. The observer gathers every descendant's trajectory store and
reconstructs the **full causal chain per correlation id** — the directive's
path down the tree and the verified result (or audited failure) that bubbled
up.

```python
chains = driver.reconstruct_audit()   # list[ChainEvent-dict], one per corr id
for c in chains:
    # c["correlation_id"], c["verified"], c["terminal"], c["terminal_value"],
    # c["events"] -> [{"node", "kind", "verified", "value", "parent"}, ...]
```

- **Read-only**: a pure function over the stores; never mutates anything.
- **Deterministic**: identical stores → identical chains.
- **Proof-oriented**: `c["verified"]` is True only if every hop in the chain is
  verified — so you can assert *why* a result is trustworthy, not just that it
  was recorded.

### Deterministic replay (re-verification after the fact)

`FbpDriver.replay()` extends audit from *reconstruction* to *re-verification*.
Every directive the driver issues is recorded in a **ledger** (kind + payload +
terminal response). `replay()` re-runs a recorded local `run` against a fresh,
storeless driver — the same deterministic task — and compares the fresh outcome
to the recorded one:

```python
ledger = driver.ledger()                # {corr_id: {"kind", "payload", "response"}}
r = driver.replay(target=<corr_id>)    # or None for the latest local run
# r["passed"] True iff fresh outcome == recorded outcome
# r["recorded"] / r["replayed"] / r["diff"]
```

- **Sound because deterministic**: identical directive + identical task =>
  identical result, so a divergence flags a real change (fragile or
  non-deterministic callable, or environment drift), never noise.
- **Fail-closed**: an unknown target or a missing task is an explicit "not
  passed", never a silent match.

`replay_session()` is the general form: it re-issues the **whole recorded
directive sequence** in order on a fresh driver — rebuilding the tree topology
(spawn / configure / `configure_child`, recorded as synthetic entries) as it
goes — and verifies every `run` outcome, **including delegated directives**:

```python
r = driver.replay_session()   # {"total", "runs", "passed", "failed", "ok"}
```

**State isolation.** Full-tree replay runs on a fresh driver with on-disk state
**isolated**: every store path granted to the replayed tree (via `configure`,
`configure_child`, or a `run` payload such as `bills_setup`'s `args.state`) is
remapped to a fresh temp path. The replayed tree therefore starts from a clean
slate and never reads or writes the original (live) store files — so stateful
trees like the bills loop replay cleanly, and replay is side-effect-free on real
data. Both `state` and `trajectory` (audit) grants are isolated. The mapping is
deterministic per original path, so a store shared across agents maps to a
single temp path and the replayed topology is preserved.

**Durable, recoverable replay.** The in-memory ledger dies with the process. To
make re-verification **crash-safe and recoverable**, a driver may be granted a
durable directive ledger (an explicit path; never silent):

```python
with FbpDriver(ledger_path="session.ledger.db") as d:
    ...                              # every directive + outcome is persisted

# Later — a fresh process re-opens the ledger and re-verifies the whole session.
from agent_centric.fbp import replay_ledger
result = replay_ledger("session.ledger.db")   # same shape as replay_session()
```

The ledger is append-only and order-preserving (a monotonic sequence), so a
reopened ledger replays identically. It also records a **registry manifest**
(name + source URL + importable module/qualname) of the callables the session
registered. **`replay_ledger` auto-re-seeds** importable callables by importing
the recorded `module.qualname`, so a fresh process re-verifies the session with
no manual registration. A callable with no importable source (e.g. a REPL
closure/lambda) is reported in `missing_callables` for the caller to seed manually.
`replay_ledger` runs on a state-isolated tree. The CLI exposes this as
`agent-centric fbp --ledger <path>` to record, then `agent-centric fbp-replay
<path>` to re-verify in a fresh process.

### Operator summary

`FbpDriver.summary()` (and, for a durable ledger, `summarise_ledger(path)`)
give a deterministic, operator-facing readout of a session: per-kind directive
counts, per-run verified/error outcomes, and a per-run listing. The CLI exposes
it as `agent-centric fbp-summary <path>`. `ok` is True only if every `run`
outcome is verified; a summary with errors exits non-zero — honest, fail-closed
ops.

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
Pass `--integrity <secret>` to run the demo over the **signed wire** (§5.5
traffic integrity): every directive is HMAC-signed and verified on receipt
(opt-in; the ledger still records the clean payload).
`agent-centric fbp-replay <path>` re-verifies a durable ledger; `agent-centric
fbp-summary <path>` gives an operator-facing summary; `agent-centric fbp-domains <path>` gives a saved Domain Registry + Artifact Vault readout; `agent-centric
fbp-web` serves a local, actionable landing page (stdlib `http.server`, loopback-only,
read/verify-only) over an in-process `FbpDriver`, with routes `/`, `/action/run`,
`/model` (a model box — dropdown + spinner; OpenRouter when `OPENROUTER_API_KEY`
is set, deterministic stub otherwise, showing `[verified]`/source),
`/model/stream` (SSE token streaming), `/history` + `/history/clear` (bounded
in-page chat), `/orchestrate` + `/orchestrate/schema` (run-an-artifact and the
schema-driven typed form), `/network` (+ `/network/save|/list|/load`), the
**bills loop** (**demonstration only** — `/bills/intake`, `/bills/accept`,
`/bills/registry`, `/bills/calendar` — intake → human-gated accept → durable
registry → verified calendar, all through the verified spine; no dashboard
tab), `/activity` (the read-only
operator activity feed — a bounded audit of operator actions, each with its
verification status), `/ledger`, `/state.json`, and
`/health`. `fbp-web --reload` auto-restarts on source edits; `fbp-web-kill`
stops the server on the port.

Pass `--history <path>` to give the model box a **durable** transcript: each turn
is appended to an on-disk, single-writer, append-only store (SQLite, WAL,
append-only like `TrajectoryStore`, bounded like the in-memory transcript), so
the chat history survives server restarts and replays identically. Without the
flag the transcript stays in-memory only (the default — persistence is an
explicit grant).

Pass `--bills <path>` to give the bills workflow a **durable** registry: accepted
bills persist across server restarts (explicit grant; in-memory by default).

Pass `--activity <path>` to give the operator **activity feed** a durable home:
every action you take through the page (model prompts, orchestrated/network
runs, bills intake/accept, provisioning, the demo action) is recorded with its
verification status and survives server restarts (explicit grant; in-memory by default).

Pass `--integrity <secret>` to `fbp-web` (or `fbp`) to turn on **§5.5 traffic
integrity** for the in-process tree: every directive on the FBP wire is HMAC-
signed and verified on receipt (opt-in). When enabled the landing page shows a
small `traf-integrity ✓` badge in the sidebar; when omitted the wire stays
plain (default, backward-compatible). The secret is a plain literal (operator-
supplied, never logged).

## Full arc demo

`examples/fbp_arc_demo.py` runs the whole production story in one command:
model-as-agent (delegated, source-referenced), intake as a hint, a durable
approved rule auto-accepting a matching draft, a verified calendar, and
crash-safe replay of the whole session in a fresh process.

```sh
u run python examples/fbp_arc_demo.py
```

## Guarantees

- **No unverified success.** `Response.verified` is True only if the value
  passed every verifier on the way up.
- **Fail-closed.** Bad messages and unknown delegation targets become explicit
  errors; the poll loop never crashes.
- **Idempotent.** Replaying a directive returns the cached result (keyed by the
  full directive fingerprint) rather than re-executing.
- **Deterministic.** Same directives, same tree, same context → same results,
  regardless of transport.