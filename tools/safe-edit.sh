#!/usr/bin/env bash
#
# safe-edit.sh — edit a file WITHOUT in-place mutation (PRINCIPLES Law 11).
#
# Mission-critical rule: a file is NEVER edited in place. The only permitted
# workflow is:
#   1. copy the target to a temporary file
#   2. make ALL changes in the temporary copy
#   3. `cp` the temp over the target (atomic, byte-complete)
#   4. remove the temp
#
# This script automates that exact workflow so there is no temptation (and no
# need) to ever edit a file in place.
#
# Usage:
#   ./tools/safe-edit.sh <file> [editor-command...]
#
#   <file>            the file you want to change (any file: source, test, doc)
#   [editor-command]  optional editor to open the temp copy. Defaults to
#                     $EDITOR, then $VISUAL, then `nano`, then `vi`.
#
# Exit codes:
#   0  file replaced successfully (temp removed)
#   1  usage error / target missing
#   2  editor failed or temp unchanged/-empty (target untouched, temp kept for safety)

set -euo pipefail

usage() { echo "usage: $0 <file> [-- editor-command...]" >&2; }

if [ "$#" -lt 1 ]; then usage; exit 1; fi

TARGET="$1"
shift

if [ ! -f "$TARGET" ]; then
  echo "safe-edit: target not found: $TARGET" >&2
  exit 1
fi

TMP="$(mktemp "${TARGET}.safeedit.XXXXXX")"
trap 'rm -f "$TMP"' EXIT   # remove temp on any exit (unless replaced)

cp "$TARGET" "$TMP"

EDITOR_CMD=("$@")
if [ "${#EDITOR_CMD[@]}" -eq 0 ]; then
  EDITOR_CMD=("${EDITOR:-${VISUAL:-nano}}")
fi

# Run the editor on the temp copy (NOT on the target).
"${EDITOR_CMD[@]}" "$TMP" || { echo "safe-edit: editor failed; target untouched" >&2; exit 2; }

# Guard: refuse an empty or truncated temp (would silently corrupt the target).
if [ ! -s "$TMP" ]; then
  echo "safe-edit: temp is empty; target untouched (kept at $TMP)" >&2
  exit 2
fi

# If nothing changed, treat as no-op (still honored as whole-file semantics).
if cmp -s "$TMP" "$TARGET"; then
  echo "safe-edit: no change."
  exit 0
fi

# Atomic, byte-complete replacement, then remove the temp.
cp "$TMP" "$TARGET"
rm -f "$TMP"
trap - EXIT
echo "safe-edit: replaced $TARGET (Law-11 whole-file write)."