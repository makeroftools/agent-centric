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
| `holdout` | acceptance scenarios | yes | isolated validator ([SPEC-0019](../../specs/SPEC-0019-isolated-holdout-validator.md); `task` + `service` probes; built; awaits operator scenarios and the authoritative green) |

## Principles

1. **The gate is the contract.** Encode rules as checks, not wiki pages.
2. **Codegen and validation are isolated.** The authoring agent never sees
   [`specs/holdout/`](../../specs/holdout/README.md) and never runs the validator.
   The validator's contract is [`SPEC-0019`](../../specs/SPEC-0019-isolated-holdout-validator.md).
3. **Fail closed.** A missing gate named by the active level means refuse, not
   proceed — never bypass a hook with `--no-verify`.

## The holdout validator (`holdout`)

The validator core is the importable library
[`src/agent_centric/cbp/holdout.py`](../../src/agent_centric/cbp/holdout.py) behind the
thin, separate-process CLI [`tools/holdout-validate.py`](../../tools/holdout-validate.py).
It runs **only** in the `CBP_HOLDOUT_CONTEXT=validator` context and refuses on a
privileged principal (`uid 0` / the `docker` group) unless `--rehearsal` is given
(which records `isolation: rehearsal`; only `isolation: enforced` is
authoritative). Exit codes: `0` green, `1` red, `2` usage, `3` refused.
`holdout.lock` pins the suite, `probe_contract_sha256`, and `validator_sha256`;
every run appends a wall-clock-free, idempotent `holdout.record/v1` to the
operator-private ledger (`$CBP_HOLDOUT_ROOT`, default `$HOME/.cbp/holdout`). The
`service` probe boots a signed composition through the core harness using an
operator-private `holdout.deployment/v1` descriptor. Scenarios are
operator-authored and author-blind; the authoritative run and the level flip are
operator gates. See the [L3 milestone plan](l3-milestone-plan.md).

The gates are run by [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)
and [`.github/workflows/gates.yml`](../../.github/workflows/gates.yml). See the
on-demand skill
[`.agents/skills/verification-gates/SKILL.md`](../../.agents/skills/verification-gates/SKILL.md).
