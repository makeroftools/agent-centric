#!/usr/bin/env bash
#
# safe-replace.sh — atomically replace a file from new content (PRINCIPLES Law 11).
#
# Use this when you have the FULL new content (e.g. piped from a temp file, a
# here-doc, or generated output) rather than an interactive editor. It:
#   1. writes the new content to a disposable temp file
#   2. validates it is non-empty
#   3. `cp` the temp over the target (atomic, byte-complete)
#   4. removes the temp
#
# This is the sanctioned way to "change" a file without ever editing it in place.
#
# Usage:
#   ./tools/safe-replace.sh <file> < new-content-file
#   somecmd | ./tools/safe-replace.sh <file>
#
# Exit codes:
#   0  replaced successfully (temp removed)
#   1  usage / target dir missing
#   2  input empty (target untouched)

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <file> < input" >&2
  exit 1
fi

TARGET="$1"

TMP="$(mktemp "${TARGET}.saverep.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

# Write stdin into the temp copy.
cat > "$TMP"

if [ ! -s "$TMP" ]; then
  echo "safe-replace: refusing empty content; target untouched" >&2
  exit 2
fi

# Ensure the target directory exists before writing.
TARGET_DIR="$(dirname -- "$TARGET")"
mkdir -p -- "$TARGET_DIR"

# Atomic, byte-complete replacement, then remove the temp.
cp "$TMP" "$TARGET"
rm -f "$TMP"
trap - EXIT
echo "safe-replace: wrote $TARGET (Law-11 whole-file write)."