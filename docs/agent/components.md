# docs/agent — adding a component (a skill)

In CBP **a skill is a component**
([`specs/SPEC-0002-cbp-component-architecture.md`](../../specs/SPEC-0002-cbp-component-architecture.md)).
Every agent-oriented component has exactly **one canonical form and one home**:

```
.agents/skills/<name>/SKILL.md
```

That form is discovered by opencode through the `skill` tool, mirrored for
Claude by the [`.claude/skills`](../../.claude/skills) symlink, and **enforced**
by [`tests/test_agent_conventions.py`](../../tests/test_agent_conventions.py) —
which **auto-discovers every skill**, so a new component cannot bypass the
convention. There is no per-skill allowlist to keep in sync.

## Scaffold a new component

```sh
./tools/new-skill.sh <name> [deterministic|suspect]
```

The scaffold writes a canonical `SKILL.md` (frontmatter plus the two required
sections) atomically. Fill in the placeholders, then validate:

```sh
uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py
```

## The contract the guard hard-wires

| Field / section | Rule |
| --- | --- |
| `name` | lowercase-hyphen, 1–64 chars, **equals the directory name**. |
| `description` | 1–1024 chars, specific enough to choose correctly. |
| `metadata.component` | the component the skill maps to for execution; **must equal the skill name** (a skill maps to a component of the same name). |
| `metadata.determinism` | `deterministic` or `suspect`. |
| `## Deterministic termination` | the command/gate the component ends in. |
| `## Non-determinism (suspect)` | what is suspect and how it is contained. |

A **suspect** component must terminate in a call to a deterministic process (a
command, a gate, or another component) — Law 8: no unverified success. Prose
points; the guard decides.

See the on-demand skill
[`.agents/skills/skill-authoring/SKILL.md`](../../.agents/skills/skill-authoring/SKILL.md).
