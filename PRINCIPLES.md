# PRINCIPLES — Agent-centric

These are the non-negotiable rules governing every decision in this repository.
They override all other considerations. When a choice trades any of these for
speed, convenience, or feature completeness, that choice is forbidden.

## 1. Correctness First

Correctness, accuracy, robustness, and verifiability outrank every other
concern. When in doubt, choose the more verifiable, more isolated, more
auditable, and more conservative path. A system that is fast but wrong is
worse than a system that is slow but provably correct.

## 2. Deterministic Control Plane

The control plane must be deterministic. Given the same inputs (agent manifest,
task, resource envelope, and a deterministic agent), it must reproduce the
same trajectory and the same outcome. Nondeterminism is confined to the
agent's own computation and is recorded, never relied upon by the control
plane.

## 2a. Abstract System — Not a Trading System

This is an **abstract, general-purpose agent system**. It is not a trading
system, and trading / automated-trading concerns are **non-relevant** to this
project. The mission-critical posture (correctness, determinism, verification,
audit, fail-closed) applies to the abstract system itself, not to any
particular domain. Do not frame this project as a trading system.

## 3. Agent-Centric Design

Agents are first-class governed components. They are registered, isolated,
and executed under explicit contracts. The harness does not embed agent
behavior; it governs it. Every agent exposes a thin, intentional interface.

There is **no central Manager**. The architecture is a tree of agents; the
"manager" role is simply the upward-facing responsibility any agent has toward
its children (providing their context, routing directives, verifying
outcomes).

The architecture is **fractal and recursive**: there is one abstract concept —
the Agent — and everything is an instance of it. Every task is itself an
agent, which may in turn delegate to further agents. Each agent is the center
of its own universe: a worker to its parent and a manager to its children. The
same contract holds at every level — there is no privileged "task" type exempt
from being an agent.

## 4. Progressive Disclosure

Public interfaces are minimal and intentional. Details are revealed only where
they are needed. The core contracts are small and stable; complexity is added
incrementally and only when justified.

## 5. Local-First

Everything runs in-process and on the local machine. No distribution, no
networking, no cloud dependencies are required for the core. This keeps the
system auditable, replayable, and testable.

## 6. Full Auditability

Every step, decision, and outcome is recorded in a durable, reconstructible
report. Nothing that matters happens silently. A trajectory can be replayed to
reconstruct exactly what occurred.

## 7. Least Privilege

Each agent receives only the resources and capabilities it was granted. These
bounds are enforced hard: timeouts, step limits, and resource caps are not
advisory.

## 8. Explicit, Audited Failure

A failure is a first-class, audited outcome, never implicit or silent. A task
either returns a verified result or an explicit, contained, audited failure.
There is no third, ambiguous state.

## 9. Critical Path as Truth

The critical path is a fundamental, first-class view of the architecture. It is
a deterministic, read-only observational aid: a pure function over a plan. It
never mutates and never drives execution — only informs it.

## 10. Registries Are Passive Catalogs; Evidence Is Immutable

1. A registry is a passive metadata catalog — never an authority. It records
   *what* and *where*, and it never decides. Authority lives in the topology.
2. Evidence is write-once and immutable — append-only, keyed by (tenant,
   domain, run), never mutated.

## 11. NO IN-PLACE FILE EDITS — EVER. THIS IS MISSION CRITICAL.

> **NEVER, EVER edit a file in place. Do not use in-place edit tools. Do not
> rewrite a file's interior by hand. Do not patch a single line into an existing
> file.** In-place editing is **ABSOLUTELY FORBIDDEN.** This is not a suggestion;
> it is a hard law with zero exceptions.

**Why.** The authoring environment's in-place editing is unreliable: it forces
repeated authorization prompts on the operator and risks partial or corrupt
writes to mission-critical files. A single interrupted write can silently
truncate or corrupt a file that the whole system depends on. This has been
observed to corrupt content in this very project. It is unacceptable and
cannot be risked.

**The ONLY permitted way to change a file:**

1. **Copy** the file's current contents to a temporary file.
2. **Apply your changes to the temporary copy** (edit the temp freely — it is
   disposable and cannot hurt anything).
3. **`cp` the temp file over the original**, atomically replacing the whole file
   in one byte-complete operation:

   ```sh
   cp tempfile targetfile
   ```

4. **`rm` the temporary file.**

This applies **always**, to **every file** — source, tests, docs, configuration —
with **no exceptions, ever**, not even for a one-character change. Do not permit
a "quick in-place fix." The whole file is always replaced as a single complete,
auditable, deterministic unit.

**Do not rationalize around this law.** There is no "small edit." There is no "it
is just a comment." There is only whole-file replacement, done as above, or the
change is refused.