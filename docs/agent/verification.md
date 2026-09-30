# Verification — gates and the path to a dark factory

The constraint on autonomy is **verification**, not generation. You may hand a
loop only as much autonomy as is cheap, reliable, and hard to fake to verify.
This page defines the gates and the honest state of each.

## Gate inventory

| Gate | Kind | Deterministic? | Where |
| --- | --- | --- | --- |
| `ruff` | lint | yes | local + CI |
| `mypy src` | types | yes | local + CI |
| `fbp-check` | readiness | yes | local + CI |
| `pytest` | invariant suite | yes | operator / validator / CI |
| `holdout` | acceptance scenarios | yes | isolated validator only (not built) |

## Principles

1. **The gate is the contract.** Encode architecture rules as checks, not wiki pages.
2. **Codegen and validation are isolated.** The authoring agent never sees the
   holdout scenarios (`specs/holdout/`) and never runs the validator. Without
   that separation there is no gate — only theater.
3. **Fail closed.** A missing or errored gate blocks; `on_missing_gate = "fail-closed"`.
4. **Earned, not declared.** A level is raised only when its gates have a track
   record (in the spirit of ref3: pass rate, false-positive rate, override rate).

## L3 status: declared-gated

L3 (`can_merge = true`) is intentionally **not enabled**. Enabling it requires
the isolated validator substrate:

- a validator process/job that runs `pytest` and holdout scenarios,
- `specs/holdout/` excluded from all authoring-agent tooling,
- recorded per-run evidence (pass/fail, attempts, tokens) keyed for audit,
- a config switch to promote green runs, with the human able to demote at any time.

Until that exists, `holdout` is a *named future gate*, and L3 remains
`status = "declared-gated"`. Selecting L3 now is a hard error by design.

## Readiness gate

`uv run agent-centric fbp-check` proves the verified spine end-to-end (transport,
integrity, CURVE where configured) and prints `fbp-check: READY` or exits
non-zero. It is the deterministic gate CI hangs the factory on.
