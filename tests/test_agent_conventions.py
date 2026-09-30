"""Convention guard (deterministic, offline).

Asserts that the agent convention layer is present and consistent: the factory
policy, the canonical Agent Skills / components (`.agents/skills/<name>/SKILL.md`),
the vendor pointers, the enforcement config, the progressive-disclosure pages,
and the specs. Prose points; this test decides. It reads files and TOML/JSON
only; it never touches ``$HOME``.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

_VALID_LEVELS = {"L0", "L1", "L2", "L3"}
_REQUIRED_LEVEL_FIELDS = {"name", "autonomy", "gates", "review"}
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# The law-critical skills that must always exist. Every OTHER skill under
# .agents/skills/ is auto-discovered and must satisfy the same contract, so
# adding components never requires editing this allowlist.
_REQUIRED_SKILLS = {
    "safe-file-editing",
    "test-authority",
    "commit-and-push",
    "operating-levels",
    "verification-gates",
    "home-path-safety",
    "cbp-architecture",
    "skill-authoring",
}
_VALID_DETERMINISM = {"deterministic", "suspect"}
_REQUIRED_AGENT_DOCS = {
    "README.md",
    "levels.md",
    "testing.md",
    "verification.md",
    "committing.md",
    "components.md",
    "architecture.md",
}
_ENTRY_POINTER_DOCS = (
    "HANDOFF.md",
    "STATUS.md",
    "README_CBP.md",
    "KERNEL.md",
    "docs/DIRECTIVE.md",
    "docs/CBP_HANDOFF.md",
)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FENCED_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_SKIP_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _discover_skills() -> set[str]:
    """Every skill directory under .agents/skills/ that contains a SKILL.md."""
    if not SKILLS_DIR.is_dir():
        return set()
    found: set[str] = set()
    for child in SKILLS_DIR.iterdir():
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if (child / "SKILL.md").is_file():
            found.add(child.name)
    return found


def _iter_markdown() -> list[Path]:
    found = []
    for path in REPO_ROOT.rglob("*.md"):
        parts = path.relative_to(REPO_ROOT).parts
        if any(part in _SKIP_DIRS for part in parts):
            continue
        found.append(path)
    return sorted(found)


def _strip_code(text: str) -> str:
    """Remove fenced and inline code so example link syntax is not scanned."""
    return _INLINE_CODE_RE.sub("", _FENCED_RE.sub("", text))


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
    def test_there_is_at_least_one_skill(self) -> None:
        assert _discover_skills(), "no skills discovered under .agents/skills/"

    def test_required_skills_are_present(self) -> None:
        assert _discover_skills() >= _REQUIRED_SKILLS

    def test_every_discovered_skill_is_canonical(self) -> None:
        # Auto-discovery is the hard wiring: ANY skill dir is held to the
        # contract, with no per-skill registration.
        for name in sorted(_discover_skills()):
            skill = SKILLS_DIR / name / "SKILL.md"
            fm, body = _parse_frontmatter(skill.read_text(encoding="utf-8"))
            assert fm.get("name") == name, f"{name}: name must equal directory"
            assert _NAME_RE.match(name) and len(name) <= 64, name
            description = fm.get("description")
            assert isinstance(description, str) and 1 <= len(description) <= 1024, name
            meta = fm.get("metadata")
            assert isinstance(meta, dict), f"{name}: metadata missing"
            assert meta.get("component") == name, (
                f"{name}: must map to a component of the same name"
            )
            assert meta.get("determinism") in _VALID_DETERMINISM, name
            assert "## Deterministic termination" in body, name
            assert "## Non-determinism (suspect)" in body, name

    def test_no_stray_skill_directories(self) -> None:
        for child in SKILLS_DIR.iterdir():
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            assert (child / "SKILL.md").is_file(), f"{child.name} has no SKILL.md"

    def test_claude_skills_symlink_resolves_to_the_canonical_tree(self) -> None:
        link = REPO_ROOT / ".claude" / "skills"
        assert link.is_symlink(), ".claude/skills must be a symlink"
        assert link.resolve() == SKILLS_DIR.resolve()

    def test_claude_exposes_every_discovered_skill(self) -> None:
        link = REPO_ROOT / ".claude" / "skills"
        exposed = {p.name for p in link.iterdir() if (p / "SKILL.md").is_file()}
        assert _discover_skills() <= exposed


class TestProgressiveDisclosure:
    def test_required_agent_docs_are_present(self) -> None:
        for name in sorted(_REQUIRED_AGENT_DOCS):
            assert (REPO_ROOT / "docs" / "agent" / name).is_file(), name

    def test_map_links_every_agent_doc(self) -> None:
        text = _read("docs/agent/README.md")
        for name in sorted(_REQUIRED_AGENT_DOCS - {"README.md"}):
            assert f"({name})" in text, f"map does not link {name}"

    def test_relative_markdown_links_resolve(self) -> None:
        broken: list[str] = []
        for path in _iter_markdown():
            text = _strip_code(path.read_text(encoding="utf-8"))
            for match in _MD_LINK_RE.finditer(text):
                target = match.group(1).strip()
                if target.startswith(("http://", "https://", "#", "mailto:", "~")):
                    continue
                if ' "' in target:
                    target = target.split(' "', 1)[0]
                target = target.split("#", 1)[0].strip()
                if not target:
                    continue
                if not (path.parent / target).resolve().exists():
                    broken.append(f"{path.relative_to(REPO_ROOT)} -> {target}")
        assert not broken, "broken relative links: " + "; ".join(broken)


class TestStragglers:
    def test_in_scope_docs_point_at_the_entry(self) -> None:
        for rel in _ENTRY_POINTER_DOCS:
            head = "\n".join(_read(rel).splitlines()[:12])
            assert "AGENTS.md" in head, f"{rel} lacks an entry pointer to AGENTS.md"


class TestDistributionBoundary:
    """SPEC-0007: run-time stays local-first; acquisition is pinned and verified."""

    def test_law5_distinguishes_acquisition_from_run_time(self) -> None:
        law = _read("PRINCIPLES.md")
        assert "SPEC-0007" in law
        assert "Acquisition is separate and bounded" in law

    def test_kernel_marks_distribution_superseded_for_components(self) -> None:
        assert "SPEC-0007" in _read("KERNEL.md")

    def test_architecture_page_describes_the_two_layers(self) -> None:
        text = _read("docs/agent/architecture.md")
        assert "harness" in text.lower()
        assert "shell component" in text
        assert "components.lock" in text


class TestEnforcement:
    def test_opencode_denies_in_place_edits(self) -> None:
        cfg = json.loads(_read("opencode.json"))
        assert cfg["$schema"] == "https://opencode.ai/config.json"
        assert cfg["permission"]["edit"] == "deny"

    def test_opencode_loads_the_convention_map(self) -> None:
        cfg = json.loads(_read("opencode.json"))
        assert "docs/agent/README.md" in cfg["instructions"]

    def test_sanctioned_mutation_primitive_exists(self) -> None:
        assert (REPO_ROOT / "tools" / "safe-replace.sh").is_file()

    def test_skill_scaffold_is_executable(self) -> None:
        scaffold = REPO_ROOT / "tools" / "new-skill.sh"
        assert scaffold.is_file(), "tools/new-skill.sh missing"
        assert os.access(scaffold, os.X_OK), "tools/new-skill.sh must be executable"

    def test_ci_runs_the_gates(self) -> None:
        ci = _read(".github/workflows/gates.yml")
        for token in ("ruff", "mypy", "cbp-check", "pytest"):
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

    def test_convention_completion_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "SPEC-0003-agent-convention-completion.md").is_file()

    def test_convention_hardwiring_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "SPEC-0004-hardwire-component-convention.md").is_file()

    def test_skill_execution_mapping_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "SPEC-0005-skill-execution-mapping.md").is_file()

    def test_skill_component_same_name_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "SPEC-0006-skill-component-same-name.md").is_file()

    def test_harness_distribution_spec_is_recorded(self) -> None:
        spec = REPO_ROOT / "specs" / "SPEC-0007-harness-shell-component-distribution.md"
        assert spec.is_file()

    def test_shell_design_spec_is_recorded(self) -> None:
        spec = REPO_ROOT / "specs" / "SPEC-0008-shell-design-interface-n8n.md"
        assert spec.is_file()

    def test_fbp_abm_conformance_spec_is_recorded(self) -> None:
        spec = REPO_ROOT / "specs" / "SPEC-0009-fbp-abm-conformance.md"
        assert spec.is_file()


class TestNoStaleLabels:
    """Guard the FBP -> CBP cut-over and the branch retirements (no drift).

    These are deterministic, offline checks: the legacy ``agent_centric.fbp``
    package and the retired ``fbp-*`` gates/branches must not reappear. Heritage
    credit for Flow-Based Programming (the acronym ``FBP`` in prose) is allowed.
    """

    def test_legacy_fbp_package_is_gone(self) -> None:
        agent = REPO_ROOT / "src" / "agent_centric"
        assert (agent / "cbp").is_dir(), "the CBP package must exist"
        assert not (agent / "fbp").exists(), "the legacy fbp package must not return"

    def test_source_has_no_legacy_fbp_paths(self) -> None:
        offenders: list[str] = []
        for source in (REPO_ROOT / "src").rglob("*.py"):
            body = source.read_text(encoding="utf-8")
            if "agent_centric.fbp" in body or "agent_centric/fbp" in body:
                offenders.append(source.relative_to(REPO_ROOT).as_posix())
        assert not offenders, f"legacy fbp references found: {offenders}"

    def test_gates_use_cbp_check(self) -> None:
        data = tomllib.loads(_read(".agentfactory.toml"))
        gates = [gate for level in data["levels"].values() for gate in level["gates"]]
        stale = [gate for gate in gates if gate.startswith("fbp")]
        assert not stale, f"stale gates: {stale}"
        ci = _read(".github/workflows/gates.yml")
        assert "cbp-check" in ci, "CI must run cbp-check"
        assert "fbp-check" not in ci, "CI must not run the retired fbp-check"
