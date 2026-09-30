# Editing — Law 11 (no in-place edits)

**Law 11 is absolute.** A file is never edited in place; it is replaced whole,
atomically, from a complete temp copy.

## The only sanctioned workflow

1. Produce the **complete** new content (a temp file, a here-doc, or generated output).
2. Replace the target atomically:
   ```sh
   ./tools/safe-replace.sh <file> < new-content
   somecmd | ./tools/safe-replace.sh <file>
   ```
   This writes a temp, refuses empty input, `cp`s it over the target, and removes the temp.
3. For interactive editing, use `./tools/safe-edit.sh <file>` (opens a temp in `$EDITOR`, then replaces).

There is never a partial or interior write. `cp` from a complete file is
byte-complete and cannot truncate.

## Enforcement in this harness

[`opencode.json`](../../opencode.json) sets `permission.edit = "deny"`. In
opencode, the `write`, `edit`, and `patch` tools all map to that one key, so
**all tool-based file mutation is blocked**. Every change must go through
`bash` and the primitives above. This is deliberate: the law is a permission,
not a suggestion.

Other harnesses (Claude Code, Cursor, …) do not read `opencode.json`; for them
this page and [`AGENTS.md`](../../AGENTS.md) are the contract, and a reviewer
rejects any in-place diff.

## Editing large existing files without retyping

To change a few lines in a large file, transform the original into a temp and
replace — never retype the file by hand (retyping drifts and has corrupted files
here before):

```sh
uv run python - <<'PY'
from pathlib import Path
p = Path("docs/big.md")
s = p.read_text()
s = s.replace("old text", "new text")
Path("/tmp/revised.md").write_text(s)
PY
./tools/safe-replace.sh docs/big.md < /tmp/revised.md
```

The transform is deterministic and reviewable; the write is still a single
whole-file `cp`.
