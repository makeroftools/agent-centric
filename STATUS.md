# STATUS — Volley 001–006

**Authority:** Lead Architect
**Classification:** Mission-Critical
**Updated:** 2026-08-08

## Adherence to the Overriding Correctness Directive

All volleys were executed under the overriding directive: correctness first,
deterministic control plane, agent-centric design, progressive disclosure,
local-first, full auditability, least privilege, and explicit failure. No
trade of correctness or robustness for speed, convenience, or feature
completeness was made. Where a choice existed, the more verifiable, more
isolated, more auditable, and more conservative path was selected. For Volley
004, every tool interaction is mediated, recorded, bounded, and subject to the
same control-plane authority as the rest of the system. For Volley 005,
composition is entirely under deterministic Manager control: agents never gain
the ability to spawn or directly invoke one another. For Volley 006, resource
control became more precise (per-stage envelopes with composition accounting)
without weakening any existing invariant.

---

# Volley 001 — Meta-Harness Foundation (delivered)

### 1. Repository foundations
- Monorepo-style layout: `src/meta_harness/contracts/` (contracts),
  `src/meta_harness/control_plane/` (Manager + Verifier),
  `src/meta_harness/agents/` (agent interface + agents),
  `tests/` (invariant tests), `examples/demo.py`.
- Lightweight tooling: `uv` (deps), `ruff` (lint), `mypy` (strict),
  `pytest`. `PRINCIPLES.md` records the non-negotiable rules.

### 2. Versioned core contracts
- `AgentComponentManifest`, `TaskSpecification` + `ResourceEnvelope`,
  `Trajectory`/`StepRecord`, `VerifiedResult`/`Failure`. Immutable, strongly
  typed, validated, versioned.

### 3. Minimal running core (in-process, local)
- Deterministic **Agent Manager**, one concrete agent (CounterAgent), a real
  **Verifier**, and hard enforcement of overall timeout, step limit, and
  per-step time budget.

---

# Volley 002 — Capability Registry & Multi-Agent Registration (delivered)

Scope: extend the core so multiple agent components can be registered and
selected by capability, while preserving **single-agent execution per task**.
No multi-agent composition, delegation, or inter-agent communication was
introduced.

### 4. Capability model
- New `Capability` contract (`src/meta_harness/contracts/capability.py`): a
  structured, versioned `{name, version}` declaration, immutable and hashable,
  designed for exact-match selection and later matching.
- `AgentComponentManifest` refined from flat `frozenset[str]` capabilities to
  `frozenset[Capability]`. Contract version bumped to **`manifest.v2`** (v1
  retained only in the version enum).

### 5. In-process deterministic Registry
- New `Registry` (`src/meta_harness/control_plane/registry.py`), owned by the
  Agent Manager. Supports registration of multiple agents and lookup by exact
  capability (name + version) or by name.
- Registration validates the manifest and rejects, atomically:
  - invalid/non-manifest inputs,
  - duplicate agent names,
  - **capability conflicts** — a capability may be owned by exactly one agent,
    keeping capability lookup unambiguous.
- Lookup is side-effect free and deterministic (pure reads over immutable
  indexes).

### 6. Manager integration
- The Agent Manager now uses the `Registry` for agent resolution.
- `TaskSpecification` may select an agent by **explicit `agent_name`** OR by
  **exact `capability`** (mutually exclusive; exactly one required). Contract
  version bumped to **`task.v2`** (v1 retained only in the version enum).
- Execution remains strictly **single-agent per task**: no child agents are
  spawned, and all existing control-plane invariants (envelope, trajectory,
  verification, failure semantics) apply unchanged to whichever agent is
  selected.

