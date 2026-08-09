# STATUS — Volley 001–023

**Authority:** Lead Architect
**Classification:** Mission-Critical
**Updated:** 2026-08-09

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
without weakening any existing invariant. For Volley 007, inter-stage data is
no longer trusted implicitly: the verified output of a stage is validated
against declared output/input schemas before it is handed off, closing a
correctness gap and making inter-stage data contracts first-class. For Volley
008, a thin deterministic Policy owned by the control plane constrains which
agents, capabilities, and tools a task or composition may use, evaluated and
enforced by the Manager before any work begins — fail-closed, explicit, and
never bypassable by an agent. For Volley 009, envelope exhaustion no longer
merely fails after the fact: the Manager now cooperatively cancels the running
agent, records the cancellation explicitly in the durable trajectory, and
never returns an unverified success. For Volley 010, the first parallel
composition (fan-out / join) was introduced: independent stages run
concurrently under full governance, any failure cancels siblings and fails
closed with a complete audit record, and success yields a deterministic join
— without weakening isolation, verification, auditability, or fail-closed
behaviour. For Volley 011, the first critical-path analysis was introduced:
critical-path (CPM) analysis is a deterministic, read-only observational aid
owned by the control plane — it identifies the longest dependency chain and
per-stage slack over a composition and, optionally, recorded consumption from
a completed trajectory, but it is explicitly observational only at this stage
and does not alter scheduling, execution, or resource enforcement. For Volley
012, the first model-mediated agent was introduced: a language-model call is
now an untrusted, stochastic step governed by the same invariants as every
other action — it is mediated through a Manager-owned tool, only usable when
explicitly granted, bounded by resource envelopes, subject to policy, recorded
in the durable trajectory, and never sufficient on its own for a verified
result. Model output is explicitly untrusted until it passes the mandatory
verification gate. For Volley 013, agent execution can now be isolated in a
separate subprocess: each agent's generator loop runs in a child process that
cannot corrupt Manager state, while every authority — tool mediation, policy,
envelopes, trajectory recording, verification, and cancellation — remains in the
Manager. This isolation is strictly additive and does **not** relax verification
or mediation: a child crash, non-zero exit, or protocol violation is always an
explicit, audited, fail-closed failure and never a verified success. The
in-process backend remains the default and is unchanged for unit tests. For
Volley 014, a deterministic, immutable trajectory summary is now available to
operators: it projects a durable trajectory into a minimal, stable summary
(identity, outcome, agents/stages, tool and model calls, resource consumption,
policy decision, cancellations) without ever mutating the audit log or altering
execution. The summary is computed on demand, is side-effect free, and is
strictly additive — it does not relax verification, mediation, or any
control-plane invariant. For Volley 015, deterministic trajectory replay
verification is now available: a task can be re-executed under the same
deterministic configuration and the freshly produced trajectory checked for
equivalence against the stored one (same terminal outcome class / failure
reason, same verified output, same ordered step sequence — multiset for
concurrent parallel work — same agents, and same tool grant/rejection pattern),
excluding wall-clock timings. Replay is fail-closed and read-only with respect
to the original trajectory: it never mutates the audit record, and it applies
only to deterministic configurations (deterministic agents and stub/fake
providers). For Volley 016, composition became composable: a sequential stage may
now be either an agent stage or a nested parallel group, so the Manager can
express "run these branches concurrently, verify/join, then continue to the next
sequential stage". Nesting is shallow (a sequential stage may be a parallel
group, but a parallel group's stages are agent stages only) and remains entirely
Manager-orchestrated — agents still cannot spawn or coordinate. The nested group
reuses the existing parallel engine (``_run_parallel_group``), shares one
coherent trajectory with explicit ``pipeline stage N begin`` / ``parallel group
begin`` / ``parallel stage N begin`` / ``parallel group end`` markers, and does
not weaken isolation, verification, policy, cancellation, accounting, or
auditability. A failure inside a nested group aborts the outer sequence and
cancels siblings exactly as a top-level parallel failure does. For Volley 017,
the kernel was stabilised as a coherent v0 surface: a deliberate public API is
exported and documented, the package is versioned as a kernel milestone
(0.16.0, aligned with volley depth), a minimal local-only operator CLI provides
``run`` / ``summarise`` / ``replay-verify`` and fails closed with clear exit
codes, and a short kernel freeze note (``KERNEL.md``) records what the v0 kernel
guarantees, what is intentionally out of scope, and that further work should
prefer adapters and backends over changing Manager semantics. No execution
invariants changed; this volley prioritises clarity, stability, and operator
usability. For Volley 022, the first specialty agent was introduced: a narrow
bills agent that accepts structured bills in and produces deterministic totals
out, with real verification (independent recompute) that rejects bad or missing
data. Money math is integer-only (cents) with an explicit half-up rounding rule,
so totals are exact and replayable; the ``bill_total`` tool is Manager-mediated
and grantable; and every failure mode is an explicit, audited, fail-closed
failure. No PDF, email, workspace ontology, or Mail-in-a-Box was introduced, and
the volley did not expand into doc organization or email. For Volley 023, a
local, agent-centric workspace was introduced: a ``Workspace`` (root directory
plus a ``WorkspaceLayout`` allowlist) and a ``workspace`` specialty agent that
operates on it entirely through the Manager. The allowlisted mediated file
tools (``list_workspace``, ``read_workspace_file``, ``write_workspace_file``,
``create_workspace_dir``) reject any path not on the allowlist (fail-closed),
with no deletion, rename, or arbitrary traversal; verification is real
(recompute). No email or Mail-in-a-Box was introduced, and the volley did not
expand into doc organization or email.

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

# Volley 007 — Schema-Constrained Stage Hand-off (delivered)

### 22. Hand-off contract
- `StageSpec` (`pipeline.v3`) may declare an `output_schema` and/or an
  `input_schema`. The schema format is minimal and consistent with the existing
  tool contract: a single expected type name (scalar payload) or a mapping of
  field name -> expected type name (object payload).
- `contracts/handoff.py` provides deterministic, side-effect-free validation
  (`validate_handoff`, `is_valid_schema`) over a small, explicit type set
  (`str`, `int`, `float`, `bool`, `dict`, `list`, `null`, `any`).
- `pipeline.v1`/`pipeline.v2` reject hand-off schemas; `pipeline.v3` accepts
  them. The change is purely additive.

### 23. Manager enforcement
- After a stage produces a verified result, the Manager validates the handed-off
  payload against the producing stage's `output_schema` (if declared) and the
  consuming stage's `input_schema` (if declared) before constructing the next
  stage's input.
- A validation failure aborts the composition with an explicit, audited
  `HANDOFF_FAILED` failure; no data proceeds to the next stage.
- On success, a durable `stage N hand-off validated` step records the hand-off
  and the shape of the handed-off data (keys and value types), keeping the
  trajectory inspectable without duplicating content.

