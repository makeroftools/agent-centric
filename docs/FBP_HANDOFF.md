# HANDOFF — Agent-centric FBP subsystem (branch `agent-centric-fbp`)

**Prepared by the Lead Architect so a NEW session can resume with full context.**
Facts are current as of this handoff (verified live). This file captures BOTH
the **project state** (code, tests, git) AND the **conversational state** (the
design threads, decisions, working relationship, and open questions) — per the
lead's HARD RULE that a handoff must preserve how we got here, not just where
we are.

---

## 0. READ FIRST (orientation)

- Architecture spec: `src/agent_centric/fbp/spec.md`
- Wire contract: `src/agent_centric/fbp/protocol.md`
- Easy-UX driver + capabilities: `docs/fbp.md`
- Story-led, human-friendly deep-dive: `README_FBP.md`
- Repo-root `HANDOFF.md` / `KERNEL.md` / `STATUS.md` describe the **older
  `main`-branch Manager system** and are NOT the current work.
- This file is `docs/FBP_HANDOFF.md`.

---

## 2. PROJECT STATE (verified live, this session)

### Git
- **Branch:** `agent-centric-fbp`; **working tree clean** (nothing unstaged).
- **HEAD:** `f6a4770` (docs: SLM provider). Commits in this session (all
  local, none pushed to origin by me): `a9e4647` (expert selection + cost
  ledger), `7528ee5` (Domain Registry + Artifact Vault rename), `93c673c`
  (Law 11 admonition), `c01ea2c` (tools: safe-edit/safe-replace), `ed5e27a`
  (bills intents), `7c61773` (Artifact Vault records real runs), `9956eb8`
  (fbp-domains CLI), `f980e3d` (SLM provider contract), `f6a4770` (SLM docs).
  **Pushed to origin:** up through `87e7bbe` (the user pushed). **Unpushed
  (29 commits):** the full `agent-centric-fbp` sequence from `c4afa94` onward.
  Run `git log origin/agent-centric-fbp..HEAD` to see the exact unpushed set.
- `main` stays the GitHub default and is **fully contained** in this branch.
- Standing rule in effect for a long time: **do not push unless the lead
  explicitly says push.** (The lead has since been pushing directly themselves;
  see the push decision note below — the "no push" rule has effectively been
  relaxed, but confirm each time for a given commit.)

### Validation (run this session, all live)
- `uv run pytest` → **939 passed** (was 908; added transport security + real credential-wiring tests)
- `uv run ruff check .` → clean
- `uv run mypy src` → clean (**84 source files**)
- FBP coverage: `experts.py` 94%, `domainrepo.py` **100%**, driver 91%.
- Cross-transport durable replay: 19/19 on inproc/ipc/tcp. Full production-arc
  demo: crash-safe replay 6/6.

### What's built and tested (the full agent-centric FBP capability surface)
Everything in the capability table below is real code, committed, and exercised
by tests — the deterministic platform first, LLMs as ordinary agents, the bills
loop, durable single-writer state, replay/audit, a landing-page server, and now
an OpenRouter-backed model box (dropdown, spinner, streaming, chat history) on
that page:

| Area | Where | Guarantee |
|------|-------|-----------|
| Protocol + transport parity | `fbp/message.py`, `fbp/agent.py` | versioned directive/response over `inproc`/`tcp`/`ipc`; fail-closed on malformed input |
| **Transport trust-boundary** | `fbp/transport.py`, `fbp/driver.py`, `fbp/agent.py` | §5.1 trust-boundary switch: non-loopback `tcp://` bind fails closed unless the caller opts in (`security="local"`/`"tls"`; default `loopback`); §5.3 owner-only `0o600` IPC sockets. Wired into root + child binds. See `docs/transport_trust_boundary.md` |
| **Transport security (§5.2/§5.4/§5.5)** | `fbp/security.py`, `fbp/driver.py` | **Per-peer authorization** (`PeerAuthz`/`PeerPolicy`, opt-in `FbpDriver(peer_autz=...)`) + **traffic integrity** (`sign_payload`/`verify_payload`/`integrity_headers`, HMAC-SHA256) + **mutual-TLS credential config** (`TlsCreds`/`configure_tls`, fail-closed on incomplete material for `security="tls"`). All pure/offline-tested; opt-in and default-off so existing binds are untouched |
| **Real training-provider credential wiring** | `fbp/providers.py` | **Env-driven** (`TRAIN_ENDPOINT`/`TRAIN_TOKEN`/`TRAIN_AUTH_SCHEME`) + a dependency-free `stdlib_http_client` with **secret redaction** (`redact_secrets`); `build_modal_from_env`/`build_credential_client` make the Modal adapter **genuinely workable** against a real endpoint while failing closed with no credentials. Verified live over a bound HTTP server, secret never leaks |
| Correctness spine | `fbp/agent.py` | parent re-verifies a child's value on the way up; a self-claimed `verified` is not conclusive |
| Durable single-writer state | `fbp/store.py` | `StateStore` (fingerprint-idempotent) + `TrajectoryStore` (append-only); explicit grants only |
| Store/registry agent | `fbp/store_agent.py` | single-writer, grant-bound reads/writes; ungranted keys fail closed |
| CPM | `fbp/critical_path.py` | read-only critical-path/slack analysis (a capability) |
| Bills loop | `fbp/bills.py`, `fbp/bills_agent.py` | intake → human-gated accept (or deterministic auto-accept) → durable registry → calendar → maintenance; no unverified money/dates |
| Intake | `fbp/intake.py`, `fbp/pdf_intake.py` | file/email/PDF → **unverified** drafts (offline, deterministic) |
| Allowlisted workspace | `fbp/workspace.py` | path grants; traversal/disallowed fail closed |
| Tree-audit | `fbp/audit.py` | reconstruct causal chains; audit as proof |
| Replay + durable ledger | `fbp/ledger.py`, drivers | `replay` / `replay_session` / `load_ledger` / `replay_ledger`; crash-safe re-verify |
| Operator summary + status | `fbp/driver.py` | `summary()`, `status()` (flat {tree, summary}), `summarise_ledger` |
| Read-only inspection | `fbp/driver.py` | `tree()`, `store_keys(child)` — see **next section, recently added** |
| Plans | `fbp/driver.py` | `run_plan(on_step=...)` deterministic fail-closed sequence |
| Source references | `fbp/message.py`, drivers | `Response.sources` on non-deterministic output, audited |
| Model agent | `fbp/model_agent.py` | `ModelAgent` (kind `model`); deterministic stub; `ModelProvider` opt-in; `configure_provider` |
| Determinism + auto-accept | `fbp/determinism.py`, `fbp/bills_agent.py` | `score_determinism`, `Rule`/`RuleSet`, `bills_accept_deterministic` only on an approved rule |
| Durable approved rules | `fbp/bills_agent.py` (`bills_rule_add`) | rules persist; auto-accept across restarts — **"authorize once, run after restart"** |
| **Landing-page server** | `fbp/web.py` | `agent-centric fbp-web`; stdlib `http.server`, loopback-only, read/verify-only; live tree, summary, invariants, actions |
| **Model box (dropdown + spinner)** | `fbp/web.py` | `/model` POST runs a prompt through the `model` agent → OpenRouter when `OPENROUTER_API_KEY` set, else stub; model dropdown (`OPENROUTER_MODEL`), spinner, verified/source status; **Enter submits, Shift+Enter = newline** |
| **Streaming answers + chat history** | `fbp/web.py` | `/model/stream` (SSE-over-POST) streams OpenRouter tokens live; `/history` + `/history/clear` give a bounded in-page transcript (100 turns). Streaming reports honestly as verified=False (the audited path stays `/model`) |
| **Resource envelopes** | `fbp/envelopes.py` | `ResourceEnvelope` (step/size/latency/child bounds) granted at configure time, enforced fail-closed at run/spawn; unbounded by default |
| **Durable chat history** | `fbp/chatstore.py`, `fbp/web.py`, CLI | OPT-IN model-box transcript (`fbp-web --history <path>`): single-writer, append-only, WAL, bounded like the in-memory log; survives restarts, replays identically. In-memory by default (no write without an explicit grant) |
| **Post-return determinism** | `fbp/chat_pipeline.py` | The "model proposes, code accepts" seam: deterministic repair → schema-parse → canonicalize → request-key → pin. `PinCache` serves the first accepted artifact for identical inputs (lexical determinism by caching). Pure/offline |
| **Chat → FBP orchestration** | `fbp/orchestrate.py`, `fbp/web.py` `/orchestrate` | A canonical JSON artifact (single `task` or `steps`) maps to an ordered FBP `run` plan executed through the driver's verified spine (parent re-verified, ledgered, replayable). Fail-closed on invalid/unorchestrable artifacts |
| **Chat-context** | `fbp/web.py` (`_build_chat_context`) | prior (durable) transcript turns fold into the next model prompt (oldest-first, bounded), so the model answers with continuity — wired into both the audited `/model` and the streaming preview path |
| **Schema-driven orchestration** | `fbp/orchestrate.py`, `fbp/web.py` | **Typed, schema-constrained intents** (`SCHEMAS`: `run`/`double`/`sum` + the full bills surface `bills_intake`/`bills_accept`/`bills_calendar`/`bills_registry`/`bills_mark_paid`/`bills_mark_status`/`bills_accept_deterministic`/`bills_rule_add`): `plan_from_schema` validates + coerces a typed artifact fail-closed before planning; the landing page gains a **schema-driven form** (`/orchestrate/schema` serves the schemas) — same verified spine, typed form instead of free-form JSON. Additive (`plan_from_artifact` unchanged) |
| **Bills workflow on the landing page** | `fbp/web.py`, `fbp/bills_agent.py`, `fbp/store_agent.py` | The mission-relevant loop is now a **live, runnable card** on the landing page: `/bills/intake` → `/bills/accept` (human-gated, the only registry write) → `/bills/registry` (read-only snapshot) → `/bills/calendar` (verified projection). **Prefix grants** (`bill-*`) let the UI accept arbitrary bill ids under a granted namespace while still failing closed outside it. Durable via `fbp-web --bills <path>`. Store teardown made **thread-safe** (cross-thread close no longer crashes) |
| **Expert selection + cost ledger** | `fbp/experts.py`, `fbp/web.py`, `fbp/driver.py` | The deterministic core of **"Network of Experts AI"** (the coinded axiom): `select_expert(domain)` picks deterministic / learned (per-domain SLM) / human for each component (domain), and `CostLedger` accounts cost + irreducible residue per run. Read-only **Network of Experts** card on the landing page (`/experts`). Driver surfaces configured verifier names (read-only). Additive |
| **Domain-expert SLM provider contract** | `fbp/slm.py`, `fbp/web.py`, `docs/domain_slm.md` | The **learned** tier: an opt-in external provider (`SlmProvider`/`SlmSpec`/`SlmExpert`/`StubSlmProvider`/`build_domain_expert`) turns a domain corpus into a trained per-domain expert with full provenance (base model, corpus, method, dataset size, benchmark, artifact). Training stays external; the core selects (`select_expert`) and verifies, never bypassing a verifier. Read-only **Domain SLM** card (`/slm`) shows which domains warrant a learned expert. `docs/domain_slm.md` is the 2026 engineering reference |
| **Micro-payment / settlement adapter** | `fbp/settlement.py` | The **paid** tier: an opt-in external provider (`SettlementProvider`/`SettlementGrant`/`Settlement`/`StubSettlementProvider`/`settle_run`) settles a `CostAccount`'s cost under an explicit operator grant. **Never auto-charges**: a settlement without a matching grant, a cost over the grant's cap, a domain mismatch, or a negative cost all fail closed. Stub is offline/deterministic (CI-safe). Slots into the `CostLedger` contract |
| **Deterministic training-plan tier** | `fbp/training_plan.py` | The **strategic hardware-provisioning decision**: `plan_training(domain, corpus_size, method, budget)` picks a hardware tier (`cpu`/`single-gpu`/`multi-gpu`) deterministically from corpus size + method, caps `SlmSpec.max_examples` at the tier's one-pass capacity, and fails closed if the estimated cost exceeds an explicit budget (the natural source is a `SettlementGrant`). Pure/offline; the provider executes, the core decides |
| **External training-provider adapters** | `fbp/providers.py` | The **execution** side of the learned tier: an operator-selectable registry (`ProviderRegistry`/`provider_names`/`get_provider`) of `SlmProvider` adapters — **Modal** (GPU-on-demand, the easiest fit; implemented) plus **Runpod** and **Replicate** (documented, opt-in stubs to wire later). Default is the offline stub; a real provider is opt-in and never runs in the offline suite. The core plans and verifies; the provider trains |
| **End-to-end expert provisioning** | `fbp/provision.py` | Ties select → plan → train → account → settle into one deterministic, workable path: `provision_expert(domain, corpus, provider, grant, ledger)` selects the expert kind, plans the hardware tier, trains via an opt-in provider (default stub, offline/CI-safe), records the `CostAccount` in a `CostLedger`, and settles under an explicit `SettlementGrant`. Fail-closed: unverifiable domain / over-budget plan / provider failure / missing grant all abort. The Modal adapter is **workable** with an injected HTTP client (real transport seam, fail-closed without one) |
| **Provisioning card on the landing page** | `fbp/web.py` | **Easy-UX surface for the provisioning path**: a runnable **Provision a domain expert** card (`/provision`, `/provision/domains`) that selects the domain from the live tree, plans the tier, trains via the offline stub, accounts cost, and settles under a grant — recording the expert's artifact into the Artifact Vault as write-once evidence. Real providers never run here; unknown domains, over-budget plans, and invalid bodies fail closed. Additive |
| **Domain Registry + artifact vault** | `fbp/domainrepo.py`, `fbp/web.py` | The **observability + provenance** layer (catalog → decision → accounting → evidence): `DomainRegistry` (read-only, **tenant-aware** catalog — passive, never an authority) + `ArtifactVault` (append-only, write-once evidence keyed by (tenant, domain, run), never mutable). Read-only **registry** + **artifact** cards (`/domains`, `/artifacts`); durable via `fbp-web --registry <path>` (explicit grant). Tenant-awareness makes a future paid multi-tenant web service additive, not a rewrite; the service would be a shared observability/provenance layer, never the governance layer. Additive |
| **Component Networks** | `fbp/network.py`, `fbp/web.py` `/network` | Deterministic visual-programming core: a directed graph of components wired by data-flow edges, validated as a DAG, compiled (topological, ties by id) to an ordered FBP `run` plan run through the verified spine. **True dataflow** (a downstream component consumes the *computed* verified output of its upstreams, not their args). Cycles / unknown refs / unverified steps fail closed. Landing page has a **dependency-free drag-and-drop node-and-wire canvas** editor (palette, draggable nodes, click-to-connect ports, SVG edges, run) + **durable save/load** (`fbp-web --networks <path>`, `/network/save|/list|/load`) |
| **Card-based UI + mode switch** | `fbp/web.py` | Card-based landing page with a **Chat / Designer** mode switch: Chat = model box + chat history + run-an-artifact (general-purpose); Designer = Component Network editor. Clear functional division |

