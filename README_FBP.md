# FBP — the Agent-Centric Flow-Based Subsystem

> ## Network of Experts AI
>
> **The model is not the expert. The network is.**
>
> An AI is a **network of domain experts** — components, each an expert in its
> own narrow domain, wired by data-flow edges into a directed graph. No single
> model (however large) is the authority; each component is an expert *within*
> its contract, and its output is only trusted when a **deterministic verifier**
> confirms it. A general model is just one (fallible) kind of expert — used,
> never fully trusted, and only where a deterministic method cannot cover the
> domain. The network is the intelligence; the experts are its parts.
>
> **Network of Experts AI** is the name for this: intelligence that lives in a
> verified network of narrow domain experts, not in any one model.

> **The topology is the governance.** No central Manager. A rooted, recursive
> tree of agents where work flows **down** as directives and responsibility
> bubbles **up** — each parent **re-verifying its child's value** before it
> accepts it. A task ends in a **verified result or an explicit, audited
> failure** — never a silent third state.
> **The platform is deterministic first and foremost.** Non-deterministic tools
> are inputs we *use* — never authorities we fully trust; only a deterministic
> check can make a result count.

`agent-centric-fbp` is the flow-based, agent-centric branch of the
Agent-centric system. It is a **mission-critical, automated backbone** built for
correctness, determinism, security, and an easy UX. Everything below is real,
tested, and reachable today through a single synchronous driver.

---

## The one-line idea

```text
        work + context flow DOWN

          ┌────────────────────────────┐
          │  directive / response      │
          │  protocol  (inproc/tcp/ipc)│
          └────────────────────────────┘

   root ──▶ bills ──▶ store ──▶ calendar
    ▲                
    └───────── verified responses + responsibility come UP
              (each parent re-verifies a child's value)
```

Think of the tree as **governance made physical**: authority isn't vested in a
central manager, it lives in *who sits above whom*. A parent grants context,
re-verifies results, and owns responsibility for its subtree. When a task
finishes, either its value was verified on every step back to the root, or the
tree recorded exactly where and why it failed.

**LLMs have a place — as ordinary agents.** A model (a "Grok 4.6" agent, a
"DeepSeek V4 Flash" agent, …) is just another capability in the tree. Other
agents **delegate to it through the normal directive/response protocol** when a
task warrants judgment — but its output is treated like any other child's value:
re-verified by each parent's verifier, audited, and not treated as conclusive
on its own word. That is exactly the "deterministic platform first" rule in
action: use the non-deterministic tool, verify it deterministically, don't let
its word alone be the answer.

---

## Why you should care

Autonomous agents are only useful if you can *trust* what they did. FBP is
built around four non-negotiable guarantees:

- **🔒 No unverified success.** `Response.verified` is `True` *only* if the
  value passed every verifier on the upward path. A child's self-claimed
  `verified` is not conclusive on its own.
- **🚪 Fail-closed everywhere.** Malformed directives, unknown targets,
  ungranted store keys, malformed intake, cyclic CPM, replay mismatch — all
  become explicit, audited errors. Nothing crashes, nothing is silent.
- **🎯 Deterministic by construction.** Identical directives + identical
  context ⇒ identical results. Replay is a computation, not a hope.
- **🧾 Fully auditable.** Every response carries the `source` of the callable
  that produced it; the whole tree audit is reconstructible per correlation id
  and re-verifiable after the fact.

---

## Appreciating the architecture (what makes it elegant)

Before the code tour, it's worth pausing on *why* this architecture is shaped the
way it is — because the subtle parts are the most valuable parts, and they are
the easiest to miss on a first read.

**1. The topology *is* the governance — there is no boss to hack.**
Authority isn't held by one privileged component you'd need to protect. It's
distributed into *who sits above whom*: a parent is responsible for its subtree
simply by being its parent. To change what a subtree may do, you change the
relationship. There is no central registry of power to compromise; security comes
from the shape, not from locking a door.