### 24. Backward compatibility / defaults
- Stages that declare neither schema are validated under a documented
  conservative default: the handed-off payload must be a mapping (the shape the
  harness agents expect). A scalar output is wrapped into `{"text": ...}` per
  the prior hand-off rule, so existing deterministic agents keep working
  unchanged.
- The default validation still records the handed-off shape in the trajectory.

## Explicit non-goals honored (Volley 007)

- Parallel composition
- Agent-initiated delegation
- Complex transformation or mapping logic between stages
- Full JSON Schema ecosystem features beyond reliable structural validation
- Changes to tool mediation beyond consistency requirements

## Definition of Done — confirmed (Volley 007)

1. ✅ Stage hand-off is schema-constrained under explicit rules.
2. ✅ Invalid hand-off aborts with a durable, inspectable record.
3. ✅ All prior invariants hold.
4. ✅ New tests provide clear evidence of validation success/failure paths.
5. ✅ This `STATUS.md` is accurate.

## Contract versioning notes (Volley 007)

- `pipeline.v2` → `pipeline.v3` (additive): adds optional per-stage
  `output_schema` and `input_schema`. `pipeline.v1`/`pipeline.v2` reject hand-off
  schemas; `pipeline.v3` accepts them.
- New `FailureReason.HANDOFF_FAILED` for explicit, audited hand-off rejection.
- Versioned contracts remain immutable and validated in `__post_init__`;
  unsupported versions and malformed schemas are rejected.

## Correctness evidence (Volley 007 state)

Automated tests prove all invariants. All pass:

```
105 passed in 0.7s          # pytest (was 91)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 21 source files)
```

New `tests/test_handoff.py` (14 tests) proves the hand-off contract (valid and
invalid schemas, `pipeline.v3` gating), valid hand-off success appearing in the
durable trajectory, schema mismatch (output and input) aborting cleanly with a
fully audited `HANDOFF_FAILED` record, that no schema-invalid data reaches a
subsequent stage, the documented default behaviour for stages without schemas,
and that ordering, verification, and resource accounting invariants remain
intact.

# Volley 008 — Thin Policy Component (Deterministic Governance) (delivered)

### 25. Policy contract
- `contracts/policy.py` defines a versioned `Policy` (`policy.v1`) with a
  deliberately small surface: allow/deny sets for agent names, exact
  capabilities, and tool names. `PolicyDecision` carries `allowed` plus a
  reason.
- Evaluation is pure and deterministic with deny-overrides-allow semantics:
  an item in a deny set is denied; otherwise a non-empty allow set restricts to
  its members; otherwise the item is allowed.

### 26. Attachment & evaluation
- `TaskSpecification` (`task.v5`) may carry an optional `policy`.
- The Manager evaluates the policy **before** any agent is instantiated or any
  stage begins (immediately after the durable trajectory is begun), so a
  violation produces an explicit, audited failure and no partial execution.

### 27. Enforcement points
- Agent / capability selection must pass policy (single-agent tasks and every
  pipeline stage).
- Tool grants must pass policy: a tool listed in `granted_tools` but denied by
  policy is rejected before execution.
- Sequential composition: every stage's agent/capability and granted tools must
  satisfy the policy.

### 28. Trajectory / audit
- On acceptance, a durable `policy accepted` step records the checked
  constraints.
- On rejection, a durable `policy rejected` step records the constraint that
  was violated, and the outcome fails closed with `POLICY_VIOLATION`.

## Explicit non-goals honored (Volley 008)

- Complex rule engines, priorities, or inheritance
- Runtime / dynamic policy mutation by agents
- Parallel composition
- Identity, authentication, or multi-tenancy systems
- Learned or probabilistic policies
- External policy stores

## Definition of Done — confirmed (Volley 008)

1. ✅ Tasks and compositions can carry a thin policy the Manager enforces
   before work begins.
2. ✅ Violations are fail-closed, explicit, and fully audited.
3. ✅ All prior invariants hold.
4. ✅ New tests provide clear evidence of allow/deny paths for agents,
   capabilities, and tools.
5. ✅ This `STATUS.md` is accurate.

## Contract versioning notes (Volley 008)

- `task.v4` → `task.v5` (additive): adds optional `policy`.
- New `FailureReason.POLICY_VIOLATION` for explicit, audited policy rejection.
- Versioned contracts remain immutable and validated in `__post_init__`;
  unsupported versions are rejected.

## Correctness evidence (Volley 008 state)

Automated tests prove all invariants. All pass:

```
125 passed in 0.7s          # pytest (was 105)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 22 source files)
```

New `tests/test_policy.py` (20 tests) proves the policy contract (including
deny-overrides-allow), allowed work proceeding for agents, capabilities, and
tools, denied agents/capabilities being rejected before any execution, denied
tools being rejected even when granted, `policy accepted` appearing in the
durable trajectory, every stage of a composition being checked against the
policy before any stage begins, and that the absence of a policy preserves
current behaviour.

# Volley 009 — Cooperative Cancellation & Envelope Exhaustion (delivered)

### 29. Cancellation model
- A minimal, explicit cooperative signal — the ``Cancelled`` dataclass in
  ``agents/interface.py`` — is delivered to a running agent as the value of its
  generator's ``yield`` when a stage or composition envelope is exhausted. The
  agent may observe it and exit cleanly; it is purely advisory and cooperative.
- The Manager remains the sole authority that decides when cancellation occurs.
  The interface is typed so the ``send`` channel accepts a ``ToolResult`` or a
  ``Cancelled`` signal.

### 30. Envelope exhaustion behaviour
- When a stage or composition envelope (steps or time) is exhausted, the
  Manager now cancels the current agent rather than only failing after the
  fact: it records a ``CANCELLED`` step, delivers the cooperative signal, and
  then fails the run regardless of what the agent does (a non-cooperative agent
  still ends fail-closed).
- The failure reason is preserved as the existing distinct limit reasons
  (``STEP_LIMIT`` / ``TIMEOUT``), the causal envelope-bound, while the
  trajectory explicitly records the cancellation.

### 31. Trajectory & semantics
- A durable ``CANCELLED`` step (status ``cancelled``, description
  ``agent cancelled``) records that cancellation was requested and that the
  agent stopped.
- No verified success is ever returned after cancellation. Partial work already
  recorded remains in the trajectory; the outcome is an explicit failure.

## Explicit non-goals honored (Volley 009)

- Pre-emptive hard killing of threads/processes (cooperative first)
- Parallel composition
- Agent-initiated cancellation of other agents
- Complex cancellation hierarchies or priorities
- Distributed cancellation protocols

## Definition of Done — confirmed (Volley 009)

1. ✅ The Manager cooperatively cancels a running agent when an envelope is
   exhausted.
2. ✅ Cancellation is explicit, audited, and never yields an unverified success.
3. ✅ All prior invariants hold.
4. ✅ New tests provide clear evidence of the cancellation paths.
5. ✅ This `STATUS.md` is accurate.

## Contract versioning notes (Volley 009)

