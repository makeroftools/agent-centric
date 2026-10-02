---
name: verification-gates
description: Use when designing or judging a gate - lint, types, readiness, tests, or the future holdout validator. Verification, not generation, is the constraint on autonomy.
license: MPL-2.0
compatibility: opencode
metadata:
  component: verification-gates
  determinism: deterministic
---

# Verification gates

Hand a loop only as much autonomy as is cheap, reliable, and hard to fake to
verify.

## Gate inventory

| Gate | Kind | Deterministic? | Where |
| --- | --- | --- | --- |
| `ruff` | lint | yes | pre-commit + CI |
| `mypy src` | types | yes | pre-commit + CI |
| `agent-centric cbp-check` | readiness | yes | pre-commit + CI |
| `pytest` | invariant suite | yes | CI / agent |
| `holdout` | acceptance scenarios | yes | isolated validator ([SPEC-0019](../../../specs/SPEC-0019-isolated-holdout-validator.md); built) |

## Principles

1. **The gate is the contract.** Encode rules as checks, not wiki pages.
2. **Codegen and validation are isolated.** The authoring agent never sees
   `specs/holdout/` and never runs the validator.
3. **Fail closed.** A missing or errored gate blocks.
4. **Earned, not declared.** Raise a level only when its gates have a track record.

The validator is **built** ([SPEC-0019](../../../specs/SPEC-0019-isolated-holdout-validator.md));
real scenarios live in the **operator-private** root
(`$CBP_HOLDOUT_ROOT`), never in the public `specs/holdout/`. L3 (auto-merge)
stays `declared-gated` until the validator runs **green** and the operator flips
it deliberately.

## Deterministic termination

`uv run agent-centric cbp-check` proves the verified spine and exits non-zero on
any failure; `uv run pytest` proves the invariants.

## Non-determinism (suspect)

Choosing what to verify and what counts as exercised is the suspect step; the
gate's exit code is the deterministic answer.
