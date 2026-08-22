# HANDOFF — Agent-centric FBP subsystem (branch `agent-centric-fbp`)

**Prepared by the Lead Architect so a new session can resume with full context.**
Facts are current as of this handoff (verified live).

> **Read first:** `src/agent_centric/fbp/spec.md` (architecture) and
> `src/agent_centric/fbp/protocol.md` (wire contract), then `docs/fbp.md`
> (easy-UX driver + capabilities), then this file.
> `README_FBP.md` is the story-led, human-friendly deep-dive of the branch.
> `docs/FBP_HANDOFF.md` is this file. `HANDOFF.md` at repo root describes the
> *older* `main`-branch Manager system and is **not** the current work.
>
> **Separate apps spun out** to their own repositories (scaffolds in
> `repositories/`, gitignored — separate git histories): `repositories/ac-router`
> (AC Router gateway) and `repositories/ac-platform` (agent construction law).
> Those land the separate-app design material; this repo keeps only its own work.

## State (verified this session)

- **Branch:** `agent-centric-fbp`; **working tree clean**.
- **HEAD:** `27276c5` = `feat(fbp): full production-arc runnable example`.
- **Pushed:** up through `7b1979f` (origin/FBP). **Unpushed (57 commits)** — see
  `git log origin/agent-centric-fbp..HEAD`.
- **Validation:** `uv run pytest` → **598 passed**; `uv run ruff check .` clean;
  `uv run mypy src` clean (**75 source files**).
- **Git topology:** `main` is **fully contained** in the FBP branch (0 divergent
  commits). Your instruction stands: **keep `main` as the GitHub default**; port
  main's goodness into FBP (done for intake/workspace/maintenance), and only
  later consider making FBP `main` after picking it clean.
- Standing rule: **do not push unless the lead explicitly says push.**

## The mission-critical positioning (the actual design north star)

Agent-centric is **a deterministic platform first and foremost.** It *uses* —
but never fully trusts — non-deterministic tools (LLMs, free-form parsers):

1. A non-deterministic output is a **hint**, not an answer. We derive a
   deterministic method to every degree possible.
2. The only genuine **irreducible residue** goes to a human.
3. The human-authorization becomes a **deterministic rule** that runs
   unattended thereafter ("authorize once, run after restart").
4. LLMs are **ordinary agents** in the tree — delegated to over the normal
   protocol, re-verified by the parent (correctness spine), and their output
   carries **source references**, so it is auditable with citations.
5. **never let a self-claimed `verified` stand alone** → softened to: a child's
   `verified` is *not conclusive on its own*.

These are captured in code (ModelAgent, determinism/auto-accept) and docs.

## What's built (the full arc, all implemented + tested)