- New ``Cancelled`` cooperative signal in the agent interface; the agent
  generator's send channel now accepts ``ToolResult | None | Cancelled``.
- New ``StepStatus.CANCELLED`` for recording cancellations in the trajectory.
- New ``FailureReason.CANCELLED`` (available for explicit cancellation paths);
  envelope-exhaustion failures keep their causal `STEP_LIMIT` / `TIMEOUT`
  reasons.
- No breakage to existing agents: the cooperative signal is advisory and
  delivered only at the point the Manager has already decided to terminate.

## Correctness evidence (Volley 009 state)

Automated tests prove all invariants. All pass:

```
135 passed in 0.9s          # pytest (was 125)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 22 source files)
```

New `tests/test_cancellation.py` (10 tests) proves step-limit and timeout
exhaustion cancel a cooperative agent with a durable `cancelled` record, that
an agent which cooperates stops without producing a success outcome, that a
non-cooperative agent is still terminated fail-closed, that cancellation never
yields an unverified success, that a stage envelope exhaustion aborts a
composition before later stages begin, that prior limit-failure semantics are
unchanged, and that cancellation is deterministic and leaves partial work
recorded.

# Volley 010 — Manager-Orchestrated Parallel Composition (Fan-out / Join) (delivered)

### 32. Parallel composition model
- `contracts/parallel.py` defines a versioned `ParallelComposition`
  (`parallel.v1`): an ordered, non-empty set of independent `StageSpec` stages
  that may run concurrently. Each stage reuses the full sequential-stage
  capabilities (selection, tool grants, optional per-stage envelope, optional
  output/input schemas).
- `TaskSpecification` (`task.v6`) may carry exactly one of a single-agent
  selector, a sequential `pipeline`, or a `parallel` composition (validated
  mutually exclusive).

### 33. Manager orchestration
- The Manager resolves every stage up front (unknown agents abort before any
  thread runs), records a `parallel group begin` marker and a per-stage
  `parallel stage N begin` marker, then dispatches each stage to a worker
  thread sharing the same append-only trajectory.
- A lock serialises durable step appends so step indices stay globally ordered
  and reconstructible even under concurrent stage threads.
- Policy, per-stage envelopes, tool mediation, and the mandatory verification
  gate all apply per stage exactly as for sequential stages.

### 34. Failure semantics & join
- On any stage failure (verification, policy, envelope exhaustion, cancellation,
  unknown agent, or internal), the Manager sets a shared cancel `Event` so
  remaining running siblings cooperatively cancel, then fails closed with a
  single terminal failure. No partial success is returned.
- Only if every stage succeeds and verifies does the Manager produce the
  deterministic join: an ordered list of `(stage_index, agent, output)` entries
  in declared stage order.

### 35. Trajectory continuity
- One coherent, durable, reconstructible trajectory for the whole composition,
  with explicit `parallel group begin`, per-stage, and `parallel group end`
  markers. Step indices are contiguous and ordered.

## Explicit non-goals honored (Volley 010)

- Dynamic fan-out size based on runtime data
- Partial success / best-effort join modes
- Agent-to-agent messaging or coordination
- Complex reduction functions beyond a simple deterministic join
- Distributed execution across machines
- Critical Path Method (still deferred)

## Definition of Done — confirmed (Volley 010)

1. ✅ The Manager executes a parallel composition under full governance.
2. ✅ Success yields a deterministic join; any failure cancels siblings and
   fails closed with a complete audit record.
3. ✅ All prior invariants hold.
4. ✅ New tests provide clear evidence of the success and failure paths.
5. ✅ This `STATUS.md` is accurate.

## Contract versioning notes (Volley 010)

- `task.v5` → `task.v6` (additive): adds optional `parallel`. A task carries
  exactly one of single-agent selector, `pipeline`, or `parallel`.
- New `contracts/parallel.py` (`parallel.v1`).
- Concurrency uses worker threads for agent computation only; all governance
  (selection, policy, envelopes, tool mediation, verification, join) remains in
  the deterministic control plane. Step appends are serialised under a lock for
  a coherent, reconstructible trajectory.

## Correctness evidence (Volley 010 state)

Automated tests prove all invariants. All pass:

```
149 passed in 6.0s          # pytest (was 135)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 23 source files)
```

New `tests/test_parallel.py` (14 tests) proves the parallel contract
(empty/conflicting compositions rejected), all stages running and succeeding
with a deterministic join and full trajectory, the join preserving declared
order, one stage failure aborting the composition and cancelling siblings with
an audited failure and no success outcome, no partial success, unknown agents
aborting before any thread runs, policy and per-stage envelopes and
verification and tool mediation applying per stage, and the trajectory being
coherent, durable, and reconstructible.

# Volley 011 — Read-Only Critical Path Analysis (delivered)

### 36. Versioned CPM result contract

- `CpmVersion.V1 = "cpm.v1"`, `CpmMetric` (`ENVELOPE_MAX_STEPS`,
  `RECORDED_STEPS`)
- `CriticalPathStage(stage, agent, cost, slack, on_critical_path)`
- `CriticalPathResult(version, kind, metric, path, path_length, stages,
  assumptions)`, validated in `__post_init__`: `kind` must be
  `sequential` | `parallel`; at least one stage; unsupported versions rejected.
  All dataclasses are frozen and immutable.

### 37. Pure critical-path analysis

- `analyse_critical_path(plan, recorded_steps=None, parent_envelope=None)`
- Accepts `SequentialComposition`, `ParallelComposition`, or a
  `TaskSpecification` (which uses its own `envelope`). A bare composition
  requires an explicit `parent_envelope`; unsupported plans raise `TypeError`.