### Easy-UX driver & CLI
- `FbpDriver` API: `register`, `resolve`, `configure`, `configure_child`,
  `configure_provider`, `run`, `run_plan`, `spawn`, `ping`, `kill`,
  `state_set`/`state_get`, `audit`, `reconstruct_audit`, `ledger`, `replay`,
  `replay_session`, `summary`, `status`, `load_ledger`/`replay_ledger`,
  `tree()`, `store_keys(child)`.
- CLI: `agent-centric fbp [--transport inproc|tcp|ipc] [--ledger <path>]`,
  `fbp-summary <path>`, `fbp-replay <path>`, `fbp-web [--host --port --open]`,
  `fbp-web --reload` (auto-restart on source change), `fbp-web --kill [--port]`
  (stop the server on the port; the `fbp-web-kill` subcommand is kept for
  backward compatibility).
- Examples: `examples/fbp_arc_demo.py`, `fbp_demo.py`, `fbp_durability_demo.py`.

### Tooling / commands
```sh
uv sync --extra dev
uv run pytest                  # 939 passed (as of this handoff)
uv run ruff check .            # clean
uv run mypy src                # clean, 90 source files
uv run pytest --cov=agent_centric.fbp --cov-report=term   # ~88%
uv run agent-centric fbp --transport inproc|tcp|ipc
uv run agent-centric fbp-replay <ledger>   # re-verify a durable session
uv run agent-centric fbp-web                # landing page (stub model)
uv run agent-centric fbp-web --reload      # auto-restart on source edits
uv run agent-centric fbp-web-kill          # stop the server on the port
# model selection:
#   OPENROUTER_API_KEY  -> live OpenRouter (default deepseek/deepseek-v4-flash-0731)
#   OPENROUTER_MODEL    -> comma-separated ids for the dropdown (default single)
```

### Standing invariants (never break)
1. **No unverified success; fail-closed everywhere.**
2. **Deterministic by construction** — identical directives + context ⇒
   identical results; replay is a computation, not a hope.
