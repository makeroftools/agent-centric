"""Tests for the target-agnostic UI targets (``cbp/ui_runtime.py``; SPEC-0022).

Deterministic and offline: the same ``ui.v1`` component and the same pinned
projection render to ``web`` and ``cli`` byte-identically, the default-deny
boundaries refuse, the provenance sandbox is enforced, and a composition is
pinned before it renders (an unknown/incompatible UI is skipped, and the surface
degrades to the raw projection).
"""

from __future__ import annotations

import json

import pytest

from agent_centric.cbp.ui_runtime import (
    ASSEMBLY_SCHEMA,
    AVAILABLE_TARGETS,
    DIAGRAM_PROJECTION_SCHEMA,
    LEDGER_PROJECTION_SCHEMA,
    SURFACE_SCHEMA,
    TREE_PROJECTION_SCHEMA,
    CliTarget,
    DiagramView,
    LedgerView,
    Projection,
    Provenance,
    SandboxError,
    TreeView,
    UIAssembly,
    UIError,
    UIRef,
    WebTarget,
    default_ui_composition,
    sandbox_policy,
    submit_directive,
    target_binding,
    tree_field,
    tree_hash,
    tree_table,
    tree_view,
)
from agent_centric.contracts.capability import Capability
from agent_centric.contracts.ui import (
    Assurance,
    DirectiveBinding,
    UIComponent,
    UITarget,
)


def _diagram_doc() -> dict[str, object]:
    return {
        "schema": "cbp.diagram.v1",
        "network_sha256": "a" * 64,
        "content_hash": "b" * 64,
        "nodes": [
            {"id": "alpha", "task": "double", "rank": 0},
            {"id": "beta", "task": "square", "rank": 1},
        ],
        "edges": [
            {
                "from": "alpha",
                "from_port": "out",
                "to": "beta",
                "to_port": "in",
                "capacity": 1,
                "points": [],
            }
        ],
    }


def _tree_projection() -> Projection:
    data = {
        "tree": [
            {
                "identity": "root",
                "kind": "root",
                "capabilities": ["run", "delegate"],
                "verifiers": ["even"],
            },
            {"identity": "child", "kind": "child", "capabilities": ["double"], "verifiers": []},
        ],
        "summary": {"nodes": 2},
    }
    return Projection.of(TREE_PROJECTION_SCHEMA, data)


def _ledger_projection() -> Projection:
    data = {
        "ledger": {
            "c0": {"kind": "run", "payload": {"task": "double", "value": 2}},
            "c1": {"kind": "spawn", "payload": {"child": "child"}},
        },
        "count": 2,
    }
    return Projection.of(LEDGER_PROJECTION_SCHEMA, data)


def _simple_component(**overrides: object) -> UIComponent:
    base: dict[str, object] = {
        "id": "cbp.diagram-view",
        "targets": (UITarget.WEB, UITarget.CLI),
        "projections": (DIAGRAM_PROJECTION_SCHEMA,),
        "directives": (DirectiveBinding("open-review"),),
        "capabilities": (Capability(name="ui.read.diagram"),),
        "slots": ("main",),
        "tokens": {"accent": "#38bdf8"},
    }
    base.update(overrides)
    return UIComponent(**base)  # type: ignore[arg-type]


class TestProjection:
    def test_content_hash_is_deterministic_and_order_independent(self) -> None:
        a = Projection.of("cbp.web.tree.v1", {"b": 2, "a": 1})
        b = Projection.of("cbp.web.tree.v1", {"a": 1, "b": 2})
        assert a.content_hash == b.content_hash
        assert len(a.content_hash) == 64

    def test_explicit_bad_hash_is_refused(self) -> None:
        with pytest.raises(UIError):
            Projection(schema="cbp.web.tree.v1", data={"a": 1}, content_hash="d" * 64)

    def test_unknown_assurance_is_refused(self) -> None:
        with pytest.raises(UIError):
            Projection.of("cbp.web.tree.v1", {"a": 1}, assurance="maybe")

    def test_bad_schema_is_refused(self) -> None:
        with pytest.raises(UIError):
            Projection.of("Not A Schema", {"a": 1})

    def test_round_trips(self) -> None:
        projection = _tree_projection()
        assert Projection.from_dict(projection.to_dict()) == projection

    def test_hash_covers_only_the_document(self) -> None:
        verified = Projection.of("cbp.web.tree.v1", {"a": 1}, assurance=Assurance.VERIFIED)
        model = Projection.of("cbp.web.tree.v1", {"a": 1}, assurance=Assurance.MODEL)
        assert verified.content_hash == model.content_hash


