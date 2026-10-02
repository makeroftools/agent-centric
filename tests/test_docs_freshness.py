"""Documentation freshness guard (mission-critical handoffs).

Volatile facts — the test count, the conformance suite ratios, and spec
references — drift when one document is updated and another is missed. This
guard makes that drift **fail closed**: it compares the documents to each other
and to the *real* collected test count.

It is deterministic and offline: it reads tracked text and runs
``pytest --collect-only`` (no test bodies execute, no network, no cache).

**Single source of truth.** The ``# -> N passed`` anchor in ``HANDOFF.md``
§"Verify everything" is authoritative. Update it from a real run; this guard keeps
the README badge, the "Verified state" bullet, and ``ROADMAP.md`` honest, and
checks that every referenced ``SPEC-NNNN`` exists. The refresh ritual is in
``HANDOFF.md`` §"Documentation freshness".
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SUITE_RATIO_RE = re.compile(
    r"(shared suite|WASM suite|network suite|appointed suite) \*\*(\d+/\d+)\*\*"
)
_SPEC_RE = re.compile(r"SPEC-\d{4}")
_SPEC_ID_RE = re.compile(r"^id:\s*(SPEC-\d{4})\s*$", re.MULTILINE)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _anchor_count() -> int:
    """The authoritative core-suite count from the HANDOFF verify block.

    The ``$``/MULTILINE guard keeps this from matching the private companion's
    ``# -> 104 passed, 1 skipped`` line in the same code block.
    """
    matches = re.findall(
        r"#\s*->\s*(\d+)\s+passed\s*$", _read("HANDOFF.md"), re.MULTILINE
    )
    assert len(matches) == 1, (
        "HANDOFF.md §'Verify everything' must contain exactly one "
        f"'# -> N passed' anchor; found {len(matches)}"
    )
    return int(matches[0])


def _collected_count() -> int:
    """The count pytest actually collects (no test bodies are run)."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "pytest --collect-only failed while checking documentation freshness:\n"
        f"{result.stderr}"
    )
    # Preferred: pytest's summary line (format is stable across TTY/non-TTY).
    summary = re.search(r"(\d+)\s+tests collected", result.stdout)
    if summary:
        return int(summary.group(1))
    # Fallback: sum the per-file '<path>: <count>' lines.
    per_file = sum(
        int(m.group(1))
        for m in re.finditer(r":\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    )
    if per_file:
        return per_file
    # Last resort: count the collected test ids.
    ids = [line for line in result.stdout.splitlines() if "::" in line]
    assert ids, "could not determine the collected test count"
    return len(ids)


def _known_spec_ids() -> set[str]:
    """Every spec id declared in ``specs/*.md`` front-matter."""
    known: set[str] = set()
    for path in (REPO_ROOT / "specs").glob("*.md"):
        known.update(_SPEC_ID_RE.findall(path.read_text(encoding="utf-8")))
    return known


class TestDocsFreshness:
    """The documents must agree with each other and with the real suite."""

    def test_readme_badge_matches_handoff_anchor(self) -> None:
        anchor = _anchor_count()
        badge = re.search(r"tests-(\d+)%20passing", _read("README.md"))
        assert badge is not None, "README.md test badge not found"
        assert int(badge.group(1)) == anchor, (
            f"README badge says {badge.group(1)} tests; HANDOFF anchor says "
            f"{anchor}. Run the refresh ritual in HANDOFF.md "
            "§'Documentation freshness'."
        )

    def test_verified_state_bullet_matches_anchor(self) -> None:
        anchor = _anchor_count()
        assert f"full suite **{anchor} passed**" in _read("HANDOFF.md"), (
            "HANDOFF.md 'Verified state (tips)' bullet disagrees with the "
            "§'Verify everything' anchor."
        )

    def test_collected_count_matches_anchor(self) -> None:
        anchor = _anchor_count()
        actual = _collected_count()
        assert actual == anchor, (
            f"the suite really collects {actual} tests, but HANDOFF.md anchors "
            f"{anchor}. Run the suite, then update the anchor and the README "
            "badge (refresh ritual in HANDOFF.md)."
        )

    def test_roadmap_suite_ratios_match_handoff(self) -> None:
        handoff = _read("HANDOFF.md")
        roadmap = _read("ROADMAP.md")
        pairs = _SUITE_RATIO_RE.findall(handoff)
        assert pairs, "no labeled suite ratios found in HANDOFF.md"
        for label, ratio in pairs:
            assert f"{label} **{ratio}**" in roadmap, (
                f"ROADMAP.md is missing or stale for '{label} **{ratio}**' "
                "(HANDOFF.md is the source of truth)."
            )

    def test_referenced_specs_exist(self) -> None:
        known = _known_spec_ids()
        missing: list[str] = []
        for rel in ("README.md", "ROADMAP.md", "HANDOFF.md"):
            for spec in sorted(set(_SPEC_RE.findall(_read(rel)))):
                if spec not in known:
                    missing.append(f"{rel}: {spec}")
        assert not missing, (
            "documents reference specs that do not exist: " + ", ".join(missing)
        )
