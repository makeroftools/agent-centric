# tools/ — safe, Law-11 file editing

**PRINCIPLES Law 11 forbids in-place file edits, absolutely and forever.** These
scripts are the *provided solution* — the supported way to change any file
(source, test, doc, config) without ever mutating it in place.

## The workflow (both scripts implement it)

1. Copy (or write) the target's contents to a temporary file.
2. Make all changes in the temp copy.
3. `cp` the temp over the target — atomic, byte-complete, whole-file write.
4. `rm` the temp.

There is never a partial or in-place write. A file changes only by complete
replacement.

## `safe-edit.sh` — interactive editing (you/agent edits in an editor)

```sh
./tools/safe-edit.sh <file> [-- editor-command...]
```

Opens the temp copy in your editor (default `$EDITOR` / `$VISUAL` / `nano` /
`vi`). After you finish, it atomically replaces the target and removes the temp.

## `safe-replace.sh <file> — atomic replace from full content

```sh
./tools/safe-replace.sh <file> < new-content
somecmd | ./tools/safe-replace.sh <file>
```

Use when you have the complete new content (a temp file, a here-doc, or
generated output). Refuses empty input; replaces atomically; removes the temp.

## Example — the pattern I (the agent) use

"Write revised content to a temp, then move it over the target":

```sh
./tools/safe-replace.sh src/thing.py < /tmp/revised-content
```

This is how every document change in this repository is made. If you catch any
tool or agent editing a file in place, that is a Law-11 violation — stop it.

---

Note for the **authoring agent**: when you generate a file's *entire* new content
in one shot, the cleanest path is to write that content to a temp path, then
`cp` it over the target (or pipe it through `safe-replace.sh`). Do **not**
regenerate a file's interior into the target directly — that has corrupted files
in this project. Transport exact bytes via `cp`/`safe-replace.sh`.