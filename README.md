# agent-centric

Agent-centric Meta-Harness: a deterministic control plane for governed,
verifiable agents.

This repository implements a minimal, local-first, in-process core that lets a
control plane (the **Agent Manager**) register agent components, select them by
explicit identity or by capability, execute a task under a strict resource
envelope, record a full **durable, append-only audit trajectory**, mediate all
tool access, and orchestrate **sequential multi-agent pipelines** — all while
enforcing resource bounds hard and returning only verified results or explicit
failures.

Composition is **entirely under deterministic Manager control**: agents never
gain the ability to spawn or directly invoke one another.

## Guiding principles

Read [`PRINCIPLES.md`](PRINCIPLES.md). It records the non-negotiable rules:
correctness first, deterministic control plane, agent-centric design,
progressive disclosure, local-first, full auditability, least privilege, and
explicit failure. These rules override all other considerations.

## Layout

```
PRINCIPLES.md          Non-negotiable governing rules
src/meta_harness/
  contracts/           Versioned, strongly-typed core contracts
    capability.py      Capability (structured, versioned)
    manifest.py        Agent Component Manifest
    task.py            Task Specification + Resource Envelope
    trajectory.py      Trajectory / Audit Record
    result.py          Verified Result / explicit Failure
  agents/
    interface.py       Thin agent interface (Agent protocol)
    counter.py         Concrete agent (character counter)
    reverse.py         Concrete agent (string reverser)
  control_plane/
    manager.py         Deterministic Agent Manager
    registry.py        In-process deterministic Registry
    tools.py           Deterministic tools + ToolRegistry (Manager-controlled)
    verifier.py        Mandatory verification gate
    trajectory_store.py Durable, append-only trajectory store
  agents/
    interface.py       Thin agent interface (Agent protocol)
    counter.py         Concrete agent (character counter)
    reverse.py         Concrete agent (string reverser)
    case_tool.py       Concrete agent using a mediated tool
  contracts/
    capability.py      Capability (structured, versioned)
    pipeline.py        Sequential composition (pipeline) contract
    parallel.py        Parallel composition (fan-out / join) contract
    critical_path.py   CPM result contract (versioned)
    tool.py            ToolDescriptor (versioned tool contract)
  control_plane/
    critical_path.py   Read-only critical-path analysis (pure function)
tests/                 Invariant tests (control plane, registry, contracts, store)
examples/demo.py       End-to-end demonstration
```

## Core contracts (versioned)

Every contract is immutable, strongly typed, and carries an explicit version
so it can evolve without silently breaking the correctness model:

- `Capability` — a structured, versioned declaration of what an agent can do,
  used for capability-based selection.
- `AgentComponentManifest` (`manifest.v2`) — immutable declaration of an agent
  component: identity, interface entry point, and structured capabilities.
- `TaskSpecification` (`task.v2`/`task.v3`) — a unit of work selecting an agent
  by exact `agent_name` OR by exact `capability` (mutually exclusive), carrying
  an opaque payload, a `ResourceEnvelope`, and (in `task.v3`) the explicitly
  granted tools `granted_tools`.
- `ResourceEnvelope` — hard bounds: overall timeout, step limit, optional
  per-step time budget.
- `ToolDescriptor` (`tool.v1`) — the versioned tool contract: name,
  description, input/output schemas, and execution semantics.
- `SequentialComposition` / `StageSpec` (`pipeline.v1`/`pipeline.v2`) — a
  Manager-orchestrated sequential pipeline of agent stages, selected by name or
  capability. In `pipeline.v2`, each stage may declare its own resource
  envelope.
- `ParallelComposition` (`parallel.v1`) — a Manager-orchestrated parallel
  fan-out of independent stages followed by a deterministic join.
- `Trajectory` (`trajectory.v1`) — ordered, reconstructible audit record of
  every step.
- `VerifiedResult` (`result.v1`) / `Failure` — the only two terminal states.
  There is no ambiguous third state.

## Sequential composition (pipelines)

A task may request a Manager-orchestrated sequential pipeline via the optional
`pipeline` field (`task.v4`):

- The Manager alone sequences the stages, hands off data, and manages each
  stage's lifecycle. Agents cannot spawn or directly invoke one another.
- Each stage runs as a fully governed single-agent execution under the shared
  resource envelope.
- The verified output of stage *n* is handed off as the input to stage *n+1*
  (a scalar output is wrapped into the `{"text": ...}` payload shape the harness
  agents expect — a minimal, explicit hand-off rule).
- Any failure or verification failure at a stage aborts the composition; the
  trajectory records exactly where and why it stopped.
- The whole run produces one coherent, durable trajectory with explicit stage
  boundaries.

### Per-stage resource envelopes & accounting (`pipeline.v2`)

- Each `StageSpec` may declare its own `stage_envelope`. If declared, it is
  enforced for that stage; otherwise the stage inherits the parent task
  envelope.