class TestTargets:
    def test_known_targets(self) -> None:
        assert AVAILABLE_TARGETS == ("web", "cli")
        assert target_binding("cli").target == "cli"
        assert target_binding("web").target == "web"

    def test_unbuilt_target_refuses(self) -> None:
        with pytest.raises(UIError):
            target_binding("os")

    def test_cli_renders_deterministically(self) -> None:
        tree = TreeView().render(_tree_projection())
        target = CliTarget()
        assert target.render(tree) == target.render(tree)
        assert "root" in target.render(tree)

    def test_web_renders_deterministically(self) -> None:
        tree = TreeView().render(_tree_projection())
        target = WebTarget()
        first = target.render(tree)
        assert first == target.render(tree)
        assert first.startswith("<!doctype html>")
        assert first.rstrip().endswith("</html>")

    def test_web_escapes_text(self) -> None:
        tree = tree_view(
            "<script>alert(1)</script>",
            schema="cbp.ui.test.v1",
            assurance=Assurance.VERIFIED,
            provenance=Provenance.TRUSTED,
            children=[tree_field("label", "<img onerror=x>")],
        )
        rendered = WebTarget().render(tree)
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered
        assert "<img" not in rendered

    def test_cli_refuses_unknown_node(self) -> None:
        with pytest.raises(UIError):
            CliTarget().render({"node": "hologram"})

    def test_web_refuses_unknown_node(self) -> None:
        with pytest.raises(UIError):
            WebTarget().render({"node": "hologram"})


class TestTargetAgnostic:
    def test_same_component_renders_to_both_media(self) -> None:
        ref = UIRef(DiagramView.manifest, DiagramView(), Provenance.TRUSTED)
        projection = Projection.of(DIAGRAM_PROJECTION_SCHEMA, _diagram_doc())
        web = ref.render_section(projection, target="web")
        cli = ref.render_section(projection, target="cli")
        assert web.tree_hash == cli.tree_hash
        assert web.ui_hash == cli.ui_hash == DiagramView.manifest.ui_hash()
        assert web.assurance == cli.assurance == Assurance.VERIFIED
        assert WebTarget().render(web.tree) != CliTarget().render(cli.tree)

    def test_tree_agnostic_media_share_the_tree(self) -> None:
        assembly = UIAssembly([UIRef(TreeView.manifest, TreeView(), Provenance.TRUSTED)])
        projection = _tree_projection()
        web = assembly.render(target="web", projection=projection)
        cli = assembly.render(target="cli", projection=projection)
        assert web.rendered and cli.rendered
        assert web.sections[0].tree_hash == cli.sections[0].tree_hash
        assert "Agent tree" in web.output
        assert "Agent tree" in cli.output


class TestViews:
    def test_tree_view_declares_its_projection(self) -> None:
        assert TREE_PROJECTION_SCHEMA in TreeView.manifest.projections
        assert set(TreeView.manifest.targets) == {"web", "cli"}

    def test_tree_view_refuses_undeclared_projection(self) -> None:
        with pytest.raises(ValueError):
            TreeView().render(_ledger_projection())

    def test_ledger_view_renders_entries(self) -> None:
        tree = LedgerView().render(_ledger_projection())
        assert tree["node"] == "view"
        table = tree["children"][1]
        assert table["columns"] == ["correlation", "kind", "payload"]
        assert len(table["rows"]) == 2

    def test_diagram_view_refuses_wrong_document(self) -> None:
        with pytest.raises(UIError):
            DiagramView().render(Projection.of(DIAGRAM_PROJECTION_SCHEMA, {"nodes": []}))

    def test_diagram_view_renders_nodes_and_edges(self) -> None:
        tree = DiagramView().render(Projection.of(DIAGRAM_PROJECTION_SCHEMA, _diagram_doc()))
        tables = [child for child in tree["children"] if child["node"] == "table"]
        assert tables[0]["rows"][0][0] == "alpha"
        assert tables[1]["rows"][0][:2] == ["alpha", "beta"]


