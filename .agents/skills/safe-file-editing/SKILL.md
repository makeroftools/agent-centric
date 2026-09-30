---
name: safe-file-editing
description: Use when changing ANY file in this repository. Enforces Law 11 - whole-file atomic replacement via tools/safe-replace.sh; never in-place edits (opencode's edit/write/patch tools are denied in opencode.json).
license: MPL-2.0
compatibility: opencode
metadata:
  component: safe-file-editing
  determinism: suspect
---

# Safe file editing (Law 11 — no in-place edits)

Never edit a file in place. Replace the whole file, atomically, from a complete
temp copy.

## Do this

1. Produce the **complete** new content (temp file, here-doc, or generated output).
2. `./tools/safe-replace.sh <file> < new-content`
3. Interactive: `./tools/safe-edit.sh <file>`.
4. For a large existing file, transform the original deterministically into a
   temp, then `safe-replace` — never retype the file by hand (retyping drifts).

`opencode.json` sets `permission.edit = "deny"`. In opencode the `write`,
`edit`, and `patch` tools all map to that key, so every mutation must go through
`bash` and the primitives above. A brand-new file may be written directly.

## Deterministic termination

`./tools/safe-replace.sh <file>` writes a temp and `cp`s it over the target — a
byte-complete, atomic whole-file write. Confirm with `git diff`.

## Non-determinism (suspect)

Deciding *what* the new content should be is the non-deterministic (LLM) step.
The deterministic checks are the byte-complete replace plus `ruff`, `mypy`, and
`pytest` on the result. Never claim a change is correct without those checks.