**2. Verification happens on the way up — so the last word is never a child's.**
Work flows down, but every value that returns passes through each parent's
verifier before it counts. A child cannot make its own result true by claiming it.
This single, recursive rule dissolves a whole class of "trust me" problems into
one mechanical, auditable check.

**3. Determinism is a *decision*, not a hope.** Identical inputs give identical
outputs, so **replay is a computation**: you can re-run a session afterward and the
outcomes must match, or the system tells you what drifted. That's the difference
between "run it and hope it behaved" and "prove it behaved."

**4. Persistence is a grant, not a right.** Nothing is written unless a parent
explicitly grants it — single-writer, idempotent, no auto-generated ids. A whole
class of bugs is impossible: nothing silently persists, nothing secretly mutates,
and an ungranted key fails closed instead of leaking.

**5. Models are guests, not kings.** A large model is welcome — as one kind of
expert *within* a component's contract. Its output is re-verified, audited, and
never conclusive on its own word. The architecture uses models exactly where they
help and refuses to let them become the sole authority anywhere. That is the
**Network of Experts AI** axiom made operational.

Each of these looks like a constraint; together they are the source of the
system's calm. A network you can't quietly corrupt, values you can't silently
fake, a history you can always ask to reproduce, and durable state you can
*see* being written. That is the point of all the discipline.

---

## Meet `FbpDriver` — the easy-UX layer

Raw sockets, frames, and event loops are hidden. `FbpDriver` is a plain,
synchronous API over the whole stack:

```python
from agent_centric.fbp import FbpDriver

def double(x: int) -> int:
    return x * 2

def even(v) -> bool:
    return isinstance(v, int) and v % 2 == 0

with FbpDriver() as d:                       # inproc, offline, deterministic
    d.register("double", double, source_url="file:///tasks/double")
    d.register("even", even)
    d.configure(tasks=("double",), verifiers=("even",), verifier="even")

    local = d.run("double", {"value": 21})    # verified 42

    d.spawn("child")                          # provision a real child Agent
    d.configure_child("child", tasks=("double",))
    delegated = d.run("double", {"value": 21}, child="child")   # re-verified up

    plan = d.run_plan([                       # a deterministic sequence
        {"task": "double", "args": {"value": 5}},
        {"task": "double", "args": {"value": 10}, "child": "child"},
    ])
```

One line to run the whole story:

```sh
uv run agent-centric fbp                  # inproc (default)
uv run agent-centric fbp --transport tcp # real distribution
uv run agent-centric fbp --transport ipc # local inter-process
```

---

## The capability tour (all real, all easy)

