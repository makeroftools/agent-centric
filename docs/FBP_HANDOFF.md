# HANDOFF — Agent-centric FBP subsystem (branch `agent-centric-fbp`)

**Prepared for a new session thread.** Facts are current as of this handoff.

> **Read first:** `src/agent_centric/fbp/spec.md` (architecture) and
> `src/agent_centric/fbp/protocol.md` (wire contract), then `docs/fbp.md`
> (easy-UX driver + capabilities), then this file.
> `HANDOFF.md` at the repo root describes the *older* `main`-branch Manager
> system and is **not** the current work; the FBP subsystem is the active
> branch.

## What Agent-centric FBP is

A rooted, recursive **tree of agents** with **no central manager** — the
topology *is* the governance. Work travels **down**; responses and
responsibility bubble **up**, each parent **re-verifying a child's value**
before accepting it (the correctness spine). A task terminates in a **verified
result or an explicit, audited failure** — never a silent third state.

Agents speak a versioned **directive/response protocol** over ZeroMQ
(`inproc://`, `tcp://`, `ipc://`). The registry is a passive capability
catalog; CPM and audit reconstruction are pure, read-only **capabilities** (not
agents).

## Standards (non-negotiable)
- **No unverified success.** `Response.verified` is True only if the value
  passed every verifier on the upward path.
- **Fail-closed everywhere.** Malformed directives, unknown delegation targets,
  ungranted store keys, malformed domain input, cyclic CPM, replay mismatch —
  all become explicit, audited errors, never silent outcomes or crashes.
- **Deterministic by construction.** Identical directives + identical context
  → identical results. No auto-generated keys/timestamps on the auditable path.
- **Idempotent.** A replayed directive returns the cached result (keyed by the
  full fingerprint) rather than re-executing or double-writing.
- **Full auditability.** Every agent records its local activity; every parent
  records a `relay` hop. The tree audit is reconstructible and replayable.
- **Persistence is an explicit grant.** Stores open only via `configure`
  `state=`/`trajectory=`; an agent never silently writes a file.

## Current git state
- **Branch:** `agent-centric-fbp`; **working tree clean**.
- **HEAD:** `6de8b99` = `feat(fbp): run_plan streams per-step progress (on_step)`.
- **Pushed:** up through `7b1979f` (`feat(fbp): bills loop - first real
  end-to-end FBP graph`). **Unpushed (33):** the older commits plus replay
  state isolation, transport-resolve bills store endpoint, replay per-run
  verifier resolution, durable directive ledger, auto re-seeding, run_plan
  (+ on_step streaming), operator summary, PDF / structured-intake / email
  capability ports, BillsAgent intake tasks, registry maintenance, and CLI
  live-streaming output (see `git log origin/agent-centric-fbp..HEAD`).
- Standing rule: **do not push unless the lead explicitly says push.**

## What's built (the full arc)
| Area | File(s) | What it guarantees |
|------|---------|--------------------|
| **Protocol + transport parity** | `fbp/message.py`, `fbp/agent.py` | Versioned, enforced directive/response contract over `inproc`/`tcp`/`ipc`; ack-retry; fail-closed on malformed input. |
| **Correctness spine** | `fbp/agent.py` | Parent re-verifies a child's value on the way up; a child's self-claimed `verified` is never trusted. |
| **Durable state** | `fbp/store.py` | `StateStore` (single-writer key/value, idempotent by fingerprint) + `TrajectoryStore` (append-only, write-once audit). Separate files, on-demand. |
| **Store/registry agent** | `fbp/store_agent.py` | Single-writer durable resource; key-allowlist grant; ungranted keys fail closed. |
| **CPM (capability, not agent)** | `fbp/critical_path.py` | Deterministic, read-only critical-path/slack analysis. |
| **Bills loop (real graph)** | `fbp/bills.py`, `fbp/bills_agent.py` | Intake → human-gated accept → durable registry → verified calendar. No unverified money/dates; no auto-accept. |
| **Tree-audit reconstruction** | `fbp/audit.py` | Round-reconstructs every causal chain per correlation id (audit as proof). |
| **Deterministic replay** | `FbpDriver.replay()` / `replay_session()` | Re-run recorded local runs (or the whole sequence, incl. delegated runs, rebuilding the tree) and verify outcomes match (re-verification after the fact). Full-tree replay isolates on-disk state *and* trajectory (fresh temp paths), so stateful trees replay cleanly without touching the original stores. Replay faithfully resolves per-run verifiers (and delegated-store state paths). |
| **Durable directive ledger** | `fbp/ledger.py`, `FbpDriver(ledger_path=)`, `replay_ledger` | Crash-safe, recoverable replay: a session recorded to a durable directive ledger (explicit grant) is re-verifiable after the process is gone via `agent-centric fbp-replay`. `replay_ledger` auto-imports the registry manifest to restore callables (importable module.qualname); non-importable ones are reported for manual seeding. |
| **Intake capabilities (ported from main)** | `fbp/pdf_intake.py`, `fbp/intake.py`, `fbp/bills_agent.py` | Deterministic, offline, read-only intake into **unverified** drafts: `draft_from_pdf_text` (embedded PDF text), `draft_from_file` (json/csv/txt/pdf), `draft_from_email` (fetched email). **BillsAgent** also serves them as run-tasks (`bills_intake_file`/`_email`/`_pdf`), so intake -> human accept -> registry is reachable over the protocol and replayable. All require the human `bills_accept` gate; malformed/incomplete sources fail closed (no invented facts, no auto-enter). |
| **Registry maintenance (ported from main)** | `fbp/bills.py` (`mark_bill_status`), `fbp/bills_agent.py` | `bills_mark_paid` / `bills_mark_status` are explicit, mediated status updates through the single-writer store (closed status set); `mark_bill_status` is a pure merge that never changes money/dates or re-accepts intake, and paid bills drop out of the open calendar. |

