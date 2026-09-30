---
id: SPEC-0003
title: Complete the agent-factory convention across all agent-oriented files
type: convention
target_repo: agent-centric
target_branch: main
status: implemented
owner: operator
---

# Complete the agent-factory convention across agent-oriented files

## Context

SPEC-0001 ([`specs/agent-conventions.md`](agent-conventions.md)) applied the
agent-factory convention, but left the set of "agent-oriented files" open and
incomplete. A read-only audit found:

- **Dangling progressive-disclosure pages.** `PRINCIPLES.md`, `AGENTS.md`,
  `.agentfactory.toml`, and `specs/holdout/README.md` link
  `docs/agent/levels.md`, `testing.md`, `verification.md`, and `committing.md`;
  none existed (only `docs/agent/README.md`).
- **Broken relative links.** `specs/SPEC-0002-cbp-component-architecture.md`
  used `../../` where `../` was correct; `docs/references/README.md` used
  `../PRINCIPLES.md` instead of `../../PRINCIPLES.md`.
- **Un-normalized stragglers.** `HANDOFF.md`, `STATUS.md`, `README_FBP.md`,
  `docs/DIRECTIVE.md`, `docs/FBP_HANDOFF.md`, and `KERNEL.md` carried no
  canonical entry pointer; `docs/DIRECTIVE.md` still carried an obsolete rename
  mandate.
- **No enforcement.** `tests/test_agent_conventions.py` validated an allowlist
  and checked no links, so "all agent-oriented files follow the convention"
  could not be asserted.

## Decision

1. **Author the promised pages** — `docs/agent/{levels,testing,verification,committing}.md`,
   thin progressive-disclosure notes that point back to the laws and skills
   rather than restating them.
2. **Normalize stragglers to the pointer model** — prepend a status banner that
   points at `AGENTS.md`; leave bodies unchanged (historical docs are not
   updated retroactively). Defang the obsolete §3 mandate in
   `docs/DIRECTIVE.md`.
3. **Fix the broken relative links** in SPEC-0002 and `docs/references/README.md`.
4. **Surface the map to opencode** — add `docs/agent/README.md` to
   `opencode.json.instructions` (opencode reads `AGENTS.md` natively; the
   `.agents/skills/` tree is already discovered).
5. **Extend the guard** — `tests/test_agent_conventions.py` asserts the four
   pages exist, every relative markdown link in tracked `.md` resolves, and each
   in-scope doc carries the entry pointer.

## Scope

- **In:** `docs/agent/**`, status banners on agent-facing/handoff docs,
  `opencode.json` instructions, link fixes, the guard test, this spec.
- **Out:** any change to `PRINCIPLES.md` laws, `.agentfactory.toml` level,
  `src/` semantics, KERNEL invariants, or the L3 validator.

## Acceptance criteria

- [ ] `docs/agent/{levels,testing,verification,committing}.md` exist and are
      linked from `docs/agent/README.md`.
- [ ] Every relative markdown link in tracked `.md` files resolves
      (`TestProgressiveDisclosure`).
- [ ] Each in-scope doc carries the entry pointer to `AGENTS.md`
      (`TestStragglers`).
- [ ] `opencode.json` keeps `$schema` and `permission.edit = "deny"`; its
      `instructions` include `docs/agent/README.md`.
- [ ] `tests/test_agent_conventions.py` passes; `ruff` and `mypy src` clean.

## Risks / invariants

Respects Laws 4 (progressive disclosure), 6 (auditability), 11 (whole-file
edits), 12 (test authority), and 13 (commit and push). It does not touch KERNEL
invariants or `src/` semantics. Banner edits are prepend-only transforms — no
body is rewritten — and the link guard is restricted to markdown `[](...)`
links to avoid prose false positives.
