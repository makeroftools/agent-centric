# FBP — the Agent-Centric Flow-Based Subsystem

> **The topology is the governance.** No central Manager. A rooted, recursive
> tree of agents where work flows **down** as directives and responsibility
> bubbles **up** — each parent **re-verifying its child's value** before it
> accepts it. A task ends in a **verified result or an explicit, audited
> failure** — never a silent third state.

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

---

## Why you should care

Autonomous agents are only useful if you can *trust* what they did. FBP is
built around four non-negotiable guarantees:

- **🔒 No unverified success.** `Response.verified` is `True` *only* if the
  value passed every verifier on the upward path. A child's self-claimed
  `verified` is never trusted.
- **🚪 Fail-closed everywhere.** Malformed directives, unknown targets,
  ungranted store keys, malformed intake, cyclic CPM, replay mismatch — all
  become explicit, audited errors. Nothing crashes, nothing is silent.
- **🎯 Deterministic by construction.** Identical directives + identical
  context ⇒ identical results. Replay is a computation, not a hope.
- **🧾 Fully auditable.** Every response carries the `source` of the callable
  that produced it; the whole tree audit is reconstructible per correlation id
  and re-verifiable after the fact.

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
| **Correctness spine** | Parent re-verifies a child's value on the way up; a self-claimed `verified` is never trusted. |
| **Durable single-writer state** | `StateStore` (keyed, fingerprint-idempotent) + `TrajectoryStore` (append-only audit). Persistence is always an explicit grant — never silent. |
| **Bills loop** | intake → **human-gated accept** → durable registry → verified calendar. No unverified money/dates; nothing auto-accepts. |
| **Intake (ported from main)** | `draft_from_file` (json/csv/txt/pdf), `draft_from_email`, `draft_from_pdf_text` — offline, deterministic, read-only → **unverified** drafts. |
| **Registry maintenance** | `bills_mark_paid` / `bills_mark_status` — explicit status updates; paid bills drop out of the open calendar. |
| **Allowlisted workspace** | `WorkspaceFS` mediates file access under an explicit allowlist — traversal and disallowed paths fail closed, no deletion, no implicit dir creation. |
| **CPM capability** | Deterministic, read-only critical-path / slack analysis (a pure function, not an agent). |
| **Tree-audit reconstruction** | Rebuilds every causal chain per correlation id from the tree's stores — audit as computational proof. |
| **Deterministic replay** | Re-runs recorded runs (local and delegated, tree rebuilt) and verifies outcomes — re-verification after the fact. |
| **Durable, crash-safe replay** | A durable directive ledger (explicit grant) survives the process; `replay_ledger` auto-imports the registry manifest and re-verifies in a fresh process. |
| **Plans + observation** | `run_plan` runs a deterministic sequence (fail-closed on the first unverified step) with per-step progress; `summary()`/`summarise_ledger` give an operator-facing readout. |

---

## The bills loop — the mission-critical arc, end to end

FBP is proven on the workflow that matters most: **money and schedule, with a
human in the loop.** Nothing auto-accepts; every money/date fact is verified or
explicitly declined.

```text
inbox file · email · PDF
   │  (offline, deterministic intake ⇒ UNVERIFIED draft)
   ▼
bills_intake_file / _email / _pdf
   │
   ▼
bills_accept   ←  the ONLY write path (explicit, human-gated)
   │
   ▼
single-writer registry (grant-bound keys)
   │
   ▼
verified calendar   →   mark-paid / mark-status (maintenance)
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
uv run agent-centric fbp-replay ses.db      # re-verify 18/18 runs in a fresh process
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
- **Human-gated accept only** for money/registry writes.
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
> there. Nothing here claims more than the code and 579 passing tests prove.