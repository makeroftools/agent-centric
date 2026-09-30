"""Convention guard (deterministic, offline).

Asserts that the agent convention layer is present and consistent: the factory
policy, the canonical Agent Skills (`.agents/skills/<name>/SKILL.md`), the
vendor pointers, the enforcement config, and the specs. Prose points; this test
decides. It reads files and TOML/JSON only; it never touches ``$HOME``.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

_VALID_LEVELS = {"L0", "L1", "L2", "L3"}
_REQUIRED_LEVEL_FIELDS = {"name", "autonomy", "gates", "review"}
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_REQUIRED_SKILLS = {
    "safe-file-editing",
    "test-authority",
    "commit-and-push",
    "operating-levels",
    "verification-gates",
    "home-path-safety",
    "cbp-architecture",
}


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Minimal YAML frontmatter parse (scalars + a ``metadata`` string map)."""
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    end = text.find("\n---", 4)
    assert end != -1, "unterminated frontmatter"
    fields: dict[str, object] = {}
    meta: dict[str, str] = {}
    current: str | None = None
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current == "metadata":
            key, _, value = line.strip().partition(":")
            meta[key.strip()] = value.strip()
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            current = key
            fields[key] = meta if key == "metadata" else {}
        else:
            fields[key] = value
            current = None
    if meta:
        fields["metadata"] = meta
    return fields, text[end + 4 :]


class TestFactoryPolicy:
    def test_policy_parses_and_level_is_valid(self) -> None:
        data = tomllib.loads(_read(".agentfactory.toml"))
        assert data["schema"] == "agentfactory/v1"
        factory = data["factory"]
        assert factory["level"] in _VALID_LEVELS
        assert factory["target"] in _VALID_LEVELS
        assert factory["on_missing_gate"] == "fail-closed"

    def test_every_level_declares_the_three_axes(self) -> None:
        data = tomllib.loads(_read(".agentfactory.toml"))
        levels = data["levels"]
        assert set(levels) == _VALID_LEVELS
        for name, level in levels.items():
            missing = _REQUIRED_LEVEL_FIELDS - set(level)
            assert not missing, f"{name} missing {sorted(missing)}"
            assert isinstance(level["gates"], list) and level["gates"]

    def test_dark_factory_is_gated_not_enabled(self) -> None:
        data = tomllib.loads(_read(".agentfactory.toml"))
        assert data["levels"]["L3"]["status"] == "declared-gated"
        assert data["factory"]["level"] != "L3"

    def test_mission_critical_path_has_a_floor(self) -> None:
        data = tomllib.loads(_read(".agentfactory.toml"))
        assert data["paths"]["src/**"]["min_level"] == "L1"


class TestEntryPoints:
    def test_agents_md_points_at_laws_and_skills(self) -> None:
        text = _read("AGENTS.md")
        assert "PRINCIPLES.md" in text
        assert ".agents/skills" in text
        assert ".agentfactory.toml" in text

    def test_vendor_pointer_files_defer_to_agents_md(self) -> None:
        for name in ("CLAUDE.md", "GEMINI.md"):
            assert "AGENTS.md" in _read(name)

    def test_human_convention_map_points_at_skills(self) -> None:
        text = _read("docs/agent/README.md")
        assert ".agents/skills" in text


class TestSkills:
    def test_required_skills_are_present_and_canonical(self) -> None:
        for name in _REQUIRED_SKILLS:
            skill = SKILLS_DIR / name / "SKILL.md"
            assert skill.is_file(), f"missing skill: {name}"
            fm, _ = _parse_frontmatter(skill.read_text(encoding="utf-8"))
            assert fm.get("name") == name
            assert _NAME_RE.match(name)
            description = fm.get("description")
            assert isinstance(description, str) and 1 <= len(description) <= 1024
            meta = fm.get("metadata")
            assert isinstance(meta, dict)
            assert "component" in meta, f"{name} metadata.component missing"
            assert "determinism" in meta, f"{name} metadata.determinism missing"

    def test_skills_declare_their_deterministic_termination(self) -> None:
        for name in _REQUIRED_SKILLS:
            _, body = _parse_frontmatter(
                (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
            )
            assert "## Deterministic termination" in body, name
            assert "## Non-determinism (suspect)" in body, name

    def test_claude_skills_symlink_resolves_to_the_canonical_tree(self) -> None:
        link = REPO_ROOT / ".claude" / "skills"
        assert link.is_symlink(), ".claude/skills must be a symlink"
        assert link.resolve() == SKILLS_DIR.resolve()
        exposed = {p.name for p in link.iterdir() if (p / "SKILL.md").is_file()}
        assert exposed >= _REQUIRED_SKILLS


class TestEnforcement:
    def test_opencode_denies_in_place_edits(self) -> None:
        cfg = json.loads(_read("opencode.json"))
        assert cfg["$schema"] == "https://opencode.ai/config.json"
        assert cfg["permission"]["edit"] == "deny"

    def test_sanctioned_mutation_primitive_exists(self) -> None:
        assert (REPO_ROOT / "tools" / "safe-replace.sh").is_file()

    def test_ci_runs_the_gates(self) -> None:
        ci = _read(".github/workflows/gates.yml")
        for token in ("ruff", "mypy", "fbp-check", "pytest"):
            assert token in ci, f"CI does not run {token}"

    def test_version_control_law_and_hooks_are_enforced(self) -> None:
        assert "COMMIT AND PUSH CONTINUOUSLY" in _read("PRINCIPLES.md")
        hooks = _read(".pre-commit-config.yaml")
        assert "ruff" in hooks and "mypy" in hooks


class TestSpecs:
    def test_spec_layer_exists(self) -> None:
        for rel in ("specs/README.md", "specs/TEMPLATE.md", "specs/holdout/README.md"):
            assert (REPO_ROOT / rel).is_file()

    def test_convention_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "agent-conventions.md").is_file()

    def test_cbp_component_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "SPEC-0002-cbp-component-architecture.md").is_file()