- The parent task envelope additionally bounds the whole composition (total
  steps and total wall-clock time), so both stage-level and composition-level
  limits are enforced.
- Resource consumption (steps and elapsed time) is recorded at stage boundaries
  and in a final `pipeline resource accounting` summary step, making
  consumption attributable to stages and inspectable in the durable trajectory.
- Exceeding a stage envelope or the overall envelope aborts the composition
  with a clear, audited failure.

### Schema-constrained stage hand-off (`pipeline.v3`)

- Each `StageSpec` may declare an `output_schema` and/or an `input_schema`.
  The schema format is minimal and consistent with the tool contract: a single
  expected type name (scalar payload) or a mapping of field name -> expected
  type name (object payload).
- After a stage produces a verified result, the Manager validates the handed-off
  payload against the producing stage's `output_schema` (if declared) and the
  consuming stage's `input_schema` (if declared) before constructing the next
  stage's input. Data that flows between stages is never trusted implicitly.
- A schema mismatch aborts the composition with an explicit, audited
  `HANDOFF_FAILED` failure; no schema-invalid data proceeds to the next stage.
- On success, a durable `stage N hand-off validated` step records the hand-off
  and the shape of the handed-off data (keys and value types).
- Stages that declare neither schema are validated under a documented
  conservative default: the handed-off payload must be a mapping (the shape the
  harness agents expect). Existing deterministic agents keep working unchanged.

## Policy-based governance (`policy.v1`)

A task or composition may carry a thin, deterministic `Policy` (`task.v5`) that
constrains what it is allowed to do before execution begins:

- Allow / deny specific agent names, exact capabilities, and tool names.
- Evaluation is pure and deterministic with deny-overrides-allow semantics: an
  item in a deny set is denied; otherwise a non-empty allow set restricts to its
  members; otherwise it is allowed.
- The Manager evaluates the policy **before any agent is instantiated or any
  stage begins** — for single-agent tasks, every pipeline stage's
  agent/capability and granted tools. A tool listed in `granted_tools` but
  denied by policy is rejected.
- On acceptance a durable `policy accepted` step is recorded; on rejection a
  durable `policy rejected` step records the violated constraint and the task
  fails closed with an explicit `POLICY_VIOLATION` failure. No restricted work
  ever starts.

## Parallel composition (fan-out / join) (`parallel.v1`)

A task may request a Manager-orchestrated parallel composition via the optional
`parallel` field (`task.v6`), mutually exclusive with a single agent or a
sequential `pipeline`:

- Independent stages run concurrently in worker threads; the Manager alone
  controls spawning, resource envelopes, cancellation of siblings on failure,
  verification of each branch, and the final join. Agents never gain the ability
  to invoke or coordinate with one another directly.
- Each parallel stage reuses the full `StageSpec` capabilities: exact selection
  (name or capability), tool grants, optional per-stage envelope, and optional
  output/input schemas. Policy, envelopes, tool mediation, and the verification
  gate all apply per stage.
- **Failure semantics (conservative):** if any stage fails verification, policy,
  envelope exhaustion, or cancellation, the Manager cooperatively cancels
  remaining running siblings and aborts the composition. No partial success is
  returned.
- **Join rule (minimal):** only if every stage succeeds and verifies does the
  Manager produce a deterministic join — an ordered list of
  `(stage_index, agent, output)` entries in declared stage order.
- One coherent, durable trajectory records `parallel group begin`, per-stage,
  and `parallel group end` markers. Step appends are serialised under a lock so
  the trajectory stays ordered and reconstructible.

## Read-only critical-path (CPM) analysis (`cpm.v1`)

The control plane also exposes `analyse_critical_path(plan, recorded_steps=None,
parent_envelope=None)` as a deterministic, **read-only observational aid**: it
identifies the longest dependency chain (the critical path) and per-stage
slack over a composition, and optionally over recorded consumption from a
completed trajectory.

- Accepts a `SequentialComposition`, `ParallelComposition`, or a
  `TaskSpecification` (bare compositions require an explicit `parent_envelope`).
- **Cost metric:** default is the effective stage `max_steps` (stage envelope if
declared, else parent); `recorded_steps` overrides per stage.
- Sequential: the full ordered sequence is the path; length is the sum of the
  costs; every stage is on the path with zero slack. Parallel: the path is the
  greatest-cost stage(s); length is that greatest cost; other stages have slack
  `max_cost - stage_cost` (ties all on the path).
- CPM is **purely observational**: it never mutates tasks, envelopes,
  schedules, or resource accounting, and never alters scheduling, execution, or
  resource enforcement.

## Mediated tool access

Agents can only request external capabilities **through the Agent Manager**;
no agent may reach outside its envelope directly.

- `contracts/tool.py` defines the versioned `ToolDescriptor` contract.
- `control_plane/tools.py` provides deterministic, side-effect-free local tools
  (`to_upper`, `add`) plus the `ToolRegistry`, the tightly controlled executor
  under the Manager.
