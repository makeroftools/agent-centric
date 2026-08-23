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
- **HEAD:** `dc0b81c` (fbp-web --kill flag). **Pushed to origin:** up through
  `87e7bbe` (the user pushed). **Unpushed (11 commits):** `c4afa94`
  (chat-context), `46b3948` (handoff), `69df3d8` (Component Networks),
  `9b8f7e5` (handoff), `4a94c16` (canvas editor), `241d9a3` (handoff),
  `fac7a16` (dataflow + save/load), `10f343b` (handoff), `d44a582` (card UI +
  mode switch), `6f2f2a6` (handoff), `dc0b81c` (--kill flag). Run
  `git log origin/agent-centric-fbp..HEAD` to see the unpushed set.
- `main` stays the GitHub default and is **fully contained** in this branch.
- Standing rule in effect for a long time: **do not push unless the lead
  explicitly says push.** (The lead has since been pushing directly themselves;
  see the push decision note below — the "no push" rule has effectively been
  relaxed, but confirm each time for a given commit.)

### Validation (run this session, all live)
- `uv run pytest` → **751 passed** (was 749; added card/mode-switch render tests)
- `uv run ruff check .` → clean
- `uv run mypy src` → clean (**81 source files**)
- FBP coverage (`uv run pytest --cov=agent_centric.fbp --cov-report=term`) →
  **~88% total**; key files: registry 97%, bills 94%, store 93%, store_agent
  89%, web 83%.
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
uv run pytest                  # 674 passed (as of this handoff)
uv run ruff check .            # clean
uv run mypy src                # clean, 77 source files
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
- **Transport security (documented, not built):** over `tcp`/`ipc` the
directive/response protocol is **unauthenticated** — no TLS, no authn/z. The
trust boundary is now documented in `docs/transport_trust_boundary.md`
(§4-6 list the exact hardening needed). Fine for localhost/demo; not across a
real trust boundary.
- **Resource envelopes** are now **built and enforced** in the FBP tree
  (`fbp/envelopes.py`: step/size/latency/child bounds, fail-closed) — but only
  when a caller grants one; the Manager line still has richer per-stage
  accounting that the FBP tree does not mirror.
- **Real LLM provider** is wired as an in-process opt-in hook only; no
  production credential management, no network path hardened for deployment.
- **No container/OS sandboxing / seccomp / VM isolation** of agent execution.
- **No distribution/networking/cloud** and **no MCP/A2A** baked in.
These are listed in `STATUS.md`'s "out of scope / future volleys".

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
  envelopes, and the durable `--history` transcript).
- **The `fbp-web` landing page** is live and runnable for a demo (set
  `OPENROUTER_API_KEY` for a real model; it fails closed to the stub otherwise):
  `uv run agent-centric fbp-web --reload`. Add `--history <path>` to keep the
  model-box transcript across restarts.
- Future UX idea for the model box: schema-driven orchestration (let the chat
  window emit a *typed*, schema-constrained artifact that maps to richer FBP
  intents, e.g. bills intake → accept). **Chat-context** (feed prior turns into
  the next prompt) is now built (`c4afa94`); **persisted** (durable) chat
  history and streaming were already in.

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
uv run agent-centric fbp-web --kill     # stop the server on the port (flag)
uv run agent-centric fbp-web-kill       # stop the server on the port (legacy subcommand)
uv run agent-centric fbp-replay sess.db
uv run agent-centric fbp-summary sess.db
uv run python examples/fbp_arc_demo.py
```

---

## 6. Standing truths to re-affirm on resume
- `main` remains the GitHub default; FBP stays on `agent-centric-fbp`.
- No GitHub CI.
- Deterministic-first north star; LLM as ordinary agent; grants; fail-closed;
  no auto-pres ids.
- The AC Router is spun out, gitignored, and **not** our work here.
- Trust only what 751 tests prove and what is committed; say clearly when
  something is unverifiable or unpushed.

---

Prepared so a new session can resume **both** the codebase and the
conversation. — Lead Architect