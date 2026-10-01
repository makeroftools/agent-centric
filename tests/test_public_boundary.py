"""Public/private boundary guard (SPEC-0018 §13; SPEC-0016 §94).

The **public** repos must never carry private inventory: a private sibling-repo
slug, a private host id, or a private (RFC 1918) address. This suite scans the
files git tracks and fails closed if any appears. It is the executable form of
the SPEC-0018 acceptance criterion *"No public repo contains a hostname, IP,
private URL, key, tenant name, or branding."*

The scan is deterministic and offline: it walks the repository exactly as
``git`` would track it, performs reads only, and never touches the network.

**Self-scanning note.** As in :mod:`tests.test_repo_home_paths`, the forbidden
tokens are assembled from string pieces at import time so the exact private
strings never appear literally in this public file.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Private sibling-repo slugs (private origins) — assembled from pieces.
_PRIVATE_SLUGS = ("cbp-" + "infra", "cbp-" + "pro")

# Private host ids (inventory) — assembled from pieces.
_PRIVATE_HOSTS = ("dev" + "-01", "dev" + "-02")

# RFC 1918 addresses are real inventory; the one allowlisted test uses a
# canonical documentation address to exercise the non-loopback bind check.
_PRIVATE_IP_RE = re.compile(
    r"\b(?:10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"
    r"|192\.168\.[0-9]{1,3}\.[0-9]{1,3}"
    r"|172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b"
)
_ALLOW_PRIVATE_IP = {
    "tests/test_cbp_transport.py",
}

_IGNORED_SEGMENTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


def _tracked_files() -> list[Path]:
    """Return repo-relative paths of files known to git.

    Mirrors :mod:`tests.test_repo_home_paths`: prefer ``git ls-files`` (tracked
    plus untracked-not-ignored) and fall back to a filtered walk if git is
    unavailable, so a leaked marker is caught before it is ever committed.
    """
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        paths: list[Path] = []
        for candidate in sorted(REPO_ROOT.rglob("*")):
            if not candidate.is_file():
                continue
            rel = candidate.relative_to(REPO_ROOT)
            if any(part in _IGNORED_SEGMENTS for part in rel.parts):
                continue
            paths.append(rel)
        return paths
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def _content(rel: Path) -> str:
    try:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


class TestPublicBoundary:
    """Private inventory must never appear in a public repo."""

    def test_no_private_origins_or_host_ids(self) -> None:
        tokens = _PRIVATE_SLUGS + _PRIVATE_HOSTS
        hits: list[str] = []
        for rel in _tracked_files():
            body = _content(rel)
            for token in tokens:
                if token in body:
                    hits.append(f"{rel}: {token}")
        assert hits == [], (
            "private inventory leaked into this public repo "
            "(SPEC-0018 §13 / SPEC-0016 §94):\n"
            + "\n".join("  - " + hit for hit in hits)
        )

    def test_no_private_ip_ranges(self) -> None:
        hits: list[str] = []
        for rel in _tracked_files():
            if rel.as_posix() in _ALLOW_PRIVATE_IP:
                continue
            if _PRIVATE_IP_RE.search(_content(rel)):
                hits.append(rel.as_posix())
        assert hits == [], (
            "private (RFC 1918) addresses must not appear in this public repo:\n"
            + "\n".join("  - " + hit for hit in hits)
        )
