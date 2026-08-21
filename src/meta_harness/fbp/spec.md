# Agent-Centric FBP Architecture — Spec (draft)

**Status:** Foundation design for the `agent-centric-fbp` branch.
**Authority:** Lead Architect (via this session).
**Classification:** Mission-Critical.

This spec locks in the model refined in session and is the contract the
foundation code implements. It is deliberately a *draft* — the first concrete
step of a pivot, not the finished system.

## 1. The model

A rooted, recursive **tree of nodes**. There is no central `AgentManager`; the
topology *is* the governance.

- **Root = the shell.** The shell is a node (not an external orchestrator). It
  bootstraps the tree and is the origin of work and the final owner of
  responsibility.
- **Work travels down.** A node delegates work to its children.
- **Responses and responsibility bubble up.** Each node consolidates its
  children's responses and is responsible for its subtree.
- **Every parent is responsible for its children and provides their context.**
  The parent hands down the *context* (domain, rules, verification constraints)
  the child operates within. This is recursive — the same contract holds at
  every level.
- **Each agent owns its domain and is the context provider to its children.**
  An agent receives a **callable** (its task/domain) as its input.
- **Everything stays local when possible.** A node resolves work itself; it
  only delegates down what it cannot resolve locally.
- **The context is hierarchical.** Context composes down the tree, deepening at
  each level.

## 2. Node contract (`init / run / kill`)

Every node implements three operations:

- `init(context)` — set up the node with the context provided by its parent.
- `run(work)` — process a unit of work. May resolve locally or delegate to
  children. Returns a `Response`.
- `kill()` — teardown.

## 3. The correctness spine: verification on the upward path

The property that preserves the mission-critical guarantee ("no unverified
money/dates") in a manager-less design:

- Work + context flow **down**.
- Responses + responsibility flow **up**, and **each parent verifies a child's
  response before accepting responsibility for it and bubbling it up**.
- A response that fails verification is an explicit, audited failure — never a
  verified success.

This makes the tree a **recursive verification hierarchy**: the same
verification rule composes down via context, and is enforced on the way up.

## 4. Scope of this foundation

Implemented here (pure, offline-testable, deterministic):

- `Context` — hierarchical context (the governance mechanism).
- `Node` — the `init/run/kill` contract.
- `Shell` — the root node that builds the tree and runs work through it.
- Verification on the upward path (a `Verifier` gate).

**Not yet implemented (next steps, optional adapters):**

- ZeroMQ `poll` transport for multiplexing channels (adds a dependency).
- FastAPI UI/API layer (adds a dependency).
- The concrete bills-loop nodes (intake → accept → registry → calendar →
  maintain) as an FBP graph.
- Real concurrency / multi-channel routing.

These are deliberately deferred so the foundation can be proven correct,
deterministic, and fully tested before layering on transport and UI.

## 4. Non-goals (unchanged from the existing system)

- No unverified money/dates; no auto-accept.
- No silent registry writes.
- No network in CI.
- No SMTP / send / delete / move email.
- No recurrence engine, payments, delete-all, or broad "edit any JSON".
- No cloud OCR / cloud APIs.