**Cost metric (documented and deterministic):** default is the effective stage
`max_steps` (the stage's `stage_envelope` if declared, else the parent).
`recorded_steps: dict[int, int]` overrides per-stage with recorded consumption
from a completed trajectory.

**Path semantics:**
- Sequential: the critical path is the full ordered sequence; path length is the
  sum of stage costs; every stage is on the path with zero slack.
- Parallel: the critical path is the stage(s) of greatest cost; path length is
  the greatest cost; every other stage has slack `max_cost - stage_cost`. Ties
  (multiple stages at the maximum cost) are all placed on the path.

**Read-only guarantee:** CPM is a pure, side-effect-free function. It never
mutates tasks, envelopes, schedules, or resource accounting, and never runs or
re-orders stages. In line with the directive, it is **observational only at this
stage**: it does not alter scheduling, execution, or resource enforcement.

### 38. Exports

- `meta_harness.contracts`: `CpmMetric, CpmVersion, CriticalPathResult,
  CriticalPathStage`
- `meta_harness.control_plane`: `analyse_critical_path`
- `meta_harness`: re-exports the above.

## Correctness evidence (Volley 011 state)

Automated tests prove all invariants. All pass:

```
161 passed in 6.0s          # pytest (was 149)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 25 source files)
```

New `tests/test_critical_path.py` (12 tests) proves the sequential full-order
path and its length as the sum of costs, task-with-pipeline plans, the parallel
most-costly stage with correct slack, equal-cost stages all on the path, single
stage (parallel and sequential) edge cases, the `recorded_steps` override
flipping the critical path, deterministic results for identical inputs, no
input mutation, side-effect-free purity, and `TypeError` on unsupported plans.

# Volley 012 — First Model-Mediated Agent (delivered)

### 39. Versioned model-provider contract

- `ModelProviderVersion.V1 = "model.v1"`
- `ModelProvider` — a thin, swappable interface: prompt in, text out, plus
  optional token metadata. It is the *only* place a language model is invoked,
  and it is reached exclusively through the Manager-mediated `llm_complete`
  tool.
- `ModelResponse(text, estimated_tokens=None)` and `ModelProviderError` (an
  explicit, auditable failure).
- A model call is an untrusted, stochastic step: nothing in the contract
  implies trust. Model output alone is never a verified result.

### 40. Providers (stub + optional real)

- `StubModelProvider` — deterministic, scripted responses; the only provider
  the test suite uses. No network access or API key is ever required.
- `FailingStubModelProvider` — always raises `ModelProviderError`, to prove a
  provider failure is explicit and fails closed.
- `OptionalRealModelProvider` — a thin adapter behind the same interface,
  **disabled by default** and never required to run the tests.

### 41. Mediated model tool

- `llm_complete` is a normal mediated tool registered with the `ToolRegistry`.
  It is only registered (and thus only grantable) when an explicit
  `ModelProvider` is supplied — model use is opt-in and fail-closed.
- An agent may call the model only if `llm_complete` is explicitly granted in
  the task envelope. Every request and response (or failure) appears as
  ordered steps in the durable trajectory.

### 42. Concrete model-using agent

- `ModelAgent` (`agents/model_agent.py`) answers a constrained prompt via the
  mediated `llm_complete` tool. It is minimal, and its verification rule is
  explicit and testable against the stub provider.
- The Manager's mandatory verification gate still applies: the agent's output
  must match the expected response. An ungranted or failed model call (agent
  returns `UNVERIFIED`) is rejected by the gate.

### 43. Governance invariants (hold for stochastic steps)

- Resource envelopes (steps/time) still apply and can cancel the agent.
- Policy can allow/deny the model tool, the model agent, and its capability.
- The final verification gate still applies; model output alone is never
  sufficient for success.
- Failures (provider error, timeout, policy, verification) are explicit and
  audited; the trajectory remains reconstructible.

## Correctness evidence (Volley 012 state)

Automated tests prove all invariants. All pass:

```
180 passed in 6.0s          # pytest (was 161)
All checks passed!          # ruff
Success: no issues found    # mypy (strict, 28 source files)
```

New `tests/test_model_agent.py` (19 tests) proves the provider contract and
stub determinism, the `llm_complete` tool being absent without a provider and
present with one, the model tool being unusable unless granted, request and
response (and ungranted-request rejection) being recorded in the trajectory,
envelope exhaustion cancelling the model agent, policy denying the model tool /
agent / capability, verification failure after model use failing closed,
provider failure being explicit and fail-closed, and deterministic replayable
trajectories under the stub provider.

# Volley 013 — Subprocess Isolation for Agent Execution (delivered)

### 44. Execution-backend abstraction

Agent execution now flows through a uniform :class:`AgentSession` interface
(``next_step(sent) -> step|request|result``, ``cancel(reason)``, ``close()``)
provided by an :class:`ExecutionBackend`. The Manager ``__init__`` accepts an
optional ``backend`` (default :class:`InProcessBackend`); all existing
authority — tool mediation, policy, envelopes, trajectory recording,
verification, cancellation — stays in the Manager and is unchanged.

### 45. Versioned IPC protocol

:class:`SubprocessBackend` spawns a child (``python -u -m
meta_harness.control_plane.worker``) and communicates over a minimal, explicit,
versioned JSON-lines protocol (``IPC_VERSION = "agent-ipc.v1"``) on the child's
stdin/stdout. Codecs serialize every sent value (``None``/``ToolResult``/
``Cancelled``) and every yield (``AgentStep``/``ToolRequest``); tool grants are
shipped to the child as descriptors, never executable implementations.

### 46. Child does only the agent loop

The child (:mod:`meta_harness.control_plane.worker`) runs solely the agent's
generator loop. It never mediates tools, applies policy, enforces envelopes,
records the trajectory, or verifies output. It is intentionally not imported by
the Manager, so it is executed once as ``__main__`` and class identity across
the IPC boundary is preserved.

### 47. Fail-closed child crash handling

A child crash, non-zero exit, or protocol violation surfaces as an explicit,
audited :class:`AGENT_ERROR` failure with child stderr captured for diagnostics.
No verified success is ever produced from an unverified agent outcome.

### 48. Cancellation termination as last resort

Cooperative cancellation is delivered across the boundary as a ``Cancelled``
send. If the child ignores it, the Manager enforces the envelope and terminates
the child as a last resort, recording the cancellation honestly.

## Correctness evidence (Volley 013 state)

- ``uv run pytest`` → **190 passed** (180 prior + 10 new subprocess tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 30 source files.
- The tool round-trip (`case_tool` with `to_upper` granted, subprocess explicit)
  returns `HELLO`; ungranted tools are still rejected by the Manager.
- Trajectories from subprocess runs are deterministic, coherent, ordered, and
  reconstructible, matching the in-process backend.
- Child crash and protocol-violation tests fail closed with an audited
  ``AGENT_ERROR``; policy is still enforced before any isolated work begins.

# Volley 014 — Trajectory Summary & Operator Inspection API (delivered)

### 49. Versioned summary contract

A new immutable, versioned contract (:mod:`meta_harness.contracts.summary`,
``summary.v1``) captures a minimal, stable operator view of a run:
``TrajectorySummary`` (trajectory id, task identity, terminal state, failure
reason/message, verified output, agents, stage kind, per-stage summaries, tool
summaries, model summary, step count, approximate time, policy decision,
cancellation count). Nested ``StageSummary`` / ``ToolSummary`` / ``ModelSummary``
/ ``PolicySummary`` records keep the surface small and typed.

### 50. Pure summary builder

:mod:`meta_harness.control_plane.summary` provides pure, side-effect-free
functions (``summarise_trajectory`` / ``summarise_stored``) that project a
loaded trajectory into a summary. They never read or write the trajectory store
and are deterministic for the same trajectory content. Tool and model calls are
derived from the Manager's stable, recorded description scheme; stage structure
is derived from the recorded stage-boundary markers.

### 51. Manager / store integration

``AgentManager.summarise(trajectory_id)`` loads a durable trajectory by id and
returns its summary (or ``None`` if absent). Summaries are computed on demand
and are not persisted by default, keeping the append-only audit log untouched.

### 52. Read-only, additive

Summaries never mutate stored trajectories or control-plane behaviour. They are
strictly additive: verification, mediation, envelopes, policy, and fail-closed
semantics are unchanged.

## Correctness evidence (Volley 014 state)

- ``uv run pytest`` → **206 passed** (190 prior + 16 new summary tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 32 source files.
- Successful single-agent, sequential, and parallel runs produce accurate
  summaries (state, agents, stages, tool/model counts).
- Failure, policy-rejection, cancellation, and tool-rejection paths are
  reflected correctly (failure reason, policy decision, cancellation count,
  rejected tool counts).
- Summaries are deterministic and do not modify stored trajectory data;
  missing optional fields (no policy, no model, no parallel) are handled
  cleanly.

# Volley 015 — Deterministic Trajectory Replay Verification (delivered)

### 53. Versioned replay result contract

A new immutable, versioned contract (:mod:`meta_harness.contracts.replay`,
``replay.v1``) captures the outcome of a replay check: ``ReplayResult``
(passed, original/replayed trajectory ids, structured ``ReplayDiff`` list, and a
human-readable message). The result is fail-closed: it reports ``passed`` only
when every equivalence rule holds, and otherwise carries structured diffs.

### 54. Explicit equivalence definition

:mod:`meta_harness.control_plane.replay` documents and enforces the equivalence
rules. A replayed run is equivalent to the stored trajectory when: (1) the
terminal outcome class matches (verified, or failed with the same failure
reason); (2) the verified output matches; (3) the ordered step sequence matches
for single-agent and sequential runs, while for parallel runs the *multiset* of
concurrent step signatures is compared (order among concurrent work is excluded
because it interleaves in the append-only log) and boundary markers are always
compared in order; (4) the ordered unique agents match; and (5) the per-tool
grant / rejection pattern matches. Wall-clock timings and other
non-deterministic fields are explicitly excluded and never cause a false
failure.

### 55. Manager replay API

``AgentManager.replay(task, trajectory_id)`` re-executes ``task`` under the
same deterministic configuration and compares the freshly produced trajectory
to the stored one, returning a ``ReplayResult``. The replayed run is recorded
under a new trajectory id; the original trajectory is never mutated. An
interrupted stored trajectory is reported as non-passing (it is not a
deterministic configuration).

### 56. Read-only, additive, fail-closed

Replay never mutates the original audit record and never relaxes verification
or mediation. It applies only to deterministic configurations (deterministic
agents and stub/fake providers); true stochastic model providers are out of
scope for bit-exact replay.

## Correctness evidence (Volley 015 state)

- ``uv run pytest`` → **220 passed** (206 prior + 14 new replay tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 34 source files.
- Successful single-agent, sequential, parallel, and model-agent runs replay as
  equivalent.
- Failure paths (verification failure, policy rejection, tool denial, step-limit
  cancellation) replay as equivalent when the setup is identical.
- Deliberate divergences (different output, different step sequence) are
  detected and reported with structured diffs.
- The original trajectory bytes/records are unchanged by replay; non-equivalent
  timings do not cause false failures.

# Volley 016 — Nested Composition: a Sequential Stage as a Parallel Group (delivered)

### 57. Versioned contract

``pipeline.v4`` (additive) allows a sequential stage to be either an agent stage
(a ``StageSpec``) or a nested parallel group (a ``ParallelComposition``).
``SequentialComposition.stages`` is now ``tuple[StageSpec | ParallelComposition,
...]``. ``__post_init__`` rejects nested parallel groups for versions earlier
than ``v4`` and keeps the existing up-level envelope/schema validation (guarded
with ``isinstance(stage, StageSpec)``). Depth is shallow by construction: a
``ParallelComposition`` accepts only ``StageSpec`` stages, so a parallel group's
stages are agent stages only.

### 58. Manager orchestration (reuse, not a new executor)

The existing parallel engine was refactored into a reusable
``_run_parallel_group(task, parallel, trajectory_id, steps, composition,
cancel_event, group_start, payload)`` that records the ``parallel group begin`` /
per-stage ``parallel stage N begin`` / ``parallel group end`` markers into a
shared ``steps`` list, dispatches the group's agent stages to worker threads,
cancels siblings on failure, and returns a deterministic join
``[(stage_index, agent, output), ...]``. ``_run_parallel`` is now a thin wrapper
that begins the trajectory, evaluates policy, calls ``_run_parallel_group``, and
seals success/failure. ``_run_pipeline`` iterates stages; a ``ParallelComposition``
stage calls ``_run_parallel_group`` (output ``{"stages": joined}``) and an agent
stage calls ``_execute_agent``. The nested group receives the previous stage's
handed-off output as its branch input, and the join is handed off to the next
sequential stage. A ``_stage_failure_to_outcome`` helper records a nested-group
failure (which is an unrecorded ``_StageFailure``) as the single terminal outcome
after persisting stage accounting; an agent-stage failure is already a
fully-recorded ``Outcome`` and is returned as-is after persisting accounting.

### 59. Hand-off, policy, and accounting

``_validate_handoff`` now accepts ``StageSpec | ParallelComposition`` producers
and consumers. A parallel-group producer has no declared ``output_schema``; its
join payload is validated against the implicit default shape
``{"stages": "list"}`` (instead of the agent-stage default ``{"text": "any"}``),
while a real ``output_schema``/``input_schema`` is applied only when the
producing/consuming stage is a ``StageSpec`` that declares one. Policy evaluation
recurses into nested ``ParallelComposition`` stages via ``_check_stage_policy``.
Resource accounting records each stage (agent or group) at its boundary and in
the final summary, including on abort.

### 60. Summary, replay, and critical path

- **Summary**: a nested run is ``sequential`` (the ``pipeline stage N begin``
  markers dominate); ``_compose_agents`` and ``_sequential_stages`` already
detect both ``pipeline stage`` and ``parallel stage`` / ``parallel group begin``
markers, so a nested trajectory attributes its agents correctly.
- **Replay**: ``_is_parallel`` returns True when any ``parallel stage`` /
``parallel group begin`` marker occurs, so a nested sequential-of-parallel run
uses the documented **multiset** rule for concurrent work (parallel-order
caveat) while boundary markers are always compared in order.
- **Critical path**: a nested ``ParallelComposition`` stage is handled cleanly —
its cost is the maximum of its branch costs (the critical path through the
group) and it is labelled ``group(branch1+branch2+...)``. CPM remains
observational only; CPM-driven scheduling stays out of scope.

## Correctness evidence (Volley 016 state)

- ``uv run pytest`` → **236 passed** (220 prior + 14 new nested-composition tests
  + 2 new nested critical-path tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 34 source files.
- ``pipeline.v4`` accepts a parallel group as a sequential stage; ``pipeline.v3``
  rejects it.
- seq → parallel group → seq succeeds with correct hand-off and join; the join
  dict is handed off intact to the next sequential stage (verified by a
  ``join_consumer`` agent + verifier).
- A failure inside a nested group aborts the outer sequence, cancels siblings,
  and is fully audited; unknown agents in a group abort before any work runs.
- Policy, per-stage envelopes, verification, and tool mediation all still hold
  for nested group stages.
- The nested trajectory is durable and reconstructible; summary attributes
  agents correctly; replay passes under the documented multiset rule.

# Volley 017 — Kernel Stabilisation & Public Surface Freeze Candidate (delivered)

### 61. Public API surface

The public surface is explicit and documented. ``meta_harness`` exports the
deliberate surface: ``AgentManager``, the core contracts (task, pipeline,
parallel, policy, result, trajectory, summary, replay, critical-path, model,
manifest, capability, tool), the execution backends (``InProcessBackend`` /
``SubprocessBackend``), the trajectory stores, ``summarise`` / ``replay`` /
``verify_replay``, and ``analyse_critical_path`` (CPM) as an optional
observational aid. Sub-package ``__init__`` files define ``__all__`` and avoid
leaking internal helpers (e.g. ``_StageFailure``, ``_CompositionLimit``, and the
Manager's private methods are not exported). ``py.typed`` marks the package as
type-annotated.

### 62. Package metadata & versioning

The package is versioned as a kernel milestone aligned with volley depth:
**0.16.0** (16 volleys delivered). The scheme is documented in ``KERNEL.md``.
Project metadata is accurate and minimal: name, description, Python requirement
(``>=3.13``), and a ``meta-harness`` console entry point wired to the CLI.

### 63. Minimal operator CLI

A local-only, fail-closed CLI (``meta-harness``, also ``python -m
meta_harness``) provides three commands over the public surface:

- ``run`` — execute the deterministic demo task set against a file-backed store
  and print each outcome and its durable trajectory id.
- ``summarise <trajectory_id>`` — load a durable trajectory and print its
  deterministic summary.
- ``replay-verify <trajectory_id>`` — re-run the demo task that produced the
  stored trajectory and verify the fresh trajectory is equivalent.

It never starts a network service, never reads credentials, and exits with a
non-zero status on any failure (fail-closed), with clear messages on stderr.

### 64. Kernel freeze note

``KERNEL.md`` records the v0 freeze boundaries: what the kernel guarantees
(correctness first, deterministic control plane, composition, full auditability,
fail-closed, observational aids, local-first), what is intentionally out of
scope (messaging fabric, MCP/A2A, Rust core, deep workflow graphs, multi-tenancy,
UI, CPM-driven scheduling), and that further work should prefer adapters and
backends over changing Manager semantics.

## Correctness evidence (Volley 017 state)

- ``uv run pytest`` → **243 passed** (236 prior + 7 new CLI smoke tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 36 source files.
- ``meta-harness run`` runs the full deterministic demo set and persists five
  durable trajectories; ``summarise`` and ``replay-verify`` work against them.
- Missing trajectories and non-demo tasks fail closed with a non-zero exit code
  and a clear message.
- No execution invariants changed; all prior tests continue to pass.

## Pause point — v0 checkpoint (Volley 017 accepted)

Volley 017 was accepted by the architect as the close of the v0 kernel phase.
The kernel was frozen at v0 and no further volley was in flight. The pause was
**lifted for Volley 018 only** (subprocess isolation hardening, below); the
kernel remains otherwise frozen and the standing no-push instruction holds.

- **Push:** the standing instruction is to **not push**; local ``main`` remains
  ahead of ``origin/main`` and unpushed.
- **Constraint:** do not expand composition or introduce messaging unless
  explicitly directed. No execution invariants change.

# Volley 018 — Subprocess Isolation Hardening (delivered)

### 65. Bounded child reads (silent-hang hardening)

``SubprocessSession`` reads are now bounded by the envelope deadline. A child
that stops responding (no further output, no crash) can no longer block the
Manager indefinitely: ``_read`` waits up to the remaining envelope time and then
raises ``SubprocessTimeoutError``, which the Manager maps to an explicit
``TIMEOUT`` failure and force-terminates the child as a last resort. This closes
the gap where a silent hung child bypassed the envelope timeouts that are only
checked between ``next_step`` calls.

### 66. Honest termination accounting

``SubprocessSession`` records how the child ended (``termination``):
``completed`` (produced a result and exited), ``cooperative`` (exited on a
cancel/close signal), or ``forced`` (had to be terminated/killed as a last
resort). The Manager records a distinct ``agent forcibly terminated`` step in
the trajectory only when the child had to be killed, so the audit record
distinguishes cooperative cancel from a forced kill. The existing ``agent
cancelled`` step is unchanged.

### 67. Bounded child lifecycle & cleanup

The child is now reaped on every path — success, failure, and cancellation —
via ``session.close()``, so no zombie survives a normal test path. ``close()``
first asks the child to exit cooperatively (up to a grace period), then
terminates it, and as a last resort kills it, each with a bounded wait.

### 68. Manager authority preserved

Tool mediation, policy checks, envelope accounting, and verification all remain
in the Manager process. The subprocess carries only the agent's generator loop;
no authority moved into the child. The in-process backend's behaviour is
unchanged (it accepts ``timeout_seconds`` for shared-interface clarity only).

## Correctness evidence (Volley 018 state)

- ``uv run pytest`` → **246 passed** (243 prior + 3 new subprocess lifecycle
tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 36 source files.
- New tests prove: a silent hung child is bounded fail-closed (``TIMEOUT``,
  forced kill recorded, partial work retained); a successful subprocess run
  reaps the child (no zombie, not force-killed); a cooperative cancel records
  ``agent cancelled`` with no forced-kill step.
- All prior invariants hold: crash and protocol violations remain ``AGENT_ERROR``
  fail-closed; success matches the in-process backend; no verified success after
  any isolation failure; prior trajectories are never mutated.

## Pause point — Volley 018 accepted

Volley 018 was accepted by the architect as a completed isolation milestone.
This was a deliberate **pause point**; no volley was in flight. The pause was
**lifted for Volley 019 only** (a thin Manager-mediated MCP adapter, below); the
kernel remains otherwise frozen and the standing no-push instruction holds.

- **Kernel:** local ``main`` includes Volleys 001–018; isolation hardening is
  complete.
- **Push:** the standing instruction is to **not push**; local ``main`` is ahead
  of ``origin/main`` and unpushed.
- **Constraint:** do not expand composition or introduce messaging unless
  explicitly directed. No execution invariants change.

# Volley 019 — Thin MCP Tool Adapter (Manager-Mediated) (delivered)

### 69. Adapter boundary

``McpToolAdapter`` maps a single MCP server's tools into the existing
``ToolDescriptor`` model: it lists tools for grant/policy discovery and executes
a named tool call with a bounded timeout, returning a structured result or an
explicit :class:`McpToolError`. It sits behind the ``McpGateway`` interface so
tests use an in-process fake. ``LocalMcpServer`` is both the v1 local transport
and the fake MCP double — no real network is required in CI.

### 70. Manager-mediated integration

MCP tools are registered into ``ToolRegistry`` only via the explicit, opt-in
``register_mcp(adapter)``. They are then subject to the exact same paths as
local tools: an agent receives them only when listed in ``granted_tools`` and
allowed by policy; every request/response (or failure) is recorded as normal
mediated tool steps; timeouts and envelope consumption apply identically.
Agents never talk to the MCP server directly.

### 71. Fail-closed failure behaviour

Server unavailable, protocol error, tool error, and timeout all surface as an
``McpToolError`` converted by ``ToolRegistry`` into an explicit, audited tool
failure — never a verified success. MCP output remains untrusted until it passes
the mandatory verification gate (re-confirmed): data returned from an MCP call
alone is never sufficient for a verified result.

## Correctness evidence (Volley 019 state)

- ``uv run pytest`` → **254 passed** (246 prior + 8 new MCP adapter tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 37 source files.
- New tests prove: ungranted MCP tool cannot be used (rejected, fail-closed); a
granted MCP tool round-trips and is durably trajectory-recorded; policy can
deny an MCP tool before any work runs; server/tool error, timeout, and an
unavailable server are all audited and fail-closed; the adapter boundary raises
explicit ``McpProtocolError``/``McpTimeoutError``.
- All prior invariants hold: local tools and the full control-plane suite pass
  unchanged.

## Kernel complete — minimalist working v0.19 (pause)

Volleys 001–019 are accepted. The minimalist working kernel is declared
**complete**: a deterministic, governed, verifiable agent harness covering the
Manager, versioned contracts, registry, policy, tools (local + MCP), a
stub-tested model path, composition (single / sequential / parallel / nested),
isolation, cancellation, envelopes, durable trajectories, summary, replay,
read-only CPM, and an operator CLI.

- The pause was **lifted for Volley 020 only** (optional real model-provider
  hardening, below) once a concrete, use-driven enhancement was named; the
  kernel remains otherwise frozen and the standing no-push instruction holds.
- **Push:** the standing instruction is to **not push**; local ``main`` is ahead
  of ``origin/main`` and unpushed.
- **Resumption:** further volleys will not be opened unless a concrete
  enhancement driven by use is named (still scoped to adapters/backends per
  ``KERNEL.md``; no expansion of composition or messaging unless explicitly
  directed).

# Volley 020 — Optional Real Model Provider Hardening (delivered)

### 72. Explicit opt-in, fail-closed configuration

``OptionalRealModelProvider`` is **disabled by default**: invoking a provider
built without opt-in raises an explicit ``ModelProviderError``. The
``build_real_model_provider`` factory validates configuration fail-closed:
requesting a real provider without an ``endpoint`` or without credentials
(``api_key`` / ``auth_header``) raises a clear error. A provider built without
an injected ``http_client`` never reaches the network (it fails closed on call).

### 73. Behavioural bounds & error mapping

Real calls are bounded by a timeout and mapped into the existing tool/envelope
failure paths: a timeout or HTTP/API error surfaces as an explicit
``ModelProviderError`` (never a verified success), and the Manager records it
as a normal mediated-tool failure. Trajectory content handling is unchanged.

### 74. Secret redaction

``redact_secrets`` scrubs concrete credential values from anything the real
provider raises, and the wrapper redacts its ``secret_values`` before any error
message can surface — so secrets never leak into logs or trajectories. No
secrets are stored in the repo or recorded.

## Correctness evidence (Volley 020 state)

- ``uv run pytest`` → **262 passed** (254 prior + 8 new real-provider tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 37 source files.
- New tests prove: real provider disabled by default; missing endpoint and
  missing credentials fail closed; a provider without a transport never reaches
the network; HTTP/API errors and timeouts map fail-closed; secrets are redacted
from error messages.
- All prior invariants hold: CI uses the deterministic stub only (no network);
  the full control-plane suite passes unchanged.

## Pause point — Volley 020 accepted

Volley 020 was accepted; the optional real-provider path is hardened (explicit
opt-in, fail-closed on config/transport/timeout, secrets redacted). This is a
small **post-kernel hardening** step: the kernel-complete v0.20 baseline stands
with additive hardening and no volley in flight.

- **Baseline:** kernel-complete v0.20 + optional real-provider hardening.
- **Push:** the standing instruction is to **not push**; local ``main`` is ahead
  of ``origin/main`` and unpushed.
- **Resumption:** Volley 021 will not be opened unless a concrete, use-driven
  need appears (still scoped to adapters/backends per ``KERNEL.md``; no
  expansion of composition or messaging unless explicitly directed).

# Volley 021 — Thin ACP Adapter (Zed ↔ AgentManager) (delivered)

### 75. ACP adapter boundary

A thin ACP transport (``meta_harness.acp``) exposes Meta-Harness as an External
Agent in Zed. It uses the official ``agent-client-protocol`` Python SDK and
talks ACP over stdio (``initialize`` / ``session/new`` / ``session/prompt`` /
``session/cancel``). ACP is an edge transport only: the Agent Manager remains
the sole authority for policy, tool mediation, envelopes, verification, and
audit. No ACP path can produce a verified success that bypasses the Manager.

### 76. Manager-mediated prompt path

One process owns one Manager (built once, reused across sessions). A user prompt
is mapped to a deterministic demo ``TaskSpecification`` (``reverse``,
``upper``/case_tool, ``counter``, or stub ``model``), run through
``AgentManager.run(...)``, and the verified result (or a clear fail-closed
failure message) is streamed back as agent text with the trajectory id.
Cancellation is tracked per session; mid-run cancellation of the synchronous
run is documented as a v1 limitation.

### 77. Zed wiring & no-Zed CI

Configured via ``settings.json`` ``agent_servers`` launching ``meta-harness-acp``
(or ``python -m meta_harness.acp``). CI tests drive the adapter over the SDK's
in-memory transport with raw JSON-RPC — no Zed, no subprocess, no network.

## Correctness evidence (Volley 021 state)

- ``uv run pytest`` → **267 passed** (262 prior + 5 new ACP adapter tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 38 source files.
- New tests prove: a prompt maps to a governed run and streams the verified
  result; the ``upper`` tool path round-trips; a fail-closed outcome is reported
  explicitly (never a verified success); a cancelled session refuses a prompt;
  the Manager/demo agents are built once and reused. All prior invariants hold.

## Pause point — Volley 021 accepted

Volley 021 was accepted; Meta-Harness is usable from Zed as an External Agent
via the documented ``agent_servers`` / ``meta-harness-acp`` entry point. No
volley is in flight.

- **Status:** v0.21 baseline (thin ACP adapter, edge transport only).
- **Push:** the standing instruction is to **not push**; local ``main`` is ahead
  of ``origin/main`` and unpushed.
- **Known v1 limits:** demo task mapping and cancel-during-run constraints (as
documented) — not full coding-agent parity.
- **Resumption:** further volleys will not be opened until a concrete use
drives the next need (scoped to adapters/backends per ``KERNEL.md``).

# Volley 022 — Accounting Bills v1 (delivered)

### 78. Structured bills in → deterministic totals out

A narrow **bills specialty agent** (``bills``) accepts a structured bill and
produces deterministic totals. The input/output contracts live in
``meta_harness.contracts.bill`` (``BillLine``, ``Bill``, ``BillTotal``,
``bill.v1``). Money math is integer-only (minor units / cents) with an explicit
half-up rounding rule, so totals are exact and replayable — no cloud model is
involved. This is the first specialty agent and the first step of the planned
bills → workspace → email sequence; it is deliberately narrow and low
blast-radius.

### 79. Real verification (recompute; reject bad/missing data)

``verify_bills_output`` is a real, independent check: it re-derives the expected
``BillTotal`` from the task payload and compares it to the agent's output. Bad
or missing payload data (empty/missing ``lines``, non-integer or negative
quantities/prices, out-of-range rates) is rejected explicitly at the contract
boundary and by the verifier, so a malformed bill can never produce a verified
result.

### 80. Manager-mediated tool (``bill_total``)

A deterministic, side-effect-free ``bill_total`` tool is registered in the
``ToolRegistry`` and mediated exactly like every other tool: only usable when
explicitly granted, executed and recorded by the Manager, and never sufficient
on its own for a verified result (the verification gate still recomputes).

### 81. Fail closed; full trajectory; tests; docs; version 0.22.0

All failure modes (bad/missing data, verification mismatch, ungranted tool,
envelope exhaustion, policy denial, subprocess crash) are explicit, audited
failures. Every step and tool call is recorded in the durable trajectory.
Package version is bumped to **0.22.0**. No PDF, no email, no workspace
ontology, and no Mail-in-a-Box are introduced; this volley does not expand into
doc organization or email.

## Correctness evidence (Volley 022 state)

- ``uv run pytest`` → **293 passed** (267 prior + 26 new bills tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 40 source files.
- New tests prove: exact integer money math and half-up rounding; rejection of
  bad/missing bill data; real verification (recompute) that rejects a wrong or
  malformed output; the mediated ``bill_total`` tool (granted path verifies,
  ungranted path still verifies locally, tool interaction recorded); hard step
  envelopes; policy denial; deterministic replay; and subprocess-backend
  isolation. All prior invariants hold.

## Pause point — Volley 022 accepted

Volley 022 was accepted; the bills specialty agent is delivered and the planned
sequence (bills → workspace → email) is one step in. No volley is in flight.

- **Status:** v0.22 baseline (bills specialty agent, deterministic totals).
- **Push:** the standing instruction is to **not push**; local ``main`` is ahead
  of ``origin/main`` and unpushed.
- **Next (planned, not started):** Volley 023 — local workspace layout +
  allowlisted mediated file tools (issued by the Architect only after 022 is
  accepted).

# Volley 023 — Workspace (delivered)

### 82. Local workspace layout

A local, agent-centric workspace is introduced: a ``Workspace`` (a root
directory plus a ``WorkspaceLayout`` allowlist) plus a specialty ``workspace``
agent that operates on it through the Manager. The contracts live in
``meta_harness.contracts.workspace`` (``WorkspaceLayout``, ``WorkspaceEntry``,
``workspace.v1``). The allowlist is the single source of truth for what an
agent may touch; this is agent-centric structure **without** unsupervised
cleanup or broad filesystem powers.

### 83. Allowlisted, mediated file tools

The workspace tools (``list_workspace``, ``read_workspace_file``,
``write_workspace_file``, ``create_workspace_dir``) are registered in the
``ToolRegistry`` and mediated exactly like every other tool: only usable when
explicitly granted, executed and recorded by the Manager, and subject to policy
and envelopes. Every tool resolves the requested relative path against the
workspace root and **rejects any path not on the allowlist** (fail-closed).
There is no deletion, rename, or arbitrary traversal; writes require an
existing allowlisted parent directory (created explicitly via
``create_workspace_dir``). The workspace root itself is local-first and does
not reach outside its bounds.

### 84. Real verification

``verify_workspace_output`` is a real, independent check: it recomputes the
expected result from the task payload (operation, relative path, and for writes
the content) and compares it to the agent's output. A malformed payload, a
failed/disallowed tool call (agent returns ``None``), or a mismatched output is
rejected explicitly, so a failed or disallowed workspace operation can never
produce a verified result.

### 85. Fail closed; full trajectory; tests; docs; version 0.23.0

All failure modes (disallowed path, missing file/dir, ungranted tool, envelope
exhaustion, policy denial, subprocess crash) are explicit, audited failures.
Every tool call is recorded in the durable trajectory. Package version is
bumped to **0.23.0**. No email or Mail-in-a-Box is introduced; this volley does
not expand into doc organization or email.

## Correctness evidence (Volley 023 state)

- ``uv run pytest`` → **314 passed** (293 prior + 21 new workspace tests).
- ``uv run ruff check .`` → All checks passed.
- ``uv run mypy`` → Success: no issues found in 43 source files.
- New tests prove: layout/allowlist validation (rejects absolute, ``..``
  traversal, empty paths); write/read/list/create round-trips; writes require an
  existing parent; missing files fail closed; disallowed file/dir paths are
  rejected and never verify; the mediated workspace tools are registry-registered
  and a disallowed call raises a fail-closed tool error; the ``workspace`` agent
  verifies through granted tools, fails closed when the tool is ungranted or the
  path is disallowed, rejects bad payloads; hard step envelopes; policy denial;
  deterministic replay; and subprocess-backend isolation. All prior invariants
  hold.

## Pause point — Volley 023 accepted

Volley 023 was accepted; the workspace specialty agent is delivered. No volley
is in flight.

- **Status:** v0.23 baseline (workspace specialty agent, allowlisted file tools).
- **Push:** the standing instruction is to **not push**; local ``main`` is ahead
  of ``origin/main`` and unpushed.
- **Next (planned, not started):** Mail-in-a-Box read-only tool + email agent
  (list/fetch first, never send in v1; issued by the Architect only after 023 is
  accepted).

## Out of scope / future volleys (not started)

See ``KERNEL.md`` for the authoritative v0 freeze boundaries. Highlights:

- Agent-initiated spawning or delegation
- Cyclic or dynamic workflows
- Durable workflow engines / FBP networks
- CPM-driven scheduling: using the critical path to actively re-order,
  re-prioritize, or re-allocate resources during execution (analysis now
  exists; execution is still purely Manager-driven and CPM remains
  observational)
- Dynamic re-allocation of budgets at runtime
- Complex economic or priority-based scheduling
- External messaging
- Streaming model responses and multi-turn conversational memory
- Tool-calling loops inside the model provider itself
- RAG, embeddings, or external knowledge bases
- Production provider credential management
- MCP/A2A integration
- Distribution, networking, and cloud concerns
- Container/VM isolation of agent execution
- Network isolation / seccomp / sandbox profiles for agent execution
- Hardening subprocess isolation further (e.g. resource limits, seccomp, OS
  sandboxing) beyond the current process-boundary isolation