class TestDirective:
    def test_declared_directive_is_content_addressed(self) -> None:
        directive = submit_directive(DiagramView.manifest, "open-review", {"id": "r1"})
        assert directive.requires_approval is False
        assert directive.content_hash() == directive.content_hash()
        assert len(directive.content_hash()) == 64

    def test_undeclared_directive_is_refused(self) -> None:
        with pytest.raises(ValueError):
            submit_directive(DiagramView.manifest, "delete-everything")

    def test_requires_approval_is_surfaced(self) -> None:
        component = _simple_component(
            directives=(DirectiveBinding("provision", requires_approval=True),)
        )
        directive = submit_directive(component, "provision")
        assert directive.requires_approval is True
        assert directive.to_dict()["requires_approval"] is True

    def test_args_change_the_hash(self) -> None:
        first = submit_directive(DiagramView.manifest, "open-review", {"id": "a"})
        second = submit_directive(DiagramView.manifest, "open-review", {"id": "b"})
        assert first.content_hash() != second.content_hash()

    def test_bad_directive_name_is_refused(self) -> None:
        with pytest.raises(ValueError):
            submit_directive(_simple_component(targets=(UITarget.WEB,)), "")


class TestSandbox:
    def test_trusted_keeps_its_declared_capabilities(self) -> None:
        policy = sandbox_policy(Provenance.TRUSTED, _simple_component())
        assert policy.capabilities == ("ui.read.diagram",)
        assert policy.ambient_authority is False

    def test_appointed_runs_a0(self) -> None:
        policy = sandbox_policy(Provenance.APPOINTED, DiagramView.manifest)
        assert policy.capabilities == ()

    def test_discovered_is_refused_by_default(self) -> None:
        with pytest.raises(SandboxError):
            sandbox_policy(Provenance.DISCOVERED, DiagramView.manifest)

    def test_discovered_only_runs_when_explicitly_allowed(self) -> None:
        policy = sandbox_policy(
            Provenance.DISCOVERED, DiagramView.manifest, allow_discovered=True
        )
        assert policy.capabilities == ()

    def test_unknown_provenance_is_refused(self) -> None:
        with pytest.raises(SandboxError):
            sandbox_policy("bff", DiagramView.manifest)


