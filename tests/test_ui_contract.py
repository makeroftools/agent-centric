"""Tests for the target-agnostic UI contract (``contracts/ui.py``; SPEC-0022).

Deterministic and offline: the manifest is canonical and content-addressed, and
the default-deny boundaries refuse anything not declared.
"""

from __future__ import annotations

import pytest

from agent_centric.contracts.capability import Capability
from agent_centric.contracts.service import SecretRef
from agent_centric.contracts.ui import (
    UI_ABI_VERSION,
    UI_SCHEMA,
    Assurance,
    DirectiveBinding,
    UIComponent,
    UIReport,
    UITarget,
    validate_ui,
)
from agent_centric.contracts.ui import (
    assert_directive_declared as _assert_directive,
)
from agent_centric.contracts.ui import (
    assert_projection_granted as _assert_projection,
)


def _component(**overrides: object) -> UIComponent:
    base: dict[str, object] = {
        "id": "diagram-view",
        "targets": (UITarget.WEB, UITarget.CLI),
        "projections": ("cbp.diagram.v1",),
        "directives": (DirectiveBinding("open-review"),),
        "capabilities": (Capability(name="ui.read.diagram"),),
        "slots": ("main",),
        "tokens": {"accent": "#38bdf8"},
        "secrets": (SecretRef(name="api", path="secret/ui/api"),),
    }
    base.update(overrides)
    return UIComponent(**base)  # type: ignore[arg-type]


class TestConstants:
    def test_constants(self) -> None:
        assert UI_SCHEMA == "ui.v1"
        assert UI_ABI_VERSION == "1.0.0"
        assert set(UITarget.all()) == {"web", "cli", "os", "embedded"}
        assert set(Assurance.all()) == {"verified", "model", "unverified", "refused"}


class TestConstruction:
    def test_valid_component(self) -> None:
        component = _component()
        assert component.id == "diagram-view"
        assert component.targets == ("cli", "web")  # canonical order
        assert component.may_read("cbp.diagram.v1")
        assert component.may_emit("open-review")

    def test_hash_is_order_independent_and_deterministic(self) -> None:
        a = _component(targets=("web", "cli"), projections=("b.v1", "a.v1"))
        b = _component(targets=("cli", "web"), projections=("a.v1", "b.v1"))
        assert a.ui_hash() == b.ui_hash()

    def test_round_trips(self) -> None:
        component = _component()
        assert UIComponent.from_dict(component.to_dict()) == component

    def test_unknown_target_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _component(targets=("holodeck",))

    def test_requires_a_target(self) -> None:
        with pytest.raises(ValueError):
            _component(targets=())

    def test_requires_a_projection(self) -> None:
        with pytest.raises(ValueError):
            _component(projections=())

    def test_rejects_duplicate_projections(self) -> None:
        with pytest.raises(ValueError):
            _component(projections=("a.v1", "a.v1"))

    def test_rejects_bad_schema_ids(self) -> None:
        with pytest.raises(ValueError):
            _component(projections=("Not A Schema",))

    def test_rejects_duplicate_directives(self) -> None:
        with pytest.raises(ValueError):
            _component(directives=(DirectiveBinding("x"), DirectiveBinding("x")))

    def test_rejects_secret_material_in_tokens(self) -> None:
        with pytest.raises(ValueError):
            _component(tokens={"value": "hunter2"})

    def test_secret_refs_are_allowed(self) -> None:
        component = _component(secrets=(SecretRef(name="k", path="secret/k"),))
        assert component.secrets[0].path == "secret/k"


class TestDirectiveBinding:
    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError):
            DirectiveBinding("")

    def test_rejects_non_id_name(self) -> None:
        with pytest.raises(ValueError):
            DirectiveBinding("Open Review")


class TestValidate:
    def test_ok_component(self) -> None:
        report = validate_ui(_component())
        assert isinstance(report, UIReport) and report.ok
        assert report.targets == ("cli", "web")

    def test_unknown_abi_version_refuses(self) -> None:
        report = validate_ui(_component(abi_version="2.0.0"))
        assert report.ok is False
        assert any("unsupported ui abi version" in error for error in report.errors)

    def test_required_target_must_be_present(self) -> None:
        report = validate_ui(_component(targets=("web",)), required_targets=("cli",))
        assert report.ok is False
        assert any("required target 'cli'" in error for error in report.errors)


class TestDefaultDeny:
    def test_undeclared_projection_is_refused(self) -> None:
        component = _component()
        with pytest.raises(ValueError):
            _assert_projection(component, "review.v1")
        _assert_projection(component, "cbp.diagram.v1")  # declared: ok

    def test_undeclared_directive_is_refused(self) -> None:
        component = _component()
        with pytest.raises(ValueError):
            _assert_directive(component, "delete-everything")
        binding = _assert_directive(component, "open-review")
        assert binding.requires_approval is False

    def test_requires_approval_is_declared(self) -> None:
        component = _component(
            directives=(DirectiveBinding("provision", requires_approval=True),)
        )
        assert component.directive("provision").requires_approval is True
