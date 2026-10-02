# docs/agent — verification gates

Hand a loop only as much autonomy as is cheap, reliable, and hard to fake to
verify. **Verification, not generation, is the constraint on autonomy** — the
long-term target (L3, dark factory) is earned by verification strength, never by
enthusiasm.

## Gate inventory

| Gate | Kind | Deterministic? | Where |
| --- | --- | --- | --- |
| `ruff` | lint | yes | pre-commit + CI |
| `mypy src` | types | yes | pre-commit + CI |
| `agent-centric cbp-check` | readiness | yes | pre-commit + CI |
| `pytest` | invariant suite | yes | CI / agent |
| `holdout` | acceptance scenarios | yes | isolated validator ([SPEC-0019](../../specs/SPEC-0019-isolated-holdout-validator.md); built; awaits operator scenarios) |

## Principles

1. **The gate is the contract.** Encode rules as checks, not wiki pages.
2. **Codegen and validation are isolated.** The authoring agent never sees
   [`specs/holdout/`](../../specs/holdout/README.md) and never runs the validator.
   The validator's contract is [`SPEC-0019`](../../specs/SPEC-0019-isolated-holdout-validator.md).
3. **Fail closed.** A missing gate named by the active level means refuse, not
   proceed — never bypass a hook with `--no-verify`.

The gates are run by [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)
and [`.github/workflows/gates.yml`](../../.github/workflows/gates.yml). See the
on-demand skill
[`.agents/skills/verification-gates/SKILL.md`](../../.agents/skills/verification-gates/SKILL.md).