| Capability | What it guarantees |
| --- | --- |
| **Protocol + transport parity** | One versioned, enforced directive/response contract on `inproc://`, `tcp://`, `ipc://`. The transport is a property of the *channel*, never of the logic. |
| **Correctness spine** | Parent re-verifies a child's value on the way up; a self-claimed `verified` is not conclusive on its own. |
| **Durable single-writer state** | `StateStore` (keyed, fingerprint-idempotent) + `TrajectoryStore` (append-only audit). Persistence is always an explicit grant — never silent. |
| **Bills loop** | intake treats ambiguous output as a **hint**, derives a deterministic method wherever possible, and only the irreducible residue reaches the human → registry → verified calendar. |
| **Intake (ported from main)** | `draft_from_file` (json/csv/txt/pdf), `draft_from_email`, `draft_from_pdf_text` — offline, deterministic, read-only → **unverified** drafts. |
| **Registry maintenance** | `bills_mark_paid` / `bills_mark_status` — explicit status updates; paid bills drop out of the open calendar. |
| **Allowlisted workspace** | `WorkspaceFS` mediates file access under an explicit allowlist — traversal and disallowed paths fail closed, no deletion, no implicit dir creation. |
| **Model agent (LLM as an ordinary agent)** | `ModelAgent` (kind `model`) serves a `model` run-task; other agents delegate to it over the protocol, and its output is re-verified by the parent, audited, and carries the model id as a source. Deterministic stub by default; an opt-in provider (callable or `OptionalRealModelProvider`) can be attached via `FbpDriver.configure_provider(child, provider)` — never relaxes verification. |
| **Resource envelopes** | `ResourceEnvelope` (step/size/latency/child bounds) granted at configure time and enforced hard at the agent's run/spawn boundaries — fail-closed, unbounded by default. The FBP-tree analogue of the Manager's least-privilege guarantee. |
| **CPM capability** | Deterministic, read-only critical-path / slack analysis (a pure function, not an agent). |
| **Tree-audit reconstruction** | Rebuilds every causal chain per correlation id from the tree's stores — audit as computational proof. |
| **Deterministic replay** | Re-runs recorded runs (local and delegated, tree rebuilt) and verifies outcomes — re-verification after the fact. |
| **Durable, crash-safe replay** | A durable directive ledger (explicit grant) survives the process; `replay_ledger` auto-imports the registry manifest and re-verifies in a fresh process. |
| **Plans + observation** | `run_plan` runs a deterministic sequence (fail-closed on the first unverified step) with per-step progress; `summary()`/`summarise_ledger` give an operator-facing readout. |
| **Read-only inspection** | `tree()` returns a deterministic snapshot of the live agent tree (identity, kind, state/trajectory grants, store key allowlist, and configured capabilities/verifier/rules); `store_keys(child)` lists a `StoreAgent`'s granted, existing keys. A capability, not an agent — nothing is mutated. |
| **Landing-page server** | `agent-centric fbp-web` serves a local, actionable landing page via stdlib `http.server` (loopback-only, read/verify-only): live agent tree, session summary, standing invariants, and a **model box** (dropdown + spinner) that routes a prompt through the `model` agent to OpenRouter when `OPENROUTER_API_KEY` is set (deterministic stub otherwise), showing `[verified]` status and the model source. Answers can **stream** (`/model/stream`, SSE) and a bounded in-page **chat history** (`/history`) is kept, opt-in durable via `--history <path>`. `fbp-web --reload` auto-restarts on source edits; `fbp-web-kill`/`--kill` stops the server. No durable-state mutation. |
| **Component Networks (visual programming)** | A deterministic graph of components wired by data-flow edges (`network.py`), validated as a DAG and compiled (topological, ties by id) into an ordered FBP plan run through the verified spine — with **true dataflow** (a downstream component consumes the *computed* verified output of its upstreams). Cycles / unknown refs / unverified steps fail closed. The landing page has a dependency-free drag-and-drop node-and-wire canvas + durable save/load (`--networks <path>`). |
| **Schema-driven orchestration** | A typed, schema-constrained artifact resolves to a concrete FBP plan (`plan_from_schema`; `SCHEMAS` for `run`/`double`/`sum`) and runs through the same verified spine. The landing page has a typed form (`/orchestrate/schema`). |
| **Expert selection + cost ledger** | The deterministic core of "Network of Experts AI": for each component (a domain) `select_expert` picks the kind — deterministic method / learned (per-domain SLM) / human — and `CostLedger` accounts cost + irreducible residue per run. The landing page has a read-only **Network of Experts** card (`/experts`). |
| **Domain-expert SLM provider contract** | The **learned** tier: an opt-in external provider (`fbp/slm.py`) turns a domain corpus into a trained per-domain expert with full provenance. Training stays external; the core selects (`select_expert`) and verifies. Read-only **Domain SLM** card (`/slm`) shows which domains warrant a learned expert. |
| **Domain Registry + artifact vault** | The observability + provenance layer: `DomainRegistry` (a read-only, **tenant-aware** catalog — passive, never an authority) + `ArtifactVault` (append-only, write-once run evidence keyed by (tenant, domain, run)). Read-only **registry** and **artifact** cards on the landing page; durable via `--registry <path>`. |
| **Bills loop — demonstration** | The mission loop — intake → human-gated accept → durable registry → verified calendar — is a **demonstration** (no dashboard tab), exercised via the live `/bills/*` routes and the demo arcs; durable via `--bills <path>`. Prefix grants (`bill-*`) allow ids under a namespace while failing closed outside it. |

