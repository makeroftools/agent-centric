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
responses).

The architecture is **fractal and recursive**: there is one abstract concept —
the Agent — and everything is an instance of it. Every task is itself an
agent, which may in turn delegate to further agents, extending in principle
all the way down to individual instructions. Each agent is the center of its
own universe: a worker to its parent and a manager to its children. The same
contract holds at every level — there is no privileged "task" type exempt
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
trajectory. Nothing that matters happens silently. A trajectory can be
replayed to reconstruct exactly what occurred.

## 7. Least Privilege

Each agent receives only the resources and capabilities it was granted in its
resource envelope. These bounds are enforced hard: timeouts, step limits, and
resource caps are not advisory.

## 8. Explicit Failure

Failure is a first-class, audited outcome, never an implicit or silent one.
A task either returns a verified result or an explicit, contained, audited
failure. There is no third, ambiguous state.

## 9. Critical Path Method (CPM) as a First-Class Tool

Critical Path Method (CPM) is a fundamental, first-class tool of the
architecture. It identifies the longest dependency chain through a graph of
work (the **critical path**) and the **slack/float** of every other element.

CPM is a **deterministic, read-only observational aid**: a pure,
side-effect-free function over a plan (and optionally recorded consumption).
It never mutates exposed state. It identifies which paths dominate the duration
and where slack allows flexibility — for planning and observation, never for
driving execution.

## 10. Registries Are Passive Catalogs; Evidence Is Immutable

This is two laws bound together, the backbone of the **Network of Experts AI**
model.

1. **A registry is a passive metadata catalog — never an authority.** It holds
   records of *what* and *where* (a domain's contract, provenance). It does
   **not** compile, does **not** run, and does **not** decide. Authority lives
   in the topology — the tree, where each parent governs its subtree. The
   Domain Registry (`DomainRegistry`) exercises this: it records how a domain
   is served but never grants authority.
2. **Evidence is write-once and immutable.** An artifact (a verified run
   outcome) is append-only, keyed by `(tenant, domain, run)`, and is never
   mutated. Re-recording fails closed. The Artifact Vault holds *evidence*,
   never editable storage.

Together these keep the network-of-experts machinery trustworthy: the catalog
can be queried, provenance can be proven, and nothing changes underneath you.

## 11. Immutable, Atomic File Replacements (No In-Place Mutation)

Files are **never edited in place**. To change a file, always:

1. Copy the current file's contents to a temporary file.
2. Apply all changes to the temporary copy.
3. Delete the original file.
4. Create the new, fully revised file in place of the original.

Rationale: in-place editing in the authoring environment is unreliable — it
forces repeated authorization prompts and risks partial or corrupt writes on
mission-critical files. Atomically replacing the whole file is a complete,
auditable, deterministic write. This law applies to source, docs, and tests
alike, with no exceptions for convenience.