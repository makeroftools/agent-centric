# PRINCIPLES — Meta-Harness

These are the non-negotiable rules governing every decision in this repository.
They override all other considerations. When a choice trades any of these for
speed, convenience, or feature completeness, that choice is forbidden.

## 1. Correctness First

Correctness, accuracy, robustness, and verifiability outrank every other
concern. When in doubt, choose the more verifiable, more isolated, more
auditable, and more conservative path. A system that is fast but wrong is
worse than a system that is slow but provably correct.

## 2. Deterministic Control Plane

The control plane (Agent Manager) must be deterministic. Given the same inputs
(agent manifest, task, resource envelope, and a deterministic agent), it must
reproduce the same trajectory and the same outcome. Nondeterminism is confined
to the agent's own computation and is recorded, never relied upon by the
control plane.

## 3. Agent-Centric Design

Agents are first-class governed components. They are registered, isolated,
and executed under explicit contracts. The harness does not embed agent
behavior; it governs it. Every agent exposes a thin, intentional interface.

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
resource envelope. The Manager enforces these bounds hard: timeouts, step
limits, and resource caps are not advisory.

## 8. Explicit Failure

Failure is a first-class, audited outcome, never an implicit or silent one.
A task either returns a verified result or an explicit, contained, audited
failure. There is no third, ambiguous state.
