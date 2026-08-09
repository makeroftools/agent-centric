# HANDOFF — Meta-Harness (mission-critical system)

**Prepared for a new session thread.** All facts below are current as of this
handoff.

## Current git state — clean and synced
- **Branch:** `main`; **working tree clean**; **up to date with `origin/main`**.
- **HEAD:** `c3bef05` — `docs: enrich README frontpage for v0.21 (Zed ACP quickstart)`.
- **Version:** **0.23.0** (kernel milestone aligned with volley depth: 23 volleys delivered).
- **Pushed:** everything is pushed to origin. **Nothing is held.** The standing
  `do NOT push` rule resumes for any *future* work (pushes happen only on
  explicit instruction; the last push was the R-002 handoff of Volley 021 +
  README).

## What Meta-Harness is
A deterministic, local-first, in-process control plane for governed, verifiable
agents. The **Agent Manager** is the sole authority for policy, tool/model
mediation, resource envelopes, verification, and audit. A task terminates in a
**verified result or an explicit, audited failure** — no third state. Agents
never spawn or directly invoke one another.

Governing docs: `PRINCIPLES.md` (non-negotiable rules), `KERNEL.md` (v0 freeze
note), `STATUS.md` (volley-by-volley history + correctness evidence),
`README.md` (GitHub frontpage, now enriched with a working Zed ACP quickstart).

## What v0.23 includes (Volleys 001–023)
- Deterministic `AgentManager`: register, select (name/capability), run,
  summarise, replay.
- Versioned contracts; capability registry; local tools (`to_upper`, `add`,
  `bill_total`) + **MCP adapter** (`mcp_tools.py`) + **allowlisted workspace
  tools**.
- Model path: stub by default + optional hardened real provider
  (`providers/__init__.py`).
- Composition: sequential / parallel / nested, Manager-orchestrated.
- Governance: policy, hard envelopes, cooperative cancellation, per-step budgets.
- Isolation: optional subprocess backend with silent-hang bounding + forced-kill
  auditing.
- Observability: trajectory summary, replay verification, read-only CPM.
- **Bills specialty agent** (`agents/bills.py`, `contracts/bill.py`): structured
  bills in, deterministic totals out, real verification (recompute; rejects
  bad/missing data).
- **Workspace specialty agent** (`agents/workspace.py`, `contracts/workspace.py`,
  `control_plane/workspace.py`): local allowlisted workspace with mediated file
  tools (list/read/write/mkdir) that reject any disallowed path fail-closed.
- Operator CLI `meta-harness` (run/summarise/replay-verify) + **ACP adapter**
  `meta-harness-acp` (Zed external agent).

## Public surface (deliberate, minimal)
Top-level `meta_harness/__init__.py` exports `AgentManager`, core contracts,
backends, stores, `summarise`/`replay`/`verify_replay`, `analyse_critical_path`,
MCP adapter types, providers, and builder helpers. Sub-package `__init__.py`
files define `__all__`; `py.typed` marks the package typed. **Additive changes
only — prefer adapters/backends over changing Manager semantics.**

## Key invariants to never break
- No unverified success; fail-closed everywhere; deterministic control plane;
  full auditability; local-first.
- **Model and MCP outputs are untrusted until verified.**
- **Real providers are opt-in**; CI/stubs are the default (no network in CI).
- Migration/follow-up: if you change public types, respect the freeze note in
  `KERNEL.md`.

## Architecture quick map
- `src/meta_harness/contracts/` — versioned contracts (incl. `bill.py`, `workspace.py`).
- `src/meta_harness/agents/` — thin interface + built-in agents (counter,
  reverse, case_tool, model_agent, bills, workspace).
- `src/meta_harness/control_plane/` — `manager.py`, `registry.py`, `tools.py`,
  `verifier.py`, `trajectory_store.py`, `execution.py`, `worker.py`,
  `summary.py`, `replay.py`, `critical_path.py`, `mcp_tools.py`,
  `workspace.py`.
- `src/meta_harness/providers/` — stub / failing stub / optional real provider.
- `src/meta_harness/acp.py` — thin ACP adapter (uses official
  `agent-client-protocol` SDK, runtime dep `agent-client-protocol>=0.12.0`).
- `src/meta_harness/cli.py` + `__main__.py` — operator CLI.
- `tests/` — invariant tests across every volley (314 total).

## Tooling / validation commands
```sh
uv sync --extra dev
uv run pytest        # 314 passed (as of handoff)
uv run ruff check .  # clean
uv run mypy src      # clean, 43 source files
```
- Entry points: `meta-harness` (operator CLI), `meta-harness-acp` (ACP agent).
  Both smoke-verified.
- Quick manual checks: `uv run meta-harness run`;
  `printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}\n' | timeout 15 uv run meta-harness-acp`.

## Validation status (last full run)
- `pytest` → **314 passed**; `ruff` clean; `mypy` clean (43 files).

## Where we are / next steps
- Kernel is **complete at v0.23**; no volley currently in flight. The last
  accepted work: **Volley 023** (workspace specialty agent).
- **Known v1 limits** (documented, not bugs): ACP is edge-transport only (not
  full coding-agent parity: no diffs/slash-commands/nested subagents);
  `session/cancel` is per-session but mid-run Manager cancellation is not
  pre-emptible; demo prompt routing is fixed (`reverse` default, `upper`,
  `counter`, stub `model`).
- **Roadmap posture:** use first, enhance on demand. Planned order: **bills
  (022) → workspace (023) → email**. The next step is a **Mail-in-a-Box
  read-only tool + email agent** (list/fetch first, never send in v1; secrets
  via env; redaction). Each stays Manager-mediated, policy-bound, verified, and
  audited. The Architect issues the email volley only after 023 is accepted.

## Ground rules for the new thread
- Mission-critical: **correctness first, deterministic control plane,
  fail-closed, full auditability, additive-only.** Prefer docs/packaging/
  adapters over new machinery.
- Strict typing, linting, high test coverage. One descriptive commit per volley;
  **do not push unless the lead explicitly says push** (the current baseline is
  fully pushed).
- When in doubt, ask the lead before starting a volley; do not expand
  composition or introduce messaging/a2a without explicit direction.
