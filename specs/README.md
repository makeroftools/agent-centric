# specs/ — spec files

A **spec** is the first-class artifact of an L2+ change: a human expresses intent
here, and an agent implements against it without inventing scope. Specs are
versioned and reviewed like code.

## Format

Markdown with YAML front-matter:

```yaml
---
id: SPEC-0001
title: Short imperative title
type: feature | bug-fix | convention
target_repo: agent-centric
target_branch: main
status: draft | accepted | implemented
owner: operator
---
```

The body states: context, decision, scope (in/out), and acceptance criteria.

## Rules

1. One spec per change. If scope grows, write a new spec.
2. Acceptance criteria are observable and deterministic.
3. `specs/holdout/` holds acceptance **scenarios the authoring agent must never
   see** (see below). It is excluded from authoring-agent tooling at L3.
4. The spec does not override [`PRINCIPLES.md`](../PRINCIPLES.md).

See [`TEMPLATE.md`](TEMPLATE.md) to start one.

## `holdout/`

Holdout scenarios are plain-language acceptance checks with their own
front-matter (`service`, `feature`, `priority`). They exist so a validator can
judge a change the author could not game. Until the L3 validator lands, this
directory holds the format and a guard only.
