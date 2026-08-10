# HANDOFF — Meta-Harness (mission-critical system)

**Prepared for a new session thread.** All facts below are current as of this
handoff.

> **Read first: `KERNEL.md`, `README.md`, `STATUS.md`, then this `HANDOFF.md`.**
> `KERNEL.md` is the v0 freeze note; `STATUS.md` is the volley-by-volley history
> + correctness evidence; `README.md` is the GitHub frontpage with the Zed ACP
> quickstart; this file is the authoritative one-pager for session continuity.

## Current git state — clean and synced
- **Branch:** `main`; **working tree clean**; **in sync with `origin/main`**.
- **HEAD:** `2ed0251b7b74fe9dec012ff9c6aebb3267e6f462` =
  `feat(volley-029): email to unverified bill draft - human-gated`.
- **Version:** **0.29.0** (kernel milestone aligned with volley depth: 29 volleys delivered).
- **Pushed:** Volleys 022–029 are on `origin/main`; nothing is unpushed. The
  standing `do NOT push` rule holds; pushes happen only on explicit instruction.
- **Recommended tag (not created — repo has no tags yet):**
  `git tag -a v0.29.0-milestone -m "v0.29.0 milestone (Volleys 001-029)"`
  (create only on explicit direction).

## What Meta-Harness is
A deterministic, local-first, in-process control plane for governed, verifiable
agents. The **Agent Manager** is the sole authority for policy, tool/model
mediation, resource envelopes, verification, and audit. A task terminates in a
**verified result or an explicit, audited failure** — no third state. Agents
never spawn or directly invoke one another.

Governing docs: `PRINCIPLES.md` (non-negotiable rules), `KERNEL.md` (v0 freeze
note), `STATUS.md` (volley-by-volley history + correctness evidence),
`README.md` (GitHub frontpage, now enriched with a working Zed ACP quickstart).

## The bills loop (the core need this system serves)

```
inbox/ (json/csv/txt/PDF-text) or email --(intake)--> unverified BillDraft
        --> human accept (intake_accept, grant-gated) --> bills/registry.json
        --> calendar projection (bills_calendar) --> upsert / mark-paid (bills.maintain)
```

- **Inbox files** (`.json`/`.csv`/`.txt`/PDF embedded text) and **email** become
  **unverified** `BillDraft` proposals (Volleys 026/027/029).
- **Human accept** (`intake_accept`) is the only path that writes drafts into the
  registry; it never auto-accepts.
- **Registry + calendar** (`bills_registry` agent) project a deterministic agenda
  from the accepted registry.
- **Maintenance** (`bills_registry_upsert` / `_mark_paid` / `_mark_status`)
  keeps vendors/status correct without breaking calendar invariants (Volley 028).
- Every stage is Manager-mediated, policy-bound, verified, and audited.

## What v0.29 includes (Volleys 001–029)
- Deterministic `AgentManager`: register, select (name/capability), run,
  summarise, replay.
- Versioned contracts; capability registry; local tools (`to_upper`, `add`,
  `bill_total`) + **MCP adapter** (`mcp_tools.py`) + **allowlisted workspace
  tools** + **read-only email tools**.
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
- **Read-only email specialty agent** (`agents/email.py`, `contracts/email.py`,
  `control_plane/email_tools.py`, `providers/email.py`): reads-only mediated
  `email_list` / `email_fetch` tools, fake gateway for CI, optional real IMAP
  backend off by default, secrets redacted. No send/delete/move.
- **Bills-registry + calendar agent** (`agents/bills_registry.py`,
  `contracts/bills_registry.py`, `control_plane/bills_registry.py`): a canonical
  local bills registry (`bills/registry.json`, allowlisted) plus a deterministic
  calendar/agenda projection (`bills.registry.v1` / `bills.calendar.v1`), no
  model, recompute verification. Recurrence omitted (future).
- **Dump-intake agent** (`agents/intake.py`, `contracts/intake.py`,
  `control_plane/intake.py`): allowlisted `inbox/` drop zone, deterministic
  inventory, **unverified** bill drafts, and an explicit grant-gated accept that
  persists only human-approved rows into the registry. No silent financial
  commits.