### 7. Second concrete agent
- New deterministic **ReverseAgent** (`agents/reverse.py`): reverses a string,
  with a distinct `reverse` capability (vs. the counter's `count`).
- Both agents are selectable by name or capability and fully governed.
- A matching real verifier (`verify_reverse_output`) was added; the gate
  re-derives the expected output and rejects wrong outputs (fail-closed).

---

## Correctness evidence (Volley 002 state)

Automated tests prove all invariants. All pass:

```
43 passed in 0.57s          # pytest
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 15 source files)
```

New tests for Volley 002 (`tests/test_registry.py`) prove the registry and
selection invariants:

| Invariant | Test |
| --- | --- |
| Multiple agents can be registered | `TestRegistry::test_register_multiple_agents` |
| Lookup by name and exact capability | `test_lookup_by_name`, `test_lookup_by_capability_exact_match` |
| Duplicate name / capability conflict / invalid manifest rejected | `test_duplicate_name_rejected`, `test_capability_conflict_rejected`, `test_non_manifest_rejected` |
| Registry is deterministic / side-effect free | `test_registry_is_deterministic` |
| Capability-based selection works | `test_select_counter_by_capability`, `test_select_reverse_by_capability` |
| Envelope / trajectory / verification / failure hold for any selected agent | `test_capability_selection_still_governed`, `test_capability_selection_verification_gate`, `test_unknown_capability_fails_explicitly` |

`tests/test_contracts.py` additionally proves `Capability` validity, hashability,
and the new task selector rule (exactly one of `agent_name`/`capability`).

All Volley 001 invariants continue to hold (counter by name, step-limit,
overall timeout, per-step timeout, wrong-output verification rejection,
unknown-agent failure, replayable determinism).

`examples/demo.py` demonstrates both agents selected by name and by capability,
plus step-limit enforcement for a capability-selected agent.

---

# Volley 003 — Durable Trajectory & Strengthened Auditability (delivered)

Scope: make the trajectory a durable, reconstructible, append-only audit record
that survives process boundaries, without weakening any existing control-plane
guarantee and without introducing multi-agent composition, workflow engines,
external messaging, distributed storage, or query layers.

### 8. Durable, append-only Trajectory Store
- New `control_plane/trajectory_store.py` defines a `TrajectoryStore` protocol
  and two implementations:
  - `FileTrajectoryStore` — durable, file-based. Each trajectory is an
    append-only JSON-lines file (one record per line: `meta`, then zero or more
    `step`, then `outcome`) under a directory, hex-encoded for an injective,
    filesystem-safe, deterministic filename. Every write is `flush`ed and
    `fsync`-ed, so a crash cannot silently corrupt an already-appended record;
    a truncated or malformed record is **detected** on load via
    `CorruptTrajectoryError`.
  - `InMemoryTrajectoryStore` — the default; identical append-only semantics,
    not durable across restarts.
- Each trajectory is uniquely identified and fully reconstructible. A
  trajectory with steps but no recorded outcome is reported as
  `interrupted` (detectable, never silent). Append-only: a second `outcome`
  record is rejected so the audit record cannot contradict itself.

### 9. Manager integration
- The Agent Manager persists **every step and the terminal outcome** through
  the durable store. A verified result is returned only after its outcome is
  durably recorded; a store failure mid-task fails closed with an explicit
  `INTERNAL` failure and no certified result.
- `manager.load(trajectory_id)` and `manager.contains(trajectory_id)` provide a
  clean, tested way to retrieve and inspect a complete, immutable trajectory.
- All previous enforcement (resource envelopes, timeouts, verification gate,
  failure semantics, single-agent execution, determinism of trajectory
  content) continues to function unchanged.

### 10. Audit strengthening
- Every control-plane decision affecting the outcome is present in the
  trajectory: each step, and the sealed verified-or-failure decision.
- Trajectory records are immutable once written (append-only; reading never
  modifies the stored file).
- `Outcome` now carries the durable `trajectory_id` for retrieval and replay.
- Durable trajectories are reconstructible and replayable by id, and inspection
  is fully supported and tested.

---

# Volley 004 — Mediated Tool Access (delivered)

Scope: introduce a minimal, fully mediated Tool mechanism so agents can request
external capabilities only through the Agent Manager, with every tool
interaction recorded, bounded, and under the same control-plane authority as
the rest of the system. No real external APIs, network calls, or filesystem
mutation beyond the controlled trajectory store were introduced.

### 11. Tool contract
- New versioned `ToolDescriptor` (`contracts/tool.py`, `tool.v1`): name,
  description, input schema, output schema, and execution semantics. Tools are
  pure with respect to the agent: the agent requests, the Manager executes.

### 12. Tool registration & injection
- New `ToolRegistry` (`control_plane/tools.py`) is the tightly controlled
  executor under the Manager. Two trivial, deterministic, side-effect-free
  tools are provided: `to_upper` and `add`.
- The Agent Manager is the sole authority that makes tools available. For each
  task it builds a `ToolContext` from `task.granted_tools` (additive `task.v3`
  field), so an agent receives only the tools explicitly granted for that
  task/envelope. Unknown granted names are omitted (fail-closed).

### 13. Execution & mediation
- An agent yields a `ToolRequest`; the Manager validates the grant, records the
  request and the result/failure as ordered steps, executes (or rejects) via
  the `ToolRegistry`, and sends a `ToolResult` back to the agent.
- Tool calls consume the task's step budget (request + result each count).
- Tool failures are explicit and recorded; they are delivered to the agent and
  never produce an unverified success — the final verification gate still
  applies.
- The agent-facing `ToolContext` exposes only names/descriptors, never an
  executable implementation, so the agent cannot bypass the Manager.

### 14. Trajectory integration
- Every tool request and result (or failure) appears as a first-class, ordered
  step in the durable trajectory (e.g. a `REJECTED` step for an ungranted
  request).

A third concrete agent (`agents/case_tool.py`) requests the `to_upper` tool and
is fully governed (verifier = uppercase). Existing agents were migrated to the
new agent signature (payload, step_budget, tools).

---

## Explicit non-goals honored

Multi-agent composition/delegation/concurrent execution, workflow engines/FBP
networks, external messaging systems, distributed storage, complex query
interfaces (GraphQL, etc.), performance-oriented storage optimisations, real
external APIs/network calls/filesystem mutation beyond the controlled
trajectory store, MCP/A2A integration, multi-agent tool sharing or concurrent
tool execution, complex tool composition/search, and any bypass of the Manager
were all **excluded** from all volleys, as required.

## Definition of Done — confirmed (Volley 004)

1. ✅ Agents can only use tools that the Manager explicitly grants.
2. ✅ All tool interactions are mediated, bounded, and durably recorded.
3. ✅ Existing control-plane invariants remain intact.
4. ✅ New tests provide clear evidence of mediation, recording, and enforcement.
5. ✅ This `STATUS.md` is accurate.

## Contract versioning notes

- `manifest.v1` → `manifest.v2`: capabilities changed from `frozenset[str]` to
  `frozenset[Capability]`. Registration now rejects manifests at any version
  other than v2.
- `task.v1` → `task.v2`: agent selection changed from a required `agent_name`
  to exactly one of `agent_name` or `capability`.
- `task.v2` → `task.v3` (additive): adds `granted_tools`. A `task.v2` spec is
  equivalent to a `task.v3` spec with no granted tools; both versions remain
  accepted.
- Volley 003 made **no breaking contract changes**; Volley 004 made purely
  additive changes (`task.v3` `granted_tools`, new `tool.v1`), plus a new
  `Tool`/`ToolContext` runtime layer in the agents package.
- Versioned contracts remain immutable and validated in `__post_init__`;
  unsupported versions are rejected.

## Correctness evidence (Volley 004 state)

Automated tests prove all invariants. All pass:

```
69 passed in 0.61s          # pytest (was 55)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 19 source files)
```

New `tests/test_tools.py` (14 tests) proves the mediation, recording, and
enforcement invariants, and `examples/demo.py` demonstrates granted vs.
ungranted tool access end to end.

---

# Volley 005 — Manager-Orchestrated Sequential Composition (delivered)

Scope: introduce the first form of multi-agent composition — a
Manager-orchestrated sequential pipeline — while keeping composition entirely
under deterministic Manager control and ensuring agents never gain the ability
to spawn or directly invoke one another. No concurrent/parallel execution,
agent-initiated spawning, cyclic/dynamic workflows, FBP networks, or external
messaging were introduced.

### 15. Composition model
- New versioned pipeline contract (`contracts/pipeline.py`, `pipeline.v1`):
  `StageSpec` (exactly one of `agent_name`/`capability`, plus optional
  `granted_tools`) and `SequentialComposition` (ordered, non-empty stages).
- Additive `task.v4` adds an optional `pipeline` field. A task specifies either
  the single-agent selectors OR a pipeline (not both).
- The Manager alone is responsible for sequencing, data hand-off, and the
  lifecycle of each stage.

### 16. Trajectory continuity
- The whole sequential execution produces ONE coherent, durable trajectory.
- Stage boundaries are explicit: a `STARTED` marker step (`pipeline stage N
  begin`) is recorded before each stage, so boundaries and data flow are
  obvious in the trajectory.
- Each stage's steps and tool use (if any) appear in order within the shared
  trajectory.

### 17. Failure semantics
- Failure or verification failure at any stage aborts the composition.
- The trajectory records exactly where and why it stopped (the failing stage's
  steps are present; later stages never begin).
- No partial or unverified final result is ever returned.

### 18. Concrete demonstration
- `examples/demo.py` runs `case_tool -> reverse` (uppercase via the mediated
  `to_upper` tool, then reverse), fully deterministic and easy to verify.
- Hand-off rule: the verified output of a stage is passed as the next stage's
  payload; a scalar output is wrapped into the `{"text": ...}` payload shape the
  harness agents expect (a minimal, explicit, deterministic rule; no other
  transformation).

---

## Explicit non-goals honored

Multi-agent composition/delegation/concurrent execution, workflow engines/FBP
networks, external messaging systems, distributed storage, complex query
interfaces (GraphQL, etc.), performance-oriented storage optimisations, real
external APIs/network calls/filesystem mutation beyond the controlled
trajectory store, MCP/A2A integration, multi-agent tool sharing or concurrent
tool execution, complex tool composition/search, any bypass of the Manager,
concurrent/parallel agent execution, agent-initiated spawning or delegation,
cyclic/dynamic workflows, and complex data transformation between stages
beyond simple verified hand-off were all **excluded** from all volleys, as
required.

## Definition of Done — confirmed (Volley 005)

1. ✅ The Manager can execute a sequential composition of two or more agents
   under full governance.
2. ✅ Stage boundaries, hand-offs, and outcomes are explicit in the durable
   trajectory.
3. ✅ All prior invariants hold.
4. ✅ New tests provide clear evidence of ordered execution, verification
   between stages, failure abort, and auditability.
5. ✅ This `STATUS.md` is accurate.

## Contract versioning notes

- `manifest.v1` → `manifest.v2`: capabilities changed from `frozenset[str]` to
  `frozenset[Capability]`. Registration now rejects manifests at any version
  other than v2.
- `task.v1` → `task.v2`: agent selection changed from a required `agent_name`
  to exactly one of `agent_name` or `capability`.
- `task.v2` → `task.v3` (additive): adds `granted_tools`.
- `task.v3` → `task.v4` (additive): adds the optional `pipeline` field for
  sequential composition.
- Volley 005 added a new `pipeline.v1` contract and the additive `task.v4`
  field; no existing contract semantics were changed.
- Versioned contracts remain immutable and validated in `__post_init__`;
  unsupported versions are rejected.

## Correctness evidence (Volley 005 state)

Automated tests prove all invariants. All pass:

```
82 passed in 0.61s          # pytest (was 69)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 20 source files)
```

New `tests/test_pipeline.py` (13 tests) proves ordered execution, verified
hand-off between stages, resource-bound enforcement across the composition,
clean abort on intermediate failure, durable/reconstructible final results,
capability-based stage selection, tool use within a stage, and that agents
cannot invoke one another directly.

---

# Volley 006 — Per-Stage Resource Envelopes & Composition Accounting (delivered)

Scope: refine sequential composition so each stage can carry its own resource
envelope (or inherit the parent), with the Manager accounting for consumption
per stage and across the whole composition, making overall and stage-level
limits enforceable and auditable. No parallel/concurrent execution,
agent-initiated composition, full Critical Path Method, dynamic re-allocation,
or complex scheduling were introduced.

### 19. Per-stage resource model
- Additive `pipeline.v2`: each `StageSpec` may declare its own
  `stage_envelope`. If declared, it is enforced for that stage; otherwise the
  stage inherits the parent task envelope. `pipeline.v1` is still accepted but
  rejects stage envelopes.
- Deterministic rule: the parent task envelope always bounds the whole
  composition (total steps and total wall-clock time), so both stage-level and
  composition-level limits are enforced simultaneously.

### 20. Accounting & trajectory
- Resource consumption (steps and elapsed time) is recorded at stage boundaries
  (each `pipeline stage N begin` marker carries the effective envelope) and in
  a final `pipeline resource accounting` summary step listing per-stage
  consumption and the total step count.
- Consumption is attributable to stages and inspectable in the durable,
  reconstructible trajectory.
- Exceeding a stage envelope or the overall envelope aborts the composition
  with a clear, audited failure; the accounting summary is still recorded on
  abort.

### 21. Manager behaviour
- `_run_pipeline` resolves each stage's effective envelope, passes it to the
  shared `_execute_agent` (which now counts steps per-stage while keeping
  absolute record indices), and enforces the parent envelope across the whole
  run via a composition limit.
- All prior sequential semantics are preserved: ordered execution, verified
  hand-off, a single coherent trajectory, and fail-closed abort.

---

## Explicit non-goals honored

Multi-agent composition/delegation/concurrent execution, workflow engines/FBP
networks, external messaging systems, distributed storage, complex query
interfaces (GraphQL, etc.), performance-oriented storage optimisations, real
external APIs/network calls/filesystem mutation beyond the controlled
trajectory store, MCP/A2A integration, multi-agent tool sharing or concurrent
tool execution, complex tool composition/search, any bypass of the Manager,
concurrent/parallel agent execution, agent-initiated spawning or delegation,
cyclic/dynamic workflows, complex data transformation between stages beyond
simple verified hand-off, parallel or concurrent stage execution, full
Critical Path Method implementation, dynamic re-allocation of budgets at
runtime, and complex economic/priority-based scheduling were all **excluded**
from all volleys, as required.

## Definition of Done — confirmed (Volley 006)

1. ✅ Sequential compositions support and enforce per-stage resource envelopes
   under clear rules.
2. ✅ Consumption is attributable and visible in the durable trajectory.
3. ✅ All prior invariants hold.
4. ✅ New tests provide clear evidence of stage-level and composition-level
   enforcement.
5. ✅ This `STATUS.md` is accurate.

## Contract versioning notes

- `manifest.v1` → `manifest.v2`: capabilities changed from `frozenset[str]` to
  `frozenset[Capability]`. Registration now rejects manifests at any version
  other than v2.
- `task.v1` → `task.v2`: agent selection changed from a required `agent_name`
  to exactly one of `agent_name` or `capability`.
- `task.v2` → `task.v3` (additive): adds `granted_tools`.
- `task.v3` → `task.v4` (additive): adds the optional `pipeline` field for
  sequential composition.
- `pipeline.v1` → `pipeline.v2` (additive): adds optional per-stage
  `stage_envelope`. Both versions remain accepted; `pipeline.v1` rejects stage
  envelopes.
- Volley 006 made purely additive changes (`pipeline.v2` `stage_envelope`); no
  existing contract semantics were changed.
- Versioned contracts remain immutable and validated in `__post_init__`;
  unsupported versions are rejected.

## Correctness evidence (Volley 006 state)

Automated tests prove all invariants. All pass:

```
91 passed in 0.67s          # pytest (was 82)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 20 source files)
```

New `tests/test_pipeline_accounting.py` (9 tests) proves stage-specific
envelope enforcement (step limit and timeout), parent-envelope inheritance,
overall composition-limit enforcement, attributable consumption recording,
stage-boundary envelope recording, accounting on abort, and preservation of
verified hand-off/ordering.

## Out of scope / future volleys (not started)

- Concurrent / parallel agent execution
- Agent-initiated spawning or delegation
- Cyclic or dynamic workflows
- Durable workflow engines / FBP networks
- Full Critical Path Method implementation
- Dynamic re-allocation of budgets at runtime
- Complex economic or priority-based scheduling
- External messaging
- Additional agents and verifiers
- MCP/A2A integration
- Distribution, networking, and cloud concerns