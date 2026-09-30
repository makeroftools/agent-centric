---
name: home-path-safety
description: Use whenever a path, username, or address that begins with the home directory is needed. Never hand-type it; always resolve from $HOME. Guards against the historical misspelling.
license: MPL-2.0
compatibility: opencode
metadata:
  component: home-path-safety
  determinism: deterministic
---

# Home-directory & username safety

A recurring bug is a one-letter misspelling of the home username. It silently
breaks every path and remote reference that uses it.

## The rule

1. **Never hand-type a home directory path or username.**
2. **Always resolve from `$HOME`:**
   ```sh
   printf '%s\n' "$HOME"
   basename "$HOME"
   ```
3. Build any guard for the wrong spelling from pieces so the wrong literal
   never appears in the file itself.

> An address is a fact to be looked up, not a word to be typed.

See [`docs/HOME_DIRECTIVE_for_agents.md`](../../../docs/HOME_DIRECTIVE_for_agents.md).

## Deterministic termination

`tests/test_repo_home_paths.py` scans every tracked file and fails on the
misspelled token or a hard-coded absolute home path (outside the single
allowlisted Zed example).

## Non-determinism (suspect)

Reaching for a memorized string instead of resolving `$HOME` is the suspect
step; the guard is the deterministic check.