---

## The bills loop — determinize, then decide

FBP is proven on the workflow that matters most: **money and schedule.** The rule:
we **never rely on a non-deterministic output directly.** An ambiguous parse is a
*hint* — we analyze it and turn it into a **deterministic method** to every
degree it can be. Only the true irreducible residue goes to the human:

```text
inbox file · email · PDF
   │  (hint ⇒ derive a deterministic method)
   ▼
intake → analyze → determinize as far as possible
   │
   ├── verified ⇒ durable registry (no human needed)
   └── irreducible residue ⇒ human review
   │
   ▼
single-writer registry (grant-bound keys) → verified calendar
   → mark-paid / mark-status (maintenance)
```

```python
with FbpDriver() as d:
    d.spawn("bills", kind="bills")
    d.run("bills_setup", {"state": "registry.db", "store_keys": ["b1"]}, child="bills")

    draft = d.run("bills_intake_file", {
        "source_path": "inbox/gasco.txt",
        "content": "vendor: GasCo\namount_cents: 12345\ndue_date: 2026-10-01\n",
    }, child="bills")                          # UNVERIFIED draft

    d.run("bills_accept", {"draft": draft.value}, child="bills")   # human gate
    d.run("bills_mark_paid", {"id": "b1"}, child="bills")          # maintenance

    cal = d.run("bills_calendar", {"from_date": "2026-10-01", "to_date": "2026-10-31"}, child="bills")
    print(cal.value)                            # total_cents, open bills only
```

Every one of those steps is a **directive** — recorded, replayable, and
re-verifiable after the process is gone.

---

## Ops: record → observe → verify

```bash
uv run agent-centric fbp --ledger ses.db    # record a session durably
uv run agent-centric fbp-summary ses.db     # operator-facing readout (live)
uv run agent-centric fbp-replay ses.db      # re-verify every run in a fresh process
```

The CLI streams **line-buffered**, so you see progress as it happens — even
piped.

---

## Standing behind every line

- **No unverified success.**
- **Fail-closed everywhere.**
- **Deterministic by construction.**
- **Persistence is an explicit grant; single-writer and fingerprint-idempotent;
  no auto-generated ids.**
- **We never rely on a non-deterministic output directly** — we derive a
  deterministic method; only irreducible residue goes to the human.
- **CPM, audit, and replay are read-only capabilities — not agents.**
- **Public-surface additive only.**

---

## Layout

```
src/agent_centric/fbp/
├── message.py / agent.py      protocol + correctness spine + transports
├── driver.py                  FbpDriver (easy UX): run, run_plan, replay, summary
├── store.py                    StateStore + TrajectoryStore (single-writer, idempotent)
├── store_agent.py              single-writer durable resource (key-allowlist grant)
├── bills.py / bills_agent.py   the bills loop + maintenance
├── pdf_intake.py / intake.py   intake capabilities (files, email, PDF)
├── workspace.py                allowlisted workspace capability
├── critical_path.py            CPM — read-only
├── audit.py / ledger.py        tree-audit proof + durable crash-safe ledger
├── context.py / node.py / shell.py / registry.py / config.py
└── spec.md / protocol.md       the deep architecture + wire contract
```

Docs live in [`docs/fbp.md`](docs/fbp.md); the architecture spec and wire
contract are at `src/agent_centric/fbp/{spec,protocol}.md`.

---

> **Status.** This is the **active** FBP subsystem on `agent-centric-fbp`.
> `main` remains the default repo and the prior Manager-line stays contained
> there. Nothing here claims more than the code and **824 passing tests** prove.