- **PDF -> unverified draft (Volley 027)** (`control_plane/pdf_text.py` +
  `Workspace.read_bytes`): dependency-free, offline embedded-text extraction
  from simple PDFs (Flate or raw, `Tj`/`TJ`). Extracted vendor/amount/due date
  are parsed only into an unverified `BillDraft`; a PDF with no usable embedded
  text fails closed (no draft invented). No OCR, no cloud APIs, no network.
- **Registry maintenance (Volley 028)** (`control_plane/bills_registry.py` +
  `contracts/bills_registry.py`): governed, mediated `bills.registry`/
  `bills.calendar`/`bills.maintain` operations on the same `bills_registry`
  agent — `bills_registry_upsert` (insert or replace by id), `bills_registry_mark_paid`
  (set `paid`, fail closed on missing id), and `bills_registry_mark_status`
  (shared status path). Writes only the allowlisted registry path; never
  implicitly accepts intake drafts; verifier recomputes the expected merge;
  calendar stays correct after maintenance.
- **Email → unverified draft (Volley 029)** (`control_plane/intake.py`): the
  read-only `intake_email_draft` tool on the existing `intake` agent
  (`intake.draft_from_email.v1`) parses a fetched email (subject + body) into
  unverified `BillDraft` rows via the existing accept → registry → calendar path.
  Weak/absent parse fails closed to no draft (no invented facts); read-only (no
  send/delete); grant separate from `email_fetch` and `intake_accept`.
- Operator CLI `meta-harness` (run/summarise/replay-verify) + **ACP adapter**
  `meta-harness-acp` (Zed external agent).

## Public surface (deliberate, minimal)
Top-level `meta_harness/__init__.py` exports `AgentManager`, core contracts,
backends, stores, `summarise`/`replay`/`verify_replay`, `analyse_critical_path`,
MCP adapter types, providers, and builder helpers. Sub-package `__init__.py`
files define `__all__`; `py.typed` marks the package typed. **Additive changes
only — prefer adapters/backends over changing Manager semantics.**

## What Zed ACP does today (thin adapter)
- `src/meta_harness/acp.py` is a **thin ACP adapter** over the official
  `agent-client-protocol` SDK (runtime dep `agent-client-protocol>=0.12.0`),
  exposing Meta-Harness as an External Agent in Zed.
- It is **edge-transport only** — not full coding-agent parity: no diffs,
  slash-commands, or nested subagents.
- **Demo routing is fixed** (`reverse` default, `upper`, `counter`, stub
  `model`); it does not yet route to the bills loop.
- **Spawn path (absolute):** `uv run meta-harness-acp` from the repo root
  (`/home/makerooftools/github/agent-centric`), configured in Zed under
  `agent_servers` (see `README.md` → "Use from Zed (ACP)").

## Key invariants to never break
- No unverified success; fail-closed everywhere; deterministic control plane;
  full auditability; local-first.
- **Model and MCP outputs are untrusted until verified.**
- **Real providers are opt-in**; CI/stubs are the default (no network in CI).
- **No unverified money/dates**: extracted PDF/email facts stay unverified until
  a human accept — no silent registry writes, no unsupervised calendar from
  PDFs/email.
- **No auto-accept**: only the explicit `intake_accept` gate writes drafts into
  the registry.
- **Registry mutations are explicit, mediated, and verified** — upsert / status
  updates write only the allowlisted registry path with integer cents and valid
  ISO dates, and never implicitly accept intake drafts.
- Migration/follow-up: if you change public types, respect the freeze note in
  `KERNEL.md`.

## Explicit non-goals / do-not-build list
- New agents, ACP features, refactors, or dependency bumps without explicit
  direction.
- Auto-accept / unsupervised organize-all (never auto-file or auto-commit money).
- SMTP / send / delete / move email; email→draft stays read-only.
- Recurrence engine, payments, delete-all, or a broad "edit any JSON" tool.
- Cloud OCR, cloud APIs, or any network in CI.
- Messaging fabric / A2A / MCP integration beyond the existing thin adapter.
- Changing Manager orchestration, verification, policy, envelope, or accounting
  semantics (prefer adapters/backends).

