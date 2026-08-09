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
      execution.py       Agent session + execution backends (in-process/subprocess)
      worker.py          Subprocess worker entry (runs only the agent loop)
      summary.py         Deterministic, side-effect-free trajectory summary builder
      replay.py          Deterministic trajectory replay verification
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
    model.py           Model-provider contract (versioned)
    tool.py            ToolDescriptor (versioned tool contract)
    summary.py         Trajectory Summary contract (versioned)
    replay.py          Replay verification result contract (versioned)
  control_plane/
    critical_path.py   Read-only critical-path analysis (pure function)
  providers/
    __init__.py        Stub + optional real model providers
  agents/
    model_agent.py     Concrete model-using agent (mediated llm_complete)
tests/                 Invariant tests (control plane, registry, contracts, store)
examples/demo.py       End-to-end demonstration
```

## Operator CLI

The package ships a minimal, local-only, fail-closed operator CLI over the
public surface (installed as `meta-harness`):

```sh
meta-harness run                 # run the deterministic demo task set
meta-harness summarise <id>      # print a trajectory's deterministic summary
meta-harness replay-verify <id>  # re-run a demo task and verify equivalence
```

- `--store <dir>` selects the durable trajectory store (default
  `examples/.trajectories`) and must precede the subcommand.
- `run` executes five deterministic demo tasks (`demo-counter`, `demo-reverse`,
  `demo-tool`, `demo-model`, `demo-pipeline`) using only built-in agents and the
  stub model provider, printing each outcome and its durable trajectory id.
- `summarise` and `replay-verify` fail closed: a missing trajectory id (or a
  non-demo trajectory for `replay-verify`) exits non-zero with a clear stderr
  message.

It never starts a network service and never reads credentials. The same surface
is reachable via `python -m meta_harness`. See `KERNEL.md` for the v0 freeze
note.

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
- `ModelProvider` (`model.v1`) — the thin, versioned model-provider interface
  (prompt in, text out, optional token metadata), reached only through the
  mediated `llm_complete` tool.
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

### Nested composition: a sequential stage as a parallel group (`pipeline.v4`)

In `pipeline.v4` (additive), a sequential stage may be either an agent stage (a
`StageSpec`) or a nested parallel group (a `ParallelComposition`). This lets the
Manager express "run these branches concurrently, verify/join, then continue to
the next sequential stage":

- Composition stays Manager-orchestrated; agents cannot spawn or coordinate.
- Depth is shallow: a sequential stage may be a parallel group, but a parallel
group's stages are agent stages only.
- The nested group reuses the existing parallel engine; its branches receive the
  previous stage's handed-off output, and the deterministic join
  (`{"stages": [...]}`) is handed off to the next sequential stage.
- A failure inside a nested group aborts the outer sequence and cancels
  siblings, exactly as a top-level parallel failure does.
- Policy, per-stage envelopes, verification, tool mediation, and accounting all
  apply to nested group stages; the whole run shares one coherent trajectory
  with explicit `pipeline stage N begin` / `parallel group begin` / `parallel
  stage N begin` / `parallel group end` markers.
- A parallel-group producer has no declared `output_schema`; its join is
  validated against the implicit default shape `{"stages": "list"}`.
- Summary treats a nested run as sequential (of parallel); replay uses the
  documented multiset rule for the concurrent branch work; critical-path analysis
  gives a nested group a cost equal to the max of its branches.

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

## Model-mediated agents (`model.v1`)

A language-model call is an untrusted, stochastic step, governed by the same
invariants as every other action. The first model-using agent (`ModelAgent`)
answers a constrained prompt through a Manager-mediated tool:

- **Provider abstraction:** `ModelProvider` (`model.v1`) is a thin, swappable
  interface (prompt in, text out, optional token metadata). `StubModelProvider`
  is deterministic and is the only provider the tests use — no network access
  or API key is ever required. `OptionalRealModelProvider` is a thin adapter,
  disabled by default.
- **Mediated tool:** `llm_complete` is a normal mediated tool registered with
  the `ToolRegistry`. It is only registered (and thus only grantable) when an
  explicit `ModelProvider` is supplied, so model use is opt-in and fail-closed.
  An agent may call the model only if the tool is explicitly granted in the
  task envelope.
- **Recording:** every model request and response (or failure) appears as
  ordered steps in the durable trajectory.
- **Governance:** resource envelopes (steps/time) still apply and can cancel the
  agent; policy can allow/deny the model tool, agent, and capability; and the
  mandatory verification gate still applies — **model output alone is never a
  verified result**. An ungranted or failed model call fails closed.

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

## Subprocess isolation for agent execution

By default agents run in the Manager's own process. For stronger isolation, an
agent can be run in a separate child process via the `SubprocessBackend`:

```python
from meta_harness import AgentManager, SubprocessBackend

m = AgentManager(backend=SubprocessBackend())
```

- Only the agent's generator loop crosses the process boundary. Every authority
  — tool mediation, policy, envelopes, trajectory recording, verification, and
  cancellation — stays in the Manager.
- The Manager and child communicate over a minimal, explicit, versioned
  JSON-lines protocol (`agent-ipc.v1`) on the child's stdin/stdout. Tool grants
  are shipped as descriptors, never executable implementations.
- Isolation is strictly additive: it never relaxes verification or mediation. A
  child crash, non-zero exit, or protocol violation is an explicit, audited,
  fail-closed `AGENT_ERROR` failure — never a verified success.
- Reads are bounded by the envelope deadline: a child that stops responding
  (silent hang) cannot block the Manager. It is mapped to an explicit `TIMEOUT`
  failure and force-terminated as a last resort.
- Cooperative cancellation is delivered across the boundary; if the child
  ignores it, the Manager enforces the envelope and terminates the child as a
  last resort. The trajectory distinguishes cooperative cancel (`agent
  cancelled`) from a forced kill (`agent forcibly terminated`).
- The child is reaped on every path (success, failure, cancellation), so no
  zombie survives a normal test path.
- The in-process backend remains the default and is unchanged for unit tests.

## Trajectory summary & operator inspection

A deterministic, immutable summary of any durable trajectory is available on
`AgentManager.summarise(trajectory_id)` (or the pure `summarise_stored` /
`summarise_trajectory` functions):

```python
summary = manager.summarise(trajectory_id)
summary.state          # RunState.VERIFIED / FAILED / INTERRUPTED
summary.stage_kind     # StageKind.SINGLE / SEQUENTIAL / PARALLEL
summary.tools          # per-tool grant + request/success/failure/rejected counts
summary.models         # llm_complete call counts, if any
summary.policy         # accepted/rejected decision, if a policy was attached
summary.cancellations  # count of recorded cancelled steps
```

- Summaries are computed on demand and are **never persisted** by default; the
  append-only audit log is never mutated.
- The builder is a pure, side-effect-free function: it is deterministic for the
  same trajectory content and never reads or writes the store.
- It is strictly additive and read-only with respect to execution: it does not
  alter scheduling, resource enforcement, verification, or mediation.

## Deterministic trajectory replay verification

A deterministic run can be re-executed and checked for equivalence against its
stored trajectory via `AgentManager.replay(task, trajectory_id)`:

```python
result = manager.replay(task, trajectory_id)
result.passed   # True iff the replayed run is equivalent under the documented rules
result.diffs    # structured divergences when it fails
```

Equivalence is defined explicitly and excludes wall-clock timings:

- Same terminal outcome class (verified, or failed with the same failure
  reason) and same verified output when successful.
- Same ordered step sequence for single-agent and sequential runs; for parallel
  runs, the multiset of concurrent step signatures is compared (order among
  concurrent work is excluded) while boundary markers are compared in order.
- Same agents/selections and same tool grant/rejection pattern.

Replay is fail-closed and read-only with respect to the original trajectory: it
never mutates the audit record, and it applies only to deterministic
configurations (deterministic agents and stub/fake providers). An interrupted
stored trajectory is reported as non-passing.

## Quick start

```sh
uv sync --extra dev
uv run pytest                 # run the control-plane invariant tests
uv run ruff check .           # lint
uv run mypy                   # type check the source package
uv run python examples/demo.py
uv run meta-harness run          # operator CLI demo task set
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

The subprocess tests (`tests/test_subprocess.py`) demonstrate that:

- A subprocess run produces the same verified result and coherent, ordered,
  reconstructible trajectory as the in-process backend.
- Mediated tool access still works across the boundary: granted tools round-trip
  and ungranted tools are still rejected by the Manager.
- A child crash or protocol violation is an explicit, audited, fail-closed
  `AGENT_ERROR` failure, never a success.
- Envelope exhaustion and cooperative cancellation work across the boundary,
  including terminating a non-cooperative child as a last resort.
- Policy is still enforced before any isolated work begins.

The summary tests (`tests/test_summary.py`) demonstrate that:

- Successful single-agent, sequential, and parallel runs produce accurate
  summaries (state, agents, stages, tool/model counts).
- Failure, policy-rejection, cancellation, and tool-rejection paths are
  reflected correctly.
- Summaries are deterministic and never modify stored trajectory data.
- Missing optional fields (no policy, no model, no parallel) are handled
  cleanly.

The replay tests (`tests/test_replay.py`) demonstrate that:

- Successful single-agent, sequential, parallel, and model-agent runs replay as
  equivalent.
- Failure paths (verification failure, policy rejection, tool denial, step-limit
  cancellation) replay as equivalent when the setup is identical.
- Deliberate divergences are detected and reported with structured diffs.
- The original trajectory is never mutated by replay, and non-equivalent
  timings do not cause false failures.

## License

MPL-2.0 — see [`LICENSE`](LICENSE).