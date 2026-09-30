"""Convention guard (deterministic, offline).

Asserts that the agent-factory convention layer is present, consistent, and
enforcing. This is the machine half of the convention: prose points, this test
decides. It reads files and TOML/JSON only; it never touches ``$HOME`` and never
runs the platform.

The JSON here is not a config file for this test; it is the repository's
``opencode.json``. ``json`` is imported as ``json`` deliberately (no aliasing).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_VALID_LEVELS = {"L0", "L1", "L2", "L3"}
_REQUIRED_LEVEL_FIELDS = {"name", "autonomy", "gates", "review"}


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


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
    def test_agents_md_points_at_laws_and_convention_layer(self) -> None:
        text = _read("AGENTS.md")
        assert "PRINCIPLES.md" in text
        assert "docs/agent/README.md" in text
        assert ".agentfactory.toml" in text

    def test_vendor_pointer_files_defer_to_agents_md(self) -> None:
        for name in ("CLAUDE.md", "GEMINI.md"):
            assert "AGENTS.md" in _read(name)

    def test_convention_pages_are_present(self) -> None:
        for page in ("README", "laws", "levels", "editing", "testing", "verification"):
            assert (REPO_ROOT / "docs" / "agent" / f"{page}.md").is_file()


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


class TestSpecs:
    def test_spec_layer_exists(self) -> None:
        for rel in ("specs/README.md", "specs/TEMPLATE.md", "specs/holdout/README.md"):
            assert (REPO_ROOT / rel).is_file()

    def test_convention_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "agent-conventions.md").is_file()

    def test_cbp_component_spec_is_recorded(self) -> None:
        assert (REPO_ROOT / "specs" / "SPEC-0002-cbp-component-architecture.md").is_file()
