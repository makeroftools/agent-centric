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
- **HEAD:** `33960e1` = `feat(fbp): deterministic replay`.
- **Pushed:** up through `7b1979f` (`feat(fbp): bills loop - first real
  end-to-end FBP graph`). **Unpushed (2):** `8ae8f6f` (tree-audit
  reconstruction — audit as proof), `33960e1` (deterministic replay).
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
| **Tree-audit reconstruction** | `fbp/audit.py` | Round-reconstruct every causal chain per correlation id (audit as proof). |
| **Deterministic replay** | `FbpDriver.replay()` | Re-run recorded local runs; verify the fresh outcome matches (re-verification after the fact). |

## Easy-UX driver (`FbpDriver`) and CLI
- `FbpDriver` (`fbp/driver.py`) is the synchronous, easy-UX layer: `register`,
  `resolve`, `configure`, `configure_child`, `run`, `spawn`, `ping`, `kill`,
  `state_set`/`state_get`, `audit`, `reconstruct_audit`, `ledger`, `replay`.
- CLI: `agent-centric fbp [--transport inproc|tcp|ipc]` demonstrates the whole
  stack (protocol, correctness spine, durable state + chain audit, store
  agent, CPM, bills loop, audit reconstruction, deterministic replay).
- Example: `examples/fbp_durability_demo.py`.

## Validation
- `uv run pytest` → **519 passed**; `uv run ruff check .` clean; `uv run mypy src` clean (69 source files).

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
uv run pytest                 # 519 passed (as of this handoff)
uv run ruff check .           # clean
uv run mypy src               # clean, 69 files
uv run agent-centric fbp      # drive the FBP demo over inproc
uv run agent-centric fbp --transport tcp|ipc
```

## Honest non-goals / limits
- FBP is on `agent-centric-fbp`, **not** merged to `main` (which is the older
  Manager system). No cross-pollination has been done.
- Replay currently covers **local** (non-delegated) `run` directives; delegated
  and stateful directives are recorded in the ledger but not yet replayed.
- No FastAPI UI, no multi-language runtime, no durable git-backed directive
  ledger yet (all deferred per spec.md).
- `docs/fbp.md` is the living companion doc; keep it current with new
  capabilities.

## Suggested next (optional)
- Extend replay to delegated directives (re-issue through a fresh tree and
  compare the reconstructed relay chain).
- Merge FBP to `main` (or deliberately keep it separate) once the lead
  decides.