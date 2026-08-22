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
- **HEAD:** `9aea719` = `feat(fbp): OpenRouter model text box on the landing page`.
- **Pushed to origin:** up through `0c78394`. **Unpushed (1 commit):** `9aea719`
  (the OpenRouter text box). Run `git log origin/agent-centric-fbp..HEAD`.
- `main` stays the GitHub default and is **fully contained** in this branch.
- Standing rule in effect for a long time: **do not push unless the lead
  explicitly says push.** (The lead has since been pushing directly themselves;
  see the push decision note below — the "no push" rule has effectively been
  relaxed, but confirm each time for a given commit.)

### Validation (run this session, all live)
- `uv run pytest` → **649 passed**
- `uv run ruff check .` → clean
- `uv run mypy src` → clean (**76 source files**)
- FBP coverage (`uv run pytest --cov=agent_centric.fbp --cov-report=term`) →
  **88% total**; key files: registry 97%, bills 94%, store 93%, store_agent
  89%, web 83%.
- Cross-transport durable replay: 19/19 on inproc/ipc/tcp. Full production-arc
  demo: crash-safe replay 6/6.

### What's built and tested (the full agent-centric FBP capability surface)
Everything in the capability table below is real code, committed, and exercised
by tests — the deterministic platform first, LLMs as ordinary agents, the bills
loop, durable single-writer state, replay/audit, a landing-page server, and now
an OpenRouter-backed model text box on that page:

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
| **OpenRouter model text box** | `fbp/web.py` | `/model` POST route runs a prompt through the `model` agent → OpenRouter when `OPENROUTER_API_KEY` set, else deterministic stub |

### Easy-UX driver & CLI
- `FbpDriver` API: `register`, `resolve`, `configure`, `configure_child`,
  `configure_provider`, `run`, `run_plan`, `spawn`, `ping`, `kill`,
  `state_set`/`state_get`, `audit`, `reconstruct_audit`, `ledger`, `replay`,
  `replay_session`, `summary`, `status`, `load_ledger`/`replay_ledger`,
  `tree()`, `store_keys(child)`.
- CLI: `agent-centric fbp [--transport inproc|tcp|ipc] [--ledger <path>]`,
  `fbp-summary <path>`, `fbp-replay <path>`, `fbp-web [--host --port --open]`.
- Examples: `examples/fbp_arc_demo.py`, `fbp_demo.py`, `fbp_durability_demo.py`.

### Tooling / commands
```sh
uv sync --extra dev
uv run pytest                  # 649 passed (as of this handoff)
uv run ruff check .            # clean
uv run mypy src                # clean, 76 source files
uv run pytest --cov=agent_centric.fbp --cov-report=term   # ~88%
uv run agent-centric fbp --transport inproc|tcp|ipc
uv run agent-centric fbp-replay <ledger>   # re-verify a durable session
uv run agent-centric fbp-web --open        # landing page (+ model text box)
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
3. **OpenRouter model text box** (`9aea719`, CURRENT HEAD) — added a text box to
   the landing page that runs a prompt through the `model` agent, to OpenRouter
   when an `OPENROUTER_API_KEY` env var is set, else the deterministic stub.

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

---

## 4. WHAT'S LEFT / SUGGESTED NEXT

### Production/deploy gaps the user should resolve (explicit, not built)
These are the honest reasons the project is **not yet "1.0 / production-ready"**
despite 649 passing tests:
- **Transport security:** over `tcp`/`ipc` the directive/response protocol is
  **unauthenticated** — no TLS, no authn/z. Fine for localhost/demo, not across
  a real trust boundary. (A documented trust boundary is the least we can add.)
- **No per-directive resource envelopes** in the FBP tree (step/size/latency
  bounds live only in the Manager line, not in the FBP tree).
- **Real LLM provider** is wired as an in-process opt-in hook only; no
  production credential management, no network path hardened for deployment.
- **No container/OS sandboxing / seccomp / VM isolation** of agent execution.
- **No distribution/networking/cloud** and **no MCP/A2A** baked in.
These are listed in `STATUS.md`'s "out of scope / future volleys".

### Loose ends / immediate next actions
- **Commit is unpushed:** `9aea719` (the OpenRouter text box) is local but not
  on GitHub (remote is at `0c78394`, which is the coverage/hardening commit
  minus CI). Ask the user if they want it pushed, or push with a clear note.
- **docs/fbp.md / FBP_HANDOFF.md / README_FBP.md** are living docs — keep them
  current (they now mention the model box).
- **The `fbp-web` landing page** is live and runnable for a demo:
  `uv run agent-centric fbp-web --open` (set `OPENROUTER_API_KEY` for a real
  model; it fails closed to the deterministic stub otherwise).
- Consider adding to the model box: model dropdown, streaming, showing verified
  / source status next to the answer.

---

## 5. TOOLING / COMMANDS CHEAT-SHEET

```sh
uv run agent-centric run                # deterministic demo
uv run agent-centric fbp                # drive FBP demo (inproc)
uv run agent-centric fbp --transport tcp | ipc
uv run agent-centric fbp-web --open     # landing page + model box
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
- Trust only what 649 tests prove and what is committed; say clearly when
  something is unverifiable or unpushed.

---

Prepared so a new session can resume **both** the codebase and the
conversation. — Lead Architect