class TestAssembly:
    def test_composition_hash_is_order_independent(self) -> None:
        refs = [
            UIRef(TreeView.manifest, TreeView(), Provenance.TRUSTED),
            UIRef(LedgerView.manifest, LedgerView(), Provenance.TRUSTED),
        ]
        assert (
            UIAssembly(refs).composition_hash
            == UIAssembly(list(reversed(refs))).composition_hash
        )

    def test_composition_identity_is_recorded(self) -> None:
        assembly = UIAssembly([UIRef(TreeView.manifest, TreeView(), Provenance.TRUSTED)])
        identity = assembly.to_dict()
        assert identity["schema"] == ASSEMBLY_SCHEMA
        assert identity["components"][0]["ui_hash"] == TreeView.manifest.ui_hash()

    def test_renders_matching_sections_and_skips_the_rest(self) -> None:
        assembly = default_ui_composition()
        result = assembly.render_document(
            target="web",
            projections={
                TREE_PROJECTION_SCHEMA: _tree_projection(),
                LEDGER_PROJECTION_SCHEMA: _ledger_projection(),
            },
        )
        assert result.rendered is True
        assert result.degraded is True  # the diagram component was skipped
        assert [section.component_id for section in result.sections] == [
            "cbp.ledger-view",
            "cbp.tree-view",
        ]
        assert any(entry["component"] == "cbp.diagram-view" for entry in result.skipped)
        assert result.composition_hash == assembly.composition_hash

    def test_no_matching_ui_degrades_to_the_raw_projection(self) -> None:
        assembly = UIAssembly([UIRef(DiagramView.manifest, DiagramView())])
        projection = _tree_projection()
        result = assembly.render(target="web", projection=projection)
        assert result.rendered is False
        assert result.degraded is True
        assert json.loads(result.output)["projections"][0]["schema"] == TREE_PROJECTION_SCHEMA

    def test_target_incompatible_component_is_skipped(self) -> None:
        component = _simple_component(id="web-only", targets=(UITarget.WEB,))
        assembly = UIAssembly([UIRef(component, DiagramView())])
        projection = Projection.of(DIAGRAM_PROJECTION_SCHEMA, _diagram_doc())
        result = assembly.render(target="cli", projection=projection)
        assert result.rendered is False
        assert "does not target" in result.skipped[0]["reason"]

    def test_unknown_abi_component_is_skipped(self) -> None:
        component = _simple_component(id="future", abi_version="9.9.9")
        assembly = UIAssembly([UIRef(component, DiagramView())])
        projection = Projection.of(DIAGRAM_PROJECTION_SCHEMA, _diagram_doc())
        result = assembly.render(target="web", projection=projection)
        assert result.rendered is False
        assert "unsupported ui abi version" in result.skipped[0]["reason"]

    def test_discovered_component_is_skipped_by_default(self) -> None:
        assembly = UIAssembly(
            [UIRef(TreeView.manifest, TreeView(), Provenance.DISCOVERED)]
        )
        result = assembly.render(target="web", projection=_tree_projection())
        assert result.rendered is False
        assert result.skipped[0]["reason"].startswith("discovered UI")

    def test_discovered_component_renders_when_allowed(self) -> None:
        assembly = UIAssembly(
            [UIRef(TreeView.manifest, TreeView(), Provenance.DISCOVERED)]
        )
        result = assembly.render(
            target="web", projection=_tree_projection(), allow_discovered=True
        )
        assert result.rendered is True
        assert result.sections[0].capabilities == ()

    def test_weakest_assurance_and_provenance_win(self) -> None:
        assembly = UIAssembly(
            [
                UIRef(TreeView.manifest, TreeView(), Provenance.TRUSTED),
                UIRef(LedgerView.manifest, LedgerView(), Provenance.APPOINTED),
            ]
        )
        model_tree = Projection.of(TREE_PROJECTION_SCHEMA, {"tree": []}, assurance=Assurance.MODEL)
        result = assembly.render_document(
            target="cli",
            projections={
                TREE_PROJECTION_SCHEMA: model_tree,
                LEDGER_PROJECTION_SCHEMA: _ledger_projection(),
            },
        )
        assert result.assurance == Assurance.MODEL
        assert result.sections[0].provenance == Provenance.APPOINTED

    def test_surface_dict_schema(self) -> None:
        result = default_ui_composition().render(
            target="cli", projection=_tree_projection()
        )
        assert result.to_dict()["schema"] == SURFACE_SCHEMA


class TestDefaultComposition:
    def test_names_the_read_routes(self) -> None:
        assembly = default_ui_composition()
        assert [component.id for component in assembly.components()] == [
            "cbp.diagram-view",
            "cbp.ledger-view",
            "cbp.tree-view",
        ]
        assert assembly.name == "cbp.web.read"

    def test_hash_is_stable_across_calls(self) -> None:
        assert (
            default_ui_composition().composition_hash
            == default_ui_composition().composition_hash
        )

    def test_web_document_contains_both_sections(self) -> None:
        result = default_ui_composition().render_document(
            target="web",
            projections={
                TREE_PROJECTION_SCHEMA: _tree_projection(),
                LEDGER_PROJECTION_SCHEMA: _ledger_projection(),
            },
        )
        assert "Agent tree" in result.output
        assert "Directive ledger" in result.output
        assert result.output.startswith("<!doctype html>")

    def test_tree_hash_is_deterministic(self) -> None:
        tree = tree_table(("a", "b"), [[1, 2]])
        assert tree_hash(tree) == tree_hash(tree)
