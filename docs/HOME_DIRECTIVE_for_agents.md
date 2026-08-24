# AGENT DIRECTIVE — Home-Directory & Username Spelling

**Read this first. Follow it absolutely. It is mission-critical.**

You are a coding agent. A recurring bug in our work is a **one-letter spelling
mistake** in a path or username. It silently breaks every command, path, and
remote reference that uses it. The humans in this project have been bitten by it
repeatedly. This directive exists so you never repeat it.

---

## 1. The exact problem

There are two spellings that look almost identical but are **not** the same.
The letters are shown split apart so you can see exactly where they differ:

| Spelling (split)                  | Status  | Meaning                                   |
|-----------------------------------|---------|-------------------------------------------|
| `m-a-k-e-r-o-f-t-o-o-l-s`        | Correct | The real home username and GitHub user (12 letters). |
| `m-a-k-e-r-o-o-f-t-o-o-l-s`      | WRONG   | The bug: an extra `o` right after the `r` (13 letters). |

- The correct form is 12 letters and has one `o` in the middle.
- The wrong form is 13 letters: it has the letters doubled (`o-o`) right
  after the `r`. That doubling is the bug.

> Do **not** try to memorize the spelling. Your memory is exactly where the bug
> comes from. Follow the method in section 3 instead.

---

## 2. The hard rule

1. **Never hand-type a home directory path, a username, or any address that
   begins with the user's home folder.** Hand-typing is the source of the bug.
2. **Always refer to the home directory via the `$HOME` environment variable.**
   The real value is discovered at runtime; you never guess it.
3. **Never write the wrong spelling.** Whenever an address or path is needed:
   - resolve it from `$HOME`, or
   - derive it programmatically from the correct value — never from memory.

---

## 3. The foolproof method (use this every time)

### A. Get the correct value from `$HOME` — never type it

```sh
printf '%s\n' "$HOME"
```

This prints the real home directory. If you need just the username folder
name, use:

```sh
basename "$HOME"
```

Use the output of these as your source of truth. **Copy it, do not retype it.**

### B. When adding the spelling, guard it / derive it, don't type it

In code or scripts, derive the path from the environment. Fully resolved, with
built-in safety against reintroducing the bug:

```python
import os, pathlib
correct = pathlib.Path(os.path.expanduser("~")).name   # derived from $HOME
# Build the forbidden token from a non-adjacent fragment so the exact wrong
# string never appears as a literal in this file:
WRONG = "makeroo" + "ftools"
# ... use `correct` everywhere; assert WRONG is absent when you finish ...
```

In a shell script:

```sh
home="$(printf '%s' "$HOME")"          # runtime truth
base="$(basename "$home")"             # the username-folder name
```

### C. Verify with a self-check before you finish

Any guard or directive that asserts the wrong spelling is absent **must not
contain the wrong spelling literally** (or it will fail on itself). Build the
forbidden token from pieces:

```python
WRONG = "makeroo" + "ftools"    # assembled from pieces; never a typed literal
assert WRONG not in open(...).read()
```

---

## 4. The concrete checklist

Before you use **any** path or address that includes the home directory or a
username, ask:

- [ ] Did I get this from `$HOME`, or did I type it? **(typed = redo it)**
- [ ] Does it start with `/home/<correct-user>/` and not the bug form?
- [ ] Does the search/scan of the repo show **zero** occurrences of the wrong
      spelling?
- [ ] If I wrote a note or a guard, is the wrong spelling **absent** from the
      literal text of what I wrote?

If any answer is no, **fix it before continuing.**

---

## 5. The deeper habit

The mistake is not "one typo." It is the habit of reaching for a memorized
string when you should resolve it from the environment. Internalize this rule:

> **An address is a fact to be looked up, not a word to be typed.**

When you need the home directory, look it up. When you need a path, build from
`$HOME`. When you write something that must not contain the value, derive it
from pieces so the value never appears as a typed literal.

That one habit eliminates this whole class of bug.