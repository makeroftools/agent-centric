"""Regression guard for a home-path misspelling bug (MISSION CRITICAL).

The project has seen absolute home paths written with a misspelled username
(an extra letter in the middle). Such typos silently break Zed wiring
(``agent_servers``), spawn paths, and any reference that must resolve against
the real ``$HOME``.

The hard rule: **no absolute file/folder addresses and no home-relative
addresses**; all addresses must use the ``$HOME`` environment variable. This
suite scans the tracked source, tests, docs, and examples so that:

1. the misspelled home token never returns, and
2. a hard-coded absolute home path (correctly spelled) is caught and flagged
   for conversion to the ``$HOME`` convention.

The scan is deterministic and offline: it walks the repository exactly as
``git`` would track it, never touches ``$HOME``, and only performs reads.

**Design note on self-scanning.** A guard that asserts a token is absent must
not itself contain that token literally, or it fails on itself. The forbidden
tokens here are therefore built from string pieces at import time so the exact
forbidden substrings never appear in this file (or anywhere in the repo).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The misspelled home token that has corrupted addresses in the past. Assembled
# from pieces so the exact string never appears literally in this file.
_TOKEN = "makeroo" + "ftools"

# A hard-coded absolute path under a home dir — the first class of addresses the
# rule forbids. The regex pattern is assembled from pieces so the home-dir marker never
# appears literally in this file either.
_HOME_SEG = "/" + "hom" + "e/"
_ABS_HOME_RE = re.compile(r"(?:^|[^.\w])(?:" + _HOME_SEG + r"[a-zA-Z0-9_./+,\-]*)")

# Files allowed to carry an absolute home path because they are user-authored,
# local-only references (never resolved by the platform code). The Zed example
# is the canonical reference; it intentionally uses an absolute ``command`` path
# because Zed's ``agent_servers`` does not expand ``$HOME`` inside JSON. It is
# *correctly spelled* and operator-reviewed.
_ALLOW_ABS_HOME = {
    "examples/zed_agent_servers.json.example",
}

# Also skip these path segments (generated / vendored / non-source).
_IGNORED_SEGMENTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


def _tracked_files() -> list[Path]:
    """Return repo-relative paths of files known to git."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        # Fall back to a plain walk if git is unavailable (shouldn't happen),
        # skipping generated/vendored segments so the scan stays meaningful.
        paths: list[Path] = []
        for p in sorted(REPO_ROOT.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(REPO_ROOT)
            if any(part in _IGNORED_SEGMENTS for part in rel.parts):
                continue
            paths.append(rel)
        return paths
    return [Path(ln) for ln in result.stdout.splitlines() if ln.strip()]


def _content(rel: Path) -> str:
    try:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


class TestHomePathIntegrity:
    """No misspelled home path and no stray hard-coded absolute home path."""

    def test_no_misspelled_home_path_anywhere(self) -> None:
        """The misspelled home token must never appear in any tracked file —
        source, tests, docs, or examples."""
        hits: list[str] = []
        for rel in _tracked_files():
            if _TOKEN in _content(rel):
                hits.append(f"{rel}")
        assert hits == [], (
            "Found misspelled home token (extra letter) in tracked files:\n"
            + "\n".join("  - " + h for h in hits)
        )

    def test_absolute_home_paths_only_in_allowlisted_reference(self) -> None:
        """A hard-coded absolute home path should not leak into source/tests/
        docs outside the single allowlisted reference."""
        hits: list[str] = []
        for rel in _tracked_files():
            key = rel.as_posix()
            if key in _ALLOW_ABS_HOME:
                continue
            if _ABS_HOME_RE.search(_content(rel)):
                hits.append(key)
        assert not hits, (
            "Absolute home paths must use the $HOME convention; found:\n"
            + "\n".join("  - " + h for h in hits)
        )