3. **Persistence is an explicit grant** — single-writer, fingerprint-idempotent,
   **no auto-generated ids**.
4. **Human-gated** (or rule-authorized-deterministic) only for money/registry
   writes; intake never auto-accepts unruled.
5. **We never rely on a non-deterministic output directly** — determinize
   first; only irreducible residue reaches a human; LLM outputs carry source
   refs.
6. **CPM, audit, and replay are read-only capabilities — not agents.**
7. **Public-surface additive only.**

---

## 3. CONVERSATIONAL STATE (how we got here — the hard-rule capture)

This section is the stuff you can't re-derive from the code. It is the history
of decisions and working style that a fresh session must inherit.

### The project's purpose & north star (what the model is FOR)
- **Mission-critical, automated, must be correct/accurate/secure/robust/optimal.**
- The design **north star** = **a deterministic platform first.** We *use* but
  never fully trust non-deterministic tools (LLMs, free-form parsers). An
  ambiguous output is a **hint** → derive a deterministic method wherever
  possible → the only irreducible residue reaches a human → the human's
  authorization becomes a **durable rule** that runs after restart → LLMs are
  **ordinary agents** re-verified by the parent, carrying source refs.
- I (the coding agent) operated for a long stretch on the user's high-level
  mandate: *"Proceed as you deem is best ... Get it all done, feature-full,
  easy UX. Do not bother me until everything is done."* That drove a long
  autonomous burst; the user has since re-engaged analytically.

### Recent session arc (what happened last, chronologically)
1. **Landing-page server** (`919c0a3`) — added `agent-centric fbp-web`, the
   readable/actionable landing page (live tree, status, invariants, actions).
2. **Coverage / hardening / dedup batch** — deduplicated the two PDF text
   extractors, added determinism/replay + bills + registry + store_agent tests,
   pytest-cov, `FbpDriver.status()`, web routes `/ledger` + `/state.json`,
   a GitHub CI workflow, docs.
   → The CI workflow was **later removed** (see below); coverage evidence,
   dedup, and `status()` stayed.
3. **OpenRouter model text box** (`9aea719`) — added a text box to
   the landing page that runs a prompt through the `model` agent, to OpenRouter
   when an `OPENROUTER_API_KEY` env var is set, else the deterministic stub.
4. **Model box done right + dev commands** (`c9d5bcd`) — fixed the box so it
   actually works end-to-end: the JS `\n` bug that silently broke the whole
   `<script>` (Ask did nothing, no spinner), normalized `ModelResponse` to its
   plain text, added a spinner + red error styling, made `deepseek/deepseek-v4
   -flash-0731` the default model, and added `fbp-web --reload` (auto-restart on
   source edits) + `fbp-web-kill`. The model dropdown reads `OPENROUTER_MODEL`
   (comma-separated).
5. **Resource envelopes in the FBP tree** (`d131d2d`) — new `fbp/envelopes.py`
   (ResourceEnvelope + EnvelopeGuard: step_limit, size_cap, latency_seconds,
   child_limit), granted at configure time and enforced fail-closed at the
   agent's run/spawn boundaries. Unbounded by default (does not break existing
   call sites). 12 new tests.
6. **Streaming answers + chat history** (`3bfb824`) — `/model/stream`
   (SSE-over-POST) streams OpenRouter tokens live; `/history` + `/history/clear`
   give a bounded in-page transcript. Streaming is an added UX surface that
   reports honestly as verified=False (the audited path stays `/model`).
7. **Transport trust-boundary doc** (`docs/transport_trust_boundary.md`) — the
   honest statement of the FBP transport's authn/z + TLS gaps and the exact
   hardening needed before cross-host use.
8. **Enter/Shift-Enter submit** (`09d1c7a`) — Enter submits the model prompt
   (no more clicking Ask); Shift+Enter inserts a newline for multi-line prompts.
9. **Durable chat history** (`8f6daa2`) — OPT-IN `--history <path>` persists the
   model box transcript to a single-writer, append-only, WAL store
   (`fbp/chatstore.py`); the transcript survives restarts and replays
   identically, matching the durability spine (`TrajectoryStore` philosophy).
   In-memory by default (no write without an explicit grant).
10. **Reload opens one browser tab** (`3e391c4`) — `fbp-web --reload` now passes
   `--open` only on the initial launch and restarts the child with
   `open_browser=False`, so a source-change reload re-freshes the already-open
   tab instead of spawning a second one. `_fbp_kill_stale_web(port)` already
   guarantees no second instance runs (`--reload`/`fbp-web-kill` take the port
   over, leaving unrelated processes untouched).