## Architecture quick map
- `src/meta_harness/contracts/` — versioned contracts (incl. `bill.py`,
  `workspace.py`, `email.py`, `bills_registry.py`, `intake.py`).
- `src/meta_harness/agents/` — thin interface + built-in agents (counter,
  reverse, case_tool, model_agent, bills, workspace, email, bills_registry,
  intake).
- `src/meta_harness/control_plane/` — `manager.py`, `registry.py`, `tools.py`,
  `verifier.py`, `trajectory_store.py`, `execution.py`, `worker.py`,
  `summary.py`, `replay.py`, `critical_path.py`, `mcp_tools.py`,
  `workspace.py`, `email_tools.py`, `bills_registry.py`, `intake.py`,
  `pdf_text.py`.
- `src/meta_harness/providers/` — stub / failing stub / optional real provider
  + `email.py` (fake + optional real IMAP gateway).
- `src/meta_harness/acp.py` — thin ACP adapter (uses official
  `agent-client-protocol` SDK, runtime dep `agent-client-protocol>=0.12.0`).
- `src/meta_harness/cli.py` + `__main__.py` — operator CLI.
- `tests/` — invariant tests across every volley (428 total).

## Tooling / validation commands
```sh
uv sync --extra dev
uv run pytest        # 428 passed (as of handoff)
uv run ruff check .  # clean
uv run mypy src      # clean, 54 source files
```
- Entry points: `meta-harness` (operator CLI), `meta-harness-acp` (ACP agent).
  Both smoke-verified.
- Quick manual checks: `uv run meta-harness run`;
  `printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}\n' | timeout 15 uv run meta-harness-acp`.

## Validation status (last full run)
- `pytest` → **428 passed**; `ruff` clean; `mypy` clean (54 files).

## Where we are / next steps
- Kernel is **complete at v0.29** in code. Volley 026 (dump intake), Volley 027
  (PDF drafts), Volley 028 (registry maintenance), and Volley 029 (email →
  unverified draft) are implemented and validated. Volleys 022–029 are pushed;
  nothing is unpushed.
- **Known v1 limits** (documented, not bugs): ACP is edge-transport only (not
  full coding-agent parity: no diffs/slash-commands/nested subagents);
  `session/cancel` is per-session but mid-run Manager cancellation is not
  pre-emptible; demo prompt routing is fixed (`reverse` default, `upper`,
  `counter`, stub `model`). Read-only email v1 supports list/fetch only (no
  send/delete/move). PDF intake v1 handles only simple embedded-text PDFs; no
  OCR, no scanned-image PDFs, no auto-accept, no unsupervised calendar from
  PDFs or email. Registry maintenance v1 has no delete-all, no recurrence engine,
  no payments, and no broad "edit any JSON" tool. Email→draft v1 does not
  auto-organize or auto-accept.
- **Suggested next (only if needed):** ACP routes for the bills loop
  (propose → accept → calendar → mark-paid) so the loop is reachable from Zed,
  and/or extraction fixes driven by real data (e.g. more robust PDF/email
  vendor/amount/date heuristics). Otherwise, exercise the full loop on real data
  before adding features.
- **Roadmap posture:** use first, enhance on demand. The planned sequence
  **bills (022) -> workspace (023) -> read-only email (024) -> bills registry +
  calendar (025) -> dump intake (026) -> PDF drafts (027) -> registry
  maintenance (028) -> email → unverified draft (029)** is now implemented. Each
  stays Manager-mediated, policy-bound, verified, and audited. Follow-up
  (candidate, not started): recurrence, or auto-organize/auto-accept.

## Ground rules for the new thread
- Mission-critical: **correctness first, deterministic control plane,
  fail-closed, full auditability, additive-only.** Prefer docs/packaging/
  adapters over new machinery.
- Strict typing, linting, high test coverage. One descriptive commit per volley;
  **do not push unless the lead explicitly says push** (the current baseline is
  fully pushed).
- When in doubt, ask the lead before starting a volley; do not expand
  composition or introduce messaging/a2a without explicit direction.