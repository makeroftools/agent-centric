# specs/holdout — author-blind acceptance scenarios

Scenarios in this directory are for the **validator only**. The authoring agent
must never read them, and at L3 this path is excluded from authoring-agent
tooling. That separation is what makes the gate real.

## Format

Markdown with YAML front-matter:

```yaml
---
id: HO-0001
service: cbp
feature: bills intake
priority: high
---
```

The body is plain-language behaviour the validator can execute against an
ephemeral deployment. Each scenario is run multiple times; a change passes only
on a supermajority (in the spirit of ref3: 3 runs, 2-of-3) and an overall
threshold.

## Status

No scenarios yet — the L3 validator that consumes them is not built (see
[`SPEC-0019`](../SPEC-0019-isolated-holdout-validator.md) for its spec and
[`docs/agent/verification.md`](../../docs/agent/verification.md)). This file
reserves the format and the boundary.