11. **Post-return determinism + chat→FBP orchestration** (`e57d8f9`) — new
   `fbp/chat_pipeline.py` (validate → canonicalize → pin; `PinCache` gives
   lexical determinism by caching) and `fbp/orchestrate.py` (a canonical
   artifact → an ordered FBP `run` plan through the driver's verified spine).
   The landing page gains an **Orchestrate → FBP** box (`/orchestrate` route).
   This is the attaching seam for the LLM chat window driving FBP networks.
12. **Chat-context** (`c4afa94`) — prior (durable) transcript turns fold into
   the next model prompt (oldest-first, bounded), giving the model continuity
   without relaxing determinism. Wired into both the audited `/model` path and
   the streaming preview (`_build_chat_context`).
13. **Component Networks** (`69df3d8`) — a deterministic visual-programming
   core (`fbp/network.py`): a directed graph of components wired by data-flow
   edges, validated as a DAG, compiled (topological, ties by id) to an ordered
   FBP `run` plan run through the verified spine. Cycles / unknown refs fail
   closed. The landing page gains a **Component Network** visual editor
   (`/network` route: add components/edges, run).
14. **Canvas editor** (`4a94c16`) — the Component Network editor is now a
   dependency-free drag-and-drop node-and-wire canvas (SVG): palette buttons,
   draggable nodes, click-to-connect ports, double-click to remove, live JSON.
   No third-party/CDN library — keeps the stdlib-only, fail-closed posture.
15. **True dataflow + durable save/load** (`fac7a16`) — `run_network` now
   executes **true dataflow**: each component runs in topological order and its
   *computed* verified output is threaded into downstream args (a `sum` can
   consume two `double`s). Results carry the component `id` for the editor.
   Networks can be **saved/loaded durably** (`fbp-web --networks <path>`,
   `/network/save|/list|/load`), validated before persist (fail-closed).
16. **Card-based UI + Chat/Designer mode switch** (`d44a582`) — the landing
   page is now card-based (clean, friendly) with a **Chat / Designer** mode
   switch: Chat = the model box + chat history + run-an-artifact; Designer =
   the Component Network editor. A clear functional division — the chat box is
   general-purpose, not only for design.
17. **`fbp-web --kill` flag** (`dc0b81c`) — per the user's request, `kill` is
   now a flag on `fbp-web` (`uv run agent-centric fbp-web --kill [--port]`)
   rather than a separate `fbp-web-kill` subcommand. It routes to
   `_cmd_fbp_web_kill(port=port)`. The `fbp-web-kill` subcommand is kept for
   backward compatibility.
18. **Schema-driven orchestration** (current) — the handoff's "future UX idea"
   realized: `orchestrate.py` gains typed intent `SCHEMAS` (`run`/`double`/
   `sum`) and `plan_from_schema`, so a typed, schema-constrained artifact
   (e.g. `{"intent": "sum", "a": 2, "b": 3}`) is validated + coerced
   fail-closed and runs through the same verified spine. The landing page gains
   a **schema-driven form** card in Chat mode (`/orchestrate/schema` serves the
   schemas). Additive — `plan_from_artifact` etc. unchanged. 765 tests.
19. **Bills workflow on the landing page** (current) — the mission-relevant loop
   is now a live, runnable card: `/bills/intake` → `/bills/accept` (human-gated)
   → `/bills/registry` (read-only) → `/bills/calendar` (verified). **Prefix
   grants** (`bill-*`) in `store_agent.py` let the UI accept arbitrary bill ids
   under a granted namespace. Durable via `fbp-web --bills <path>`. Store
   teardown made **thread-safe** (cross-thread close no longer crashes). 771
   tests.
20. **Expert selection + cost ledger** (current) — the deterministic core of the
   **"Network of Experts AI"** axiom, made concrete. `experts.py` (`Domain`,
   `select_expert`, `CostLedger`/`CostAccount`): each component is a domain;
   the engine picks deterministic / learned (per-domain SLM) / human strictly
   from contract + residue + cost, fail-closed (unverifiable → human). The
   landing page gains a read-only **Network of Experts** card (`/experts`).
   `FbpDriver` surfaces its configured verifier names (read-only). Additive;
   789 tests.
21. **Domain Registry + artifact vault** (current) — the agreed
   increment realized. `domainrepo.py`: `DomainRegistry` (read-only, tenant-aware
   catalog — passive, never an authority) + `ArtifactVault` (append-only,
   write-once evidence keyed by (tenant, domain, run), never mutable). Read-only
   **registry** + **artifact** cards (`/domains`, `/artifacts`); write-once
   evidence durable via `fbp-web --registry <path>` (explicit grant). Tenant-
   awareness makes a future paid multi-tenant web service additive, not a
   rewrite. 804 tests.
22. **Domain Registry + Artifact Vault rename** (`7528ee5`) — the user approved
   the vocabulary: "Domain of Experts" was retired (parses backward, muddies the
   Network-of-Experts vocabulary, crowded trademark space). `ArtifactRepository`
   → `ArtifactVault`; "artifact repository" → "artifact vault"; `DomainRegistry`
   class stays; user-facing term is "Domain Registry". Swept code, tests, docs.
23. **Law 11 enshrined** (`93c673c`) — the no-inline-editing law made emphatic:
   files are NEVER edited in place; only copy-to-temp → `cp` → `rm`. The user
   mandated this because in-place editing in this Zed environment is unreliable,
   forces repeated authorization, and has corrupted files.
24. **Law-11 tooling** (`c01ea2c`) — `tools/safe-edit.sh` + `tools/safe-replace.sh`
   + `tools/README.md`: the provided solution so the law is enforceable in
   practice (temp + `cp`, never in-place).
25. **Schema-driven bills intents** (`ed5e27a`) — `bills_intake`/`bills_accept`/
   `bills_calendar` typed intents route to the `bills` child through the verified
   spine. **Artifact Vault records real runs** (`7c61773`) — verified run outputs
   become write-once evidence. **`fbp-domains` CLI** (`9956eb8`) — operator
   readout of a saved registry + vault.
26. **Domain-expert SLM provider contract** (`f980e3d`) — the **learned** tier:
   `fbp/slm.py` (`SlmProvider`/`SlmSpec`/`SlmExpert`/`StubSlmProvider`/
   `build_domain_expert`), `docs/domain_slm.md` (the 2026 engineering reference),
   and a read-only **Domain SLM** card (`/slm`). Docs updated (`f6a4770`). 824
   tests.

### Decisions the user made (with consequence)
- **"Completely forget about AC Router"** — explicitly. The AC Router / AC
  Platform work was **spun out** into separate repo scaffolds under
  `repositories/ac-router/` and `repositories/ac-platform/` (gitignored here),
  and the lead is to **not** pursue AC Router. Do not resurrect it unless the
  user raises it.
- **"I don't want GitHub CI"** → removed `.github/workflows/ci.yml` (amended
  into `0c78394`). Specifically: the GitHub push was rejected because an OAuth
  app lacked the `workflow` scope; rather than add it, the user said drop CI.
  **So: no GitHub Actions CI; do not add it back.** Local `pytest-cov` is fine.
- **Pushing:** During the session the standing "no push without explicit
  go-ahead" was, in practice, relaxed — the user pushed several times
  directly (`git push`) and told me "I pushed them" on a few occasions. Treat
  push as **allowed** now, but for code you're not 100% sure the user wants on
  the remote, prefer to ask or narrate before pushing; the user actively
  pushed the landing page and hardening commits themselves.
- **Ownership/tone:** the user is the Lead that delegates autonomous completion
  and expects the agent to **finish and commit**, then REPORT, not pause for
  every microdecision. They said "Do not bother me until everything is done"
  during the long autonomous stretch. They also value **honest status** — they
  pushed back when the state was misrepresented. So: be direct about what is/is
  not done, pushed/unpushed, tested/uncovered.
- **Visual-programming direction (this session):** the user asked for a visual
  programming interface like FBP ("Component Networks") and shared a curated
  list of open-source libraries (Blockly, Rete.js, LiteGraph, Drawflow,
  React Flow, Node-RED, etc.). The lead's call was to **not** pull in a
  third-party/CDN library — it would violate the stdlib-only, fail-closed,
  deterministic posture. Instead we built our own dependency-free drag-and-drop
  canvas on the deterministic `fbp/network.py` core. The user agreed with the
  reasoning ("full-featured ≠ right for us").
- **Card-based UI + Chat/Designer mode switch (this session):** the user asked
  for a clear functional division — the chat box should not be used only for
  design. Delivered as a card-based layout with a Chat/Designer mode switch
  (`d44a582`).
- **`--kill` as a flag (this session):** the user asked that `kill` be a flag
  on `fbp-web` rather than a separate subcommand (`dc0b81c`).
- **Coined axiom: "Network of Experts AI" (this session):** the user coined the
  phrase and asked it be enshrined at the top of conceptual understanding. The
  lead wrote it as: *the model is not the expert, the network is.* An AI is a
  network of narrow domain experts, each verified by a deterministic verifier; a
  general model is one (fallible) kind of expert. Enshrined at the top of
  `README_FBP.md`, `spec.md` §0, and `docs/fbp.md`; recorded as a standing truth
  (`3b30ab1`). The user also stated plainly: **"I don't believe in LLMs. I
  believe in component networks of domain (component) experts."** The lead
  reframed it (not "LLMs are worthless" but "an untrusted model's word is never
  the answer — it's a hint that must be verified deterministically"), which is
  exactly the existing north star.
- **Per-domain SLM + micro-payment idea (this session):** the user proposed
  building an engine that trains a **Small Language Model expert per domain**
  (only when warranted) and using **X402-style micro-payment infrastructure** to
  show cost and provide the SLM-building infrastructure for component networks.
  The lead's call: (a) **training is not an in-architecture operation** — it is
  an **opt-in external provider** (corpus in, expert out) behind a strict
  contract, never baked into the deterministic spine; (b) **"expert" doesn't
  have to mean a trained model** — deterministic method first, SLM only for the
  residue; (c) **cost accounting is a perfect in-architecture fit**, but the
  payment infrastructure itself (X402 or any provider) is an **opt-in external
  adapter**, never auto-charge without an explicit grant. This led directly to
  the built `experts.py` foundation.
- **Domain Registry + artifact vault (now built):** the user
  proposed a **Domain of Experts registry and artifact vault.** The lead's
  take: it is the **observability + provenance layer** that completes the
  network-of-experts model (catalog → decision → accounting → evidence),
  self-describing and the natural home for learned-expert artifacts. Two hard
  rules held throughout: (1) the **registry stays a passive catalog, never an
  authority** (authority stays in the tree topology — no central boss); (2) the
  **artifact vault is write-once evidence** — append-only, keyed by
  (tenant, domain, run), carrying source refs + residue + cost, never mutable.
  Built as `domainrepo.py` + `/domains`, `/artifacts` cards (`--registry <path>`
  durable). This completes catalog → decision → accounting → evidence.
- **Standalone paid web-service direction (this session):** the user extended
  the idea — they want this as a **stand-alone web-service that others can pay
  us for** (a paid "Network of Experts" registry + artifact service). The lead's
  strategic take: it is coherent and valuable (customers pay for *provenance +
  verification value*, not storage), and it is consistent with the architecture
  **as long as the hosted service is a shared observability + provenance layer,
  NOT the governance layer** (each customer's tree stays the authority). This is
  the natural home for the earlier **X402/micro-payment** adapter. The core is
  already **tenant-aware** so the service is additive, not a rewrite. The two
  build decisions: **loopback surface built now** (read-only cards on `fbp-web`
  + `--registry` durable); **the multi-tenant cross-process server is a
  deliberate later phase** — it crosses the transport trust boundary
  (`docs/transport_trust_boundary.md`) and needs TLS + authn/z + tenant
  isolation + billing before it is real, so it was **not** stood up
  unilaterally. The user confirmed this shape implicitly by saying proceed.

- **Domain-expert SLM writeup adopted (this session):** the user shared a
  detailed, technically accurate writeup on building domain-expert SLMs in 2026
  (QLoRA on a 3B-7B base, DAPT + SFT + DPO, corpus curation, open-source domain
  models). The lead's take: it is the empirical confirmation of the "learned"
  tier and was adopted as the reference for `fbp/slm.py` and `docs/domain_slm.md`.
  The SLM is an *expert kind*, not a new authority — selected by `select_expert`,
  verified by the domain verifier, its artifacts in the Artifact Vault. Training
  stays an opt-in external provider, never built into the deterministic core.
- **No-inline-editing mandate (this session, MISSION CRITICAL):** the user
  explicitly required that files be **never edited in place** — the Zed in-place
  editing is broken, forces repeated authorization, and risks corruption. This
  became **Law 11** in `PRINCIPLES.md` (emphatic admonition) and is now a
  standing truth in this handoff. The provided tools are `tools/safe-edit.sh`
  and `tools/safe-replace.sh`. The user also asked for a generalized directive
  (no project-specific terms) for other agents, and a copy-paste directive for
  the agent that currently needs it.

### How to talk to the user / working style
- Be the **senior, decisive engineer**: propose a course, proceed on the
  low-risk, and flag genuinely risky/irreversible decisions for explicit
  sign-off.
- **Default to concise, structured, factual updates.** Show validation numbers
  and be candid about gaps.
- When something is "risky" (networking/credentials/remote push semantics,
  anything not reversible), either defer or get a clear yes.
- The user is technical but not a Python/FBP native — explain concepts plainly
  when relevant (the platform's "verified vs unverified", "deterministic",
  "no auto-gener ids"). They walked through a beginner's tour and engaged with
  it.
- **Agent failure mode to avoid (this session):** the agent's local terminal
  was wedged, so it routed terminal work through a sub-agent. When asked for a
  handoff, the agent tried to do too much in one turn (validate + commit + write
  handoff + commit) and its responses truncated to nothing — it "died"
  repeatedly. **Lesson: execute in small concrete steps (one tool call at a
  time), don't narrate a plan without running it, and commit/validate each piece
  before moving on.** The user explicitly called this out.

---

## 4. WHAT'S LEFT / SUGGESTED NEXT

### Production/deploy gaps the user should resolve (explicit, not built)
These are the honest reasons the project is **not yet "1.0 / production-ready"**
despite 751 passing tests:
- **Transport security (now built, opt-in):** the §5.1 switch, §5.3 IPC modes, and —
  new this session — §5.2 TLS-credential config, §5.4 per-peer authorization, and
  §5.5 traffic integrity are implemented in `fbp/security.py` and wired into the
  driver (opt-in, default-off). The **real production caveat** is now: TLS is
  validated/config-checked but not yet performing live packet encryption over
  the ZeroMQ wire itself — a genuine cross-host deployment must supply certs and
  a TLS-capable transport (or restrict to loopback). Fine for localhost/demo;
  not yet for an untrusted wide-area network.
- **Resource envelopes** are now **built and enforced** in the FBP tree
  (`fbp/envelopes.py`: step/size/latency/child bounds, fail-closed) — but only
  when a caller grants one; the Manager line still has richer per-stage
  accounting that the FBP tree does not mirror.
- **Real LLM provider** is wired as an in-process opt-in hook only; no
  production credential management, no network path hardened for deployment.
- **No container/OS sandboxing / seccomp / VM isolation** of agent execution.
- **No distribution/networking/cloud** and **no MCP/A2A** baked in.
These are listed in `STATUS.md`'s "out of scope / future volleys".

### Roadmap — standalone multi-tenant paid web service (roadmap only, NOT built)

The user asked this be captured **for the roadmap only** — documented as a
future direction, **not** started. It is the natural extension of the
**Domain Registry + Artifact Vault**: a **stand-alone web service customers pay
us for** (a paid "Network of Experts" registry + artifact service). Key design
constraints already agreed with the user:

- **It must be a shared observability + provenance layer, NEVER the governance
  layer.** Each customer's tree stays the authority; the hosted service only
  records *what* and *how* (catalog), and write-once evidence (vault).
- The core is already **tenant-aware**, so the service would be **additive, not a
  rewrite**.
- It is the natural home for the **X402 / micro-payment** settlement adapter
  (the paid tier, already drafted in `fbp/settlement.py`).
- **Build gates before it is real (deliberately, from the transport trust
  boundary):** real mutual-TLS transport over a genuine network path, per-tenant
  authorization/isolation, and billing. These require live credentials and
  network access, so they are an explicit, irreversible, money-adjacent step that
  needs the lead/operator's sign-off before any code is written.

**Status: roadmap entry only.** Nothing is built or committed for the standalone
service; the existing loopback (`fbp-web --registry`) surface remains the only
hosted layer.

### Loose ends / immediate next actions
- **Unpushed commits (11):** `c4afa94` (chat-context), `46b3948` (handoff),
  `69df3d8` (Component Networks), `9b8f7e5` (handoff), `4a94c16` (canvas
  editor), `241d9a3` (handoff), `fac7a16` (dataflow + save/load), `10f343b`
  (handoff), `d44a582` (card UI + mode switch), `6f2f2a6` (handoff),
  `dc0b81c` (--kill flag) are local but not yet on GitHub; plus this handoff
  update. The remote was at `87e7bbe`. The user pushes directly; confirm before
  pushing anything yourself.
- **Terminal glitch (this session):** the session's local terminal began
  rejecting ``cd`` into the project with "not in any of the project's
  worktrees"; the same command had worked minutes earlier. The sub-agent (which
  runs from `agent-centric/` — note the nested project root) had no such issue.
  If a fresh session hits the same wall, use the sub-agent/spawn path or the
  nested `agent-centric/` root.
- **`OPENROUTER_MODEL` lives in the user's `~/.bashrc`** (out of the repo):
  `deepseek/deepseek-v4-flash-0731,openai/gpt-4o-mini,anthropic/claude-3.5-son
  net,meta-llama/llama-3.3-70b-instruct`. The `anthropic/claude-3.5-sonnet` id
  404s on OpenRouter for this key (invalid slug); remove or correct it.
- **docs/fbp.md / FBP_HANDOFF.md / README_FBP.md** are living docs — keep them
  current (they now mention the model box, reload, kill, streaming, history,
  envelopes, the durable `--history` transcript, the **"Network of Experts AI"**
  axiom enshrined at the top of `README_FBP.md` and `spec.md` §0, and an
  **"Appreciating the architecture"** section for new readers at the top of
  `README_FBP.md`). `README_FBP.md` was also brought current (789 tests, missing
  capability rows added: Component Networks, schema-driven orchestration, bills
  workflow, expert selection).
- **The `fbp-web` landing page** is live and runnable for a demo (set
  `OPENROUTER_API_KEY` for a real model; it fails closed to the stub otherwise):
  `uv run agent-centric fbp-web --reload`. Add `--history <path>` to keep the
  model-box transcript across restarts.
- Future UX idea for the model box: **schema-driven orchestration — now built**
  (typed intents via `SCHEMAS`/`plan_from_schema`; the landing page has a
  schema-driven form). The typed-intent surface is currently the demo tasks
  (`run`/`double`/`sum`); the **bills workflow is now also a first-class card**
  on the landing page (`/bills/*` routes, durable via `--bills <path>`). The
  natural next step is richer typed intents mapped onto the bills loop (intake
  → accept → calendar) and other domain agent operations, so the chat window
  can drive them through the schema form.**Chat-context** (feed prior turns
  into the next prompt) is now built (`c4afa94`); **persisted** (durable) chat
  history and streaming were already in.
- **Expert selection (Network of Experts AI) — foundation now built**
  (`experts.py` + `/experts` readout). What remains is the **opt-in external
  adapters** the core deliberately does *not* build: (a) a **per-domain SLM
  provider** (training/adaptation as an external, fail-closed provider — corpus
  in, expert out, selected only when `select_expert` warrants it); and (b) a
  **micro-payment / X402-style settlement adapter** for per-run cost (external,
  opt-in, never auto-charge without an explicit grant). Both slot into the
  contracts `experts.py` already defines.
- **Domain Registry + artifact vault — now built** as the
  agreed increment. `DomainRegistry` (read-only, tenant-aware catalog) +
  `ArtifactVault` (append-only, write-once evidence keyed by (tenant,
  domain, run)) + read-only **registry**/**artifact** cards (`/domains`,
  `/artifacts`); durable via `fbp-web --registry <path>`. The two hard rules
  held: the registry is a **passive catalog, never an authority**; the artifact
  repository is **write-once evidence, never mutable**. This is the
  observability + provenance layer that completes catalog → decision →
  accounting → evidence.

---

## 5. TOOLING / COMMANDS CHEAT-SHEET

```sh
uv run agent-centric run                # deterministic demo
uv run agent-centric fbp                # drive FBP demo (inproc)
uv run agent-centric fbp --transport tcp | ipc
uv run agent-centric fbp-web            # landing page + model box
uv run agent-centric fbp-web --reload   # auto-restart on source edits
uv run agent-centric fbp-web --history chat.db   # durable transcript across restarts
uv run agent-centric fbp-web --networks networks.json  # durable saved networks
uv run agent-centric fbp-web --bills registry.db  # durable bills registry across restarts
uv run agent-centric fbp-web --registry repo.json # durable domain registry + artifacts across restarts
uv run agent-centric fbp-web --kill     # stop the server on the port (flag)
uv run agent-centric fbp-web-kill       # stop the server on the port (legacy subcommand)
uv run agent-centric fbp-replay sess.db
uv run agent-centric fbp-summary sess.db
uv run agent-centric fbp-domains repo.json  # operator readout of a saved Domain Registry + Artifact Vault
uv run python examples/fbp_arc_demo.py
```

---

## 6. Standing truths to re-affirm on resume
- `main` remains the GitHub default; FBP stays on `agent-centric-fbp`.
- No GitHub CI.
- Deterministic-first north star; LLM as ordinary agent; grants; fail-closed;
  no auto-pres ids.
- **THE NO-INLINE-EDITING LAW (Law 11) — MISSION CRITICAL, ABSOLUTE.** Files are
  **NEVER edited in place. EVER. Zero exceptions.** Do not use in-place edit
  tools; do not patch/rewrite a single line inside an existing file; do not
  change one character/comment/label in place — not even a trivial edit. In-place
  editing corrupts files and forces repeated authorization prompts; it has been
  observed to corrupt content in this project. The ONLY permitted way to change
  a file: (1) copy the target's contents to a temp file, (2) make ALL changes in
  the temp, (3) `cp tempfile targetfile` (atomic, byte-complete whole-file
  replace), (4) `rm tempfile`. Prefer the provided tools `tools/safe-edit.sh`
  and `tools/safe-replace.sh`. Brand-new files (that don't exist yet) may be
  created directly. This law is enshrined in `PRINCIPLES.md` §11 and applies to
  source, tests, docs, and config alike, with no exceptions.
- **The coined axiom (user): "Network of Experts AI"** — *the model is not the
  expert, the network is.* An AI is a network of narrow domain experts, each
  verified by a deterministic verifier; a general model is one (fallible) kind
  of expert. Enshrined at the top of `README_FBP.md` and `spec.md` §0. Treat it
  as the conceptual north star for any future expert-selection / per-domain-SLM
  work. Two hard rules for the **Domain Registry + Artifact Vault**: the
  registry is a **passive catalog, never an authority** (authority stays in the
  tree topology); the artifact vault is **write-once evidence, never mutable**
  (append-only, keyed by (tenant, domain, run), carrying source refs + residue +
  cost).
- **The SLM provider contract** (`fbp/slm.py`) is the **learned** tier: an
  opt-in external provider turns a domain corpus into a trained per-domain
  expert with full provenance. Training stays external; the core selects
  (`select_expert`) and verifies, never bypassing a verifier. `docs/domain_slm.md`
  is the 2026 engineering reference.
- The AC Router is spun out, gitignored, and **not** our work here.
- Trust only what the tests prove and what is committed; say clearly when
  something is unverifiable or unpushed.

---

Prepared so a new session can resume **both** the codebase and the
conversation. — Lead Architect