- The agent's `ToolContext` exposes only the names/descriptors of tools
  explicitly granted for the task. To use a tool, an agent yields a
  `ToolRequest`; the Manager validates the grant, executes (or rejects),
  records the request and outcome as ordered steps, and sends the `ToolResult`
  back to the agent.
- Tool calls consume the task's step budget. Tool failures are explicit and
  recorded, and never produce an unverified success: the final verification
  gate still applies.

## Durable trajectory store

Trajectories are persisted through a durable, append-only store
(`control_plane/trajectory_store.py`):

- `FileTrajectoryStore` — a simple, inspectable, file-based store. Each
  trajectory is a JSON-lines file (one record per line) under a directory,
  written with `flush` + `fsync` per append so a crash cannot silently corrupt
  an already-appended record. A truncated or malformed record is *detected* on
  load.
- `InMemoryTrajectoryStore` — the default; same append-only semantics, not
  durable across restarts.

Every step and the terminal outcome are persisted through the store. A
verified result is returned only after its outcome is durably recorded; if the
store fails, the Manager fails closed with an explicit `INTERNAL` failure.
Trajectories are uniquely identified and fully reconstructible after process
restart via `manager.load(trajectory_id)`. A trajectory with steps but no
recorded outcome is reported as `interrupted` (detectable, never silent).

## Cooperative cancellation & envelope exhaustion

When a stage or composition envelope (steps or time) is exhausted, the Manager
cooperatively cancels the running agent rather than only failing after the
fact:

- The Manager delivers a `Cancelled` signal into the running agent's generator;
  a cooperative agent observes it and exits cleanly. No pre-emptive hard
  killing of threads/processes is used.
- The Manager remains the sole authority that decides when cancellation occurs.
- A durable `cancelled` step (`agent cancelled`) records that cancellation was
  requested and that the agent stopped.
- The agent is never allowed to return a unverified success after cancellation:
  the run fails with the causal `STEP_LIMIT` / `TIMEOUT` reason regardless of
  what the agent does next (a non-cooperative agent still ends fail-closed).
- Partial work already recorded remains in the trajectory; the outcome is an
  explicit failure.

## Quick start

```sh
uv sync --extra dev
uv run pytest                 # run the control-plane invariant tests
uv run ruff check .           # lint
uv run mypy                   # type check the source package
uv run python examples/demo.py
```

## Correctness guarantees

The control-plane tests (`tests/test_control_plane.py`) demonstrate that:

- A task is submitted and fully governed by the Manager.
- A verified result is returned only when the agent's output passes the
  mandatory verification gate.
- Resource bounds (step limit, overall timeout, per-step timeout) are actually
  enforced, not advisory.
- Every step is recorded in an ordered, reconstructible trajectory.
- Every failure mode is explicit, contained, and audited.
- The flow is deterministic and replayable: identical inputs reproduce the
  identical step sequence and outcome.

The registry tests (`tests/test_registry.py`) additionally demonstrate that:

- Multiple agent components can be registered.
- Lookup by explicit identity or by exact capability is deterministic and
  side-effect free.
- Duplicate names, capability conflicts, and invalid manifests are rejected.
- Capability-based selection is fully governed: envelopes, trajectory
  recording, verification, and failure semantics hold regardless of which
  agent is selected.

The durability tests (`tests/test_trajectory_store.py`) demonstrate that:

- Trajectories survive process restart (including a real subprocess boundary).
- Steps are append-only and reconstructible in order.
- A crash or interruption is detectable (an interrupted trajectory is reported
  explicitly, never silently corrupted).
- Replay and inspection of historical trajectories work correctly.
- Resource bounds, verification, and failure paths all produce correct durable
  records.

The tool tests (`tests/test_tools.py`) demonstrate that:

- Agents cannot use tools that were not granted (the Manager rejects them).
- Every tool interaction (request and result/failure) is present in the durable
  trajectory as first-class, ordered steps.
- Tool calls consume the task's step budget.
- Tool failures are explicit and recorded, and never produce an unverified
  success.
- The final verification gate still applies after tool use.
- Deterministic tools produce deterministic, replayable trajectories.

The pipeline tests (`tests/test_pipeline.py`) demonstrate that:

- Stages execute in the declared order.
- The output of one stage is handed off only after verification.
- Resource bounds are enforced across the whole composition.
- Failure at an intermediate stage aborts cleanly and is fully audited.
- The final result is verified and the complete trajectory is durable and
  reconstructible.
- Agents still cannot invoke one another directly.

The accounting tests (`tests/test_pipeline_accounting.py`) additionally
demonstrate that:

- Stage-specific envelopes are enforced.
- The overall composition envelope is still respected.
- Consumption is correctly recorded and attributable to stages.
- Exceeding either a stage or the overall limit aborts cleanly with a durable,
  inspectable record.
- Verified hand-off and ordering invariants remain intact.

## License

MPL-2.0 — see [`LICENSE`](LICENSE).