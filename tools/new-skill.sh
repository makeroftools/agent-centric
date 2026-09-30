#!/usr/bin/env bash
#
# new-skill.sh — scaffold a canonical Agent Skill (a CBP component).
#
# A skill IS a component (SPEC-0002). Every component lives in exactly one
# canonical place: .agents/skills/<name>/SKILL.md. This script writes that file
# from the canonical template so a new component is BORN compliant — the
# convention guard (tests/test_agent_conventions.py) auto-discovers every skill
# under .agents/skills/, so a non-compliant component cannot be added.
#
# Law 11: this is a whole-file write. A brand-new file may be written directly;
# the content is first staged in a temp and then `cp`-ed into place.
#
# Usage:
#   ./tools/new-skill.sh <name> [deterministic|suspect]
#
# Exit codes:
#   0  skill scaffolded
#   1  usage error
#   2  invalid name or determinism
#   3  skill already exists (refuses to overwrite)
#   4  generation failed (target untouched)

set -euo pipefail

usage() { echo "usage: $0 <name> [deterministic|suspect]" >&2; }

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then usage; exit 1; fi

NAME="$1"
DETERMINISM="${2:-suspect}"

case "$DETERMINISM" in
  deterministic|suspect) ;;
  *) echo "new-skill: determinism must be 'deterministic' or 'suspect'" >&2; exit 2 ;;
esac

if ! printf '%s' "$NAME" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$'; then
  echo "new-skill: name must be lowercase-hyphen (^[a-z0-9]+(-[a-z0-9]+)*\$)" >&2
  exit 2
fi
if [ "${#NAME}" -gt 64 ]; then
  echo "new-skill: name must be 1-64 characters" >&2
  exit 2
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || printf '%s' "$PWD")"
DIR="$REPO_ROOT/.agents/skills/$NAME"
TARGET="$DIR/SKILL.md"

if [ -e "$TARGET" ]; then
  echo "new-skill: refusing to overwrite $TARGET" >&2
  exit 3
fi

mkdir -p -- "$DIR"
TMP="$(mktemp "$DIR/.SKILL.md.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

# Canonical template. Tokens __NAME__ / __DETERMINISM__ are substituted below.
# The two section headers and the metadata keys are the hard-wired contract.
cat > "$TMP" <<'TEMPLATE'
---
name: __NAME__
description: Use when <trigger>. <One or two sentences: what this component does and when to load it — specific enough to choose correctly, <=1024 chars.>
license: MPL-2.0
compatibility: opencode
metadata:
  component: __NAME__
  determinism: __DETERMINISM__
  terminates-in: <the deterministic component or command this skill terminates in>
---

# __NAME__

<What this component is for, and the rule or procedure it encodes.>

## Deterministic termination

<The exact command or gate this component terminates in — e.g.
`./tools/safe-replace.sh <file>`, `uv run agent-centric fbp-check`, or the
convention guard `uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py`.>

## Non-determinism (suspect)

<What is irreducibly non-deterministic here, and how it is contained, recorded,
or verified. If there is none, write: "None — this component is deterministic.">
TEMPLATE

sed -i "s/__NAME__/$NAME/g; s/__DETERMINISM__/$DETERMINISM/g" "$TMP"

if [ ! -s "$TMP" ]; then
  echo "new-skill: generated content is empty; target untouched" >&2
  exit 4
fi

# Atomic, byte-complete placement, then remove the temp.
cp "$TMP" "$TARGET"
rm -f "$TMP"
trap - EXIT

echo "new-skill: wrote $TARGET (Law-11 whole-file write)."
echo "new-skill: fill in the placeholders, then validate:"
echo "    uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py"