| Area | File(s) | What it guarantees |
|------|---------|--------------------|
| **Protocol + transport parity** | `fbp/message.py`, `fbp/agent.py` | Versioned, enforced directive/response contract over `inproc`/`tcp`/`ipc`; ack-retry; fail-closed on malformed input. |
| **Correctness spine** | `fbp/agent.py` | Parent re-verifies a child's value on the way up; a child's self-claimed `verified` is not conclusive on its own. |
| **Durable single-writer state** | `fbp/store.py` | `StateStore` (single-writer, fingerprint-idempotent) + `TrajectoryStore` (append-only audit). Explicit grants only. |
| **Store/registry agent** | `fbp/store_agent.py` | Single-writer durable resource; key-allowlist grant; **grant-bound reads** (`store_keys`, `store_get`) + writes. Ungranted keys fail closed. |
| **CPM (capability, not agent)** | `fbp/critical_path.py` | Deterministic, read-only critical-path/slack analysis. |
| **Bills loop (real graph)** | `fbp/bills.py`, `fbp/bills_agent.py` | Intake → human-gated accept (or deterministic auto-accept) → durable registry → verified calendar → maintenance. No unverified money/dates. |
| **Intake (ported from main)** | `fbp/pdf_intake.py`, `fbp/intake.py` | `draft_from_file` (json/csv/txt/pdf), `draft_from_email`, `draft_from_pdf_text` — offline, deterministic, → **unverified** drafts. |
| **Registry maintenance** | `fbp/bills.py` (`mark_bill_status`) | `bills_mark_paid` / `bills_mark_status` — explicit, mediated status updates; paid bills leave the open calendar. |
| **Allowlisted workspace (ported from main)** | `fbp/workspace.py` | `WorkspaceFS` grants path access under an explicit allowlist; fail-closed on traversal/disallowed; no deletion. |
| **Tree-audit reconstruction** | `fbp/audit.py` | Reconstructs full causal chain per correlation id (audit as proof), incl. source references. |
| **Deterministic replay** | `FbpDriver.replay()` / `replay_session()` | Re-run recorded runs (local + delegated) and verify outcomes; isolates on-disk state, resolves per-run verifiers. |
| **Durable crash-safe replay** | `fbp/ledger.py`, `load_ledger`/`replay_ledger` | Durable directive ledger + registry manifest; `replay_ledger` auto-seeds callables and re-verifies in a fresh process. |
| **Operator summary** | `FbpDriver.summary()` / `summarise_ledger` | Deterministic per-kind/per-run readout; `ok` only if every run verified. |
| **Read-only inspection** | `FbpDriver.tree()` / `store_keys(child)` | Deterministic snapshot of the live agent tree (identity, kind, state/trajectory grants, store key allowlist) and a mediated enumeration of a `StoreAgent`'s granted keys — a capability, not an agent; nothing is mutated. |
| **Plans + progress** | `FbpDriver.run_plan(on_step=...)` | Deterministic sequence, fail-closed on first unverified, streams per-step progress. |
| **Source references on output** | `fbp/message.py`, drivers | `Response.sources` on non-deterministic output; preserved through relays/audit/chains; `FbpDriver.run(sources=...)`. |
| **Model agent (LLM as ordinary agent)** | `fbp/model_agent.py` | Spawn kind `model`; `run("model", ...)`; deterministic stub default; `ModelProvider` opt-in hook (never relaxes verification). |
| **Determinism + auto-accept** | `fbp/determinism.py`, `fbp/bills_agent.py` | `score_determinism`, `Rule`/`RuleSet`; `bills_accept_deterministic` auto-accepts **only when an approved rule matches** (rule id as source), else falls back to human review. |
| **Durable approved rules** | `fbp/bills_agent.py` (`bills_rule_add`) | Rules persisted in the single-writer store; auto-accept works across restarts ("authorize once, run after restart"). |

## Easy-UX driver (`FbpDriver`) and CLI

- `FbpDriver` (`fbp/driver.py`) is the synchronous easy-UX layer: `register`,
  `resolve`, `configure`, `configure_child`, `configure_provider`, `run`, `run_plan`, `spawn`, `ping`,
  `kill`, `state_set`/`state_get`, `audit`, `reconstruct_audit`, `ledger`,
  `replay`, `replay_session`, `summary`, `load_ledger`/`replay_ledger`, plus the
  read-only inspection helpers `tree()` and `store_keys(child)`.
- CLI: `agent-centric fbp [--transport inproc|tcp|ipc] [--ledger <path>]`
  drives the whole stack (protocol, spine, durable state, store agent, CPM,
  bills loop, intake, maintenance, model agent, determinism, audit, replay);
  `agent-centric fbp-summary <path>`; `agent-centric fbp-replay <path>`;
  `agent-centric fbp-web` serves a local, actionable landing page (stdlib
  `http.server`, loopback-only, read/verify-only).
- Example: `examples/fbp_arc_demo.py` (runnable end-to-end production arc);
  `examples/fbp_demo.py`, `examples/fbp_durability_demo.py`.

## Validation

- `uv run pytest` → **602 passed** · `uv run ruff check .` clean ·
  `uv run mypy src` clean (75 source files).
- Cross-transport durable replay verified live: **19/19 runs on inproc, ipc, tcp**.
- Full production arc demo: model-delegate → durable-rule auto-accept (rule id
  as source) → verified calendar → **crash-safe replay (6/6)**.

