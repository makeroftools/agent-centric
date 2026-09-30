# Levels — the operating modes

The active mode is `factory.level` in [`.agentfactory.toml`](../../.agentfactory.toml).
A mode sets **three axes**: *autonomy* (what the agent may do), *gates* (which
checks are hard), and *review* (where a human is required). `caps` (attempts /
tokens) are declared for L3 and enforced when the L3 machinery lands.

| Level | Name | Autonomy | Review | Suite | Merge |
| --- | --- | --- | --- | --- | --- |
| **L0** | harness-only | assist | human-all | agent may | no |
| **L1** | assisted | author | human-all | agent may | no |
| **L2** | light-factory | spec-driven | human-gate | agent + validator | no |
| **L3** | dark-factory | holdout | none-on-green | isolated validator | gated |

## The ladder

- **L0 — harness-only.** A human authors; the agent proposes. Gates: `ruff`, `mypy`. Every change is human-reviewed. The agent may run the suite (Law 12).
- **L1 — assisted (active default).** The agent authors full changes; a human reviews every one. Build-before-push: run `ruff`, `mypy`, `pytest`, and `fbp-check` before offering work. The agent may run the suite; the human reviews every change.
- **L2 — light-factory.** Work starts from a `specs/` spec. The agent may run `pytest`; an **isolated validator** independently confirms it, and a human holds the review gate. This is the "lit" factory: the light stays on at the review gate.
- **L3 — dark-factory (declared, gated).** Merge-ready on an isolated holdout-scenario pass, per ref3. **Not enabled**: it requires the validator substrate in [`verification.md`](verification.md), which does not exist yet. Selecting L3 while `status = "declared-gated"` is a hard error, not an experiment.

## Rules that apply at every level

1. The correctness spine never turns off: no unverified success, fail-closed, full audit.
2. [`PRINCIPLES.md`](../../PRINCIPLES.md) is supreme at every level.
3. A change may never operate below its per-path floor in `.agentfactory.toml`
   (e.g. `src/**` has `min_level = "L1"`).
4. Raising the level is a reviewed commit to `.agentfactory.toml`, not a prompt.

## Switching modes

Edit `.agentfactory.toml` `[factory] level`, run the guard, and let the operator
review the change like any other. If a gate named by the new level is missing,
the factory fails closed (`on_missing_gate = "fail-closed"`) rather than
silently downgrading.