## Easy-UX driver (`FbpDriver`) and CLI
- `FbpDriver` (`fbp/driver.py`) is the synchronous, easy-UX layer: `register`,
  `resolve`, `configure`, `configure_child`, `run`, `spawn`, `ping`, `kill`,
  `state_set`/`state_get`, `audit`, `reconstruct_audit`, `ledger`, `replay`,
  `replay_session`, `run_plan`, `summary` (and `load_ledger`/`ledger_callables`/
  `replay_ledger`/`summarise_ledger` for the durable ledger).
- CLI: `agent-centric fbp [--transport inproc|tcp|ipc] [--ledger <path>]`
  demonstrates the whole stack (protocol, correctness spine, durable state +
  chain audit, store agent, CPM, bills loop, plan execution, audit
  reconstruction, deterministic replay); `agent-centric fbp-replay <path>`
  re-verifies a durable ledger in a fresh process; `agent-centric fbp-summary
  <path>` gives an operator-facing summary.
- Example: `examples/fbp_durability_demo.py`.

## Validation
- `uv run pytest` → **572 passed**; `uv run ruff check .` clean; `uv run mypy src` clean (72 source files).
- CLI output streams **line-buffered** so piped operator output is visible live.

## Key invariants to never break (FBP)
- **No unverified success; fail-closed everywhere; deterministic control.
- **Persistence is an explicit grant; store writes are single-writer and
  fingerprint-idempotent; no auto-generated ids.
- **Human-gated accept only** for money/registry writes; intake never
  auto-accepts.
- **CPM and audit/replay are read-only capabilities, not agents** — an agent
  is defined by the correctness spine; a pure function isn't a unit of work.
- **Public-surface additive only** — prefer capabilities/adapters over changing
  agent semantics.

## Architecture map (FBP)
- `src/agent_centric/fbp/` — `agent.py` (core Agent + typed `_spawn` kinds),
  `driver.py` (easy-UX), `message.py`, `store.py`, `store_agent.py`,
  `bills.py`, `bills_agent.py`, `critical_path.py`, `audit.py`, `shell.py`,
  `node.py`, `context.py`, `registry.py`, `config.py`, plus `spec.md` /
  `protocol.md`.
- Package exported from `agent_centric` (aliased `Fbp*` names) and re-exported
  from `agent_centric.fbp`.

## Tooling / validation commands
```sh
uv sync --extra dev
uv run pytest                 # 522 passed (as of this handoff)
uv run ruff check .           # clean
uv run mypy src               # clean, 69 files
uv run agent-centric fbp      # drive the FBP demo over inproc
uv run agent-centric fbp --transport tcp|ipc
```

## Honest non-goals / limits
- FBP is on `agent-centric-fbp`, **not** merged to `main` (which is the older
  Manager system). No cross-pollination has been done.
- Replay covers local and delegated `run` directives recorded in the ledger;
  full-tree replay **isolates on-disk state** (fresh temp paths for replayed
  store grants), so stateful trees like bills replay cleanly and replay never
  touches the original store files. Durable-ledger replay **auto-re-seeds** the
  recorded callables from the manifest (importing module.qualname); only
  non-importable callables (REPL closures/lambdas) need manual seeding, and are
  reported in `missing_callables`.
- No FastAPI UI, no multi-language runtime, no durable git-backed directive
  ledger yet (the SQLite ledger covers recoverable replay; a git-backed queue
  is still future — all deferred per spec.md).
- `docs/fbp.md` is the living companion doc; keep it current with new
  capabilities.

## Suggested next (optional)
- Merge FBP to `main` (or deliberately keep it separate) once the lead
  decides.