## Standing invariants (never break)

- **No unverified success; fail-closed everywhere; deterministic control.
- **Persistence is an explicit grant; store writes single-writer and
  fingerprint-idempotent; no auto-generated ids.
- **Human-gated** (or rule-authorized-deterministic) only for money/registry
  writes; intake never auto-accepts unruled.
- **We never rely on a non-deterministic output directly** — determinize first;
  only irreducible residue reaches a human; LLM outputs carry source refs.
- **CPM, audit, and replay are read-only capabilities, not agents.**
- **Public-surface additive only.**

## Architecture map (FBP)

- `src/agent_centric/fbp/` — `agent.py`, `driver.py`, `message.py`,
  `store.py`, `store_agent.py`, `model_agent.py`, `determinism.py`,
  `bills.py`, `bills_agent.py`, `pdf_intake.py`, `intake.py`, `workspace.py`,
  `critical_path.py`, `audit.py`, `ledger.py`, `shell.py`, `node.py`,
  `context.py`, `registry.py`, `config.py`, plus `spec.md` / `protocol.md`.
- Public surface re-exported from `agent_centric` (aliased `Fbp*`) and
  `agent_centric.fbp`.

## Tooling / commands

```sh
uv sync --extra dev
uv run pytest                 # 602 passed (as of this handoff)
uv run ruff check .           # clean
uv run mypy src               # clean, 75 files
uv run agent-centric fbp      # drive the FBP demo (inproc)
uv run agent-centric fbp --transport tcp|ipc
uv run python examples/fbp_arc_demo.py   # full production arc example
```

## Honest non-goals / limits

- FBP is on `agent-centric-fbp`, **not** merged to `main`. `main` stays the
  GitHub default. No cross-pollination of FBP internals into main.
- Replay + durable ledger: full-tree replay reuses on-disk store paths by
  default; durable-ledger replay **isolates** state (fresh temp grants).
- No FastAPI UI, no multi-language runtime, no git-backed ledger queue
  (deferred per spec.md).
- Model agent uses a **deterministic stub** by default (CI-safe). A real
  `ModelProvider` is a future opt-in; it must never relax verification.
- `docs/fbp.md` and `README_FBP.md` are living docs; keep them current.

## Suggested next (optional)

**Done this session** — read-only operator inspection:
`FbpDriver.tree()` (live agent-tree snapshot: identity, kind, state/trajectory
grants, store key allowlist, and configured capabilities/verifier/rules) and
`FbpDriver.store_keys(child)` (grant-bounded, mediated key enumeration).
`inspect :` lines in the CLI demo print these for the operator.

**In flight as separate apps (spun out to their own repos under
`repositories/`, gitignored here):**
- AC Router (`repositories/ac-router/docs/DESIGN.md`) — an OpenRouter-style
  gateway, but a deterministic selector, never a correctness authority;
  separated knowledge-base (catalog side-car agent) from the router (pure
  callable wrapped in an agent facet).
- AC Platform (`repositories/ac-platform/docs/AGENT_CONSTRUCTION_LAW.md`) —
  dynamic in the decision, deterministic in the execution; recipe→compiled
  three-tier model with registry agents; the sticky connection; adaptive
  pre-compilation.

**Still open:**
- ✔ Implemented this session: `FbpDriver.configure_provider(child, provider)` —
  wire an opt-in model backend to a spawned `model` child at composition time,
  accepting either a callable `provider(prompt, **kwargs)` (e.g. the hardened
  `OptionalRealModelProvider`) or a `.complete(...)` object. It never relaxes
  the correctness spine. The generic provider-wiring path is complete;
  reachable real endpoints remain an operator deployment concern (network/keys).
- Distribute the model catalogue + define the closed-source/closed model policy
  gate (moved to the spun-out AC Router app).
- Commit-to-push when the lead lifts the do-not-push rule (58 unpushed).
- Later: consider making FBP `main` after picking it clean (per the lead).