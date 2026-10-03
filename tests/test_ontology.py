"""SPEC-0023 M1 — the semantic layer: pure projection, discovery, and shapes.

Every test is deterministic and offline. The properties asserted are the
mission-critical ones: canonical content addressing, fail-closed error taxonomy,
bounded materialization, declaration-before-use, additive-only ontologies, and
subtree scope isolation.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.ontology_runtime import (
    ProjectionSource,
    assert_shape,
    discover,
    project_graph,
    validate_shape,
)
from agent_centric.contracts.component import ComponentManifest
from agent_centric.contracts.components_lock import LockEntry
from agent_centric.contracts.ontology import (
    CLASS_ATOMIC,
    CLASS_COMPONENT,
    HAS_CAPABILITY,
    IMPLEMENTS,
    INPUT,
    KIND_CLASS,
    KIND_PROPERTY,
    QUERY_SCHEMA,
    Assertion,
    DiscoveryResult,
    ErrorKind,
    ImportRef,
    Literal,
    Ontology,
    OntologyError,
    OntologyTerm,
    Query,
    SemanticGraph,
    Shape,
    ShapeRequirement,
    Term,
    base_ontology,
)

CORE = "urn:cbp:core/"
COMMIT = "a" * 40
TREE = "b" * 64


def _manifest(
    name: str,
    kind: str,
    capabilities: tuple[tuple[str, str], ...] = (),
    ports: dict[str, tuple[str, ...]] | None = None,
) -> ComponentManifest:
    return ComponentManifest.from_dict(
        {
            "version": "component.v1",
            "name": name,
            "kind": kind,
            "entry": {"runtime": "python", "entrypoint": f"demo:{name}"},
            "implements": ["harness.v1"],
            "capabilities": [
                {"name": cap, "version": ver} for cap, ver in capabilities
            ],
            "ports": {key: list(value) for key, value in (ports or {}).items()},
        }
    )


def _network() -> dict:
    return {
        "components": [
            {
                "id": "alpha",
                "task": "alpha",
                "ports": {"in": ["x"], "out": ["y"]},
                "port_types": {"in": {"x": "int"}, "out": {"y": "int"}},
            },
            {
                "id": "beta",
                "task": "beta",
                "ports": {"in": ["y"], "out": ["z"]},
                "port_types": {"in": {"y": "int"}, "out": {"z": "str"}},
            },
        ],
        "edges": [
            {"source": "alpha", "source_field": "y", "target": "beta", "target_arg": "y"}
        ],
        "iips": [],
    }


def _source(
    manifests: tuple[tuple[str, ComponentManifest], ...] | None = None,
    lock: tuple[LockEntry, ...] = (),
) -> ProjectionSource:
    if manifests is None:
        manifests = (
            (
                "alpha",
                _manifest(
                    "alpha", "atomic", (("compute", "1"),), {"in": ("x",), "out": ("y",)}
                ),
            ),
            (
                "beta",
                _manifest(
                    "beta", "atomic", (("render", "1"),), {"in": ("y",), "out": ("z",)}
                ),
            ),
        )
    return ProjectionSource(scope="demo", network=_network(), manifests=manifests, lock=lock)


# -- projection -------------------------------------------------------------


def test_projection_is_deterministic_and_order_independent() -> None:
    ordered = project_graph(_source())
    reversed_manifests = tuple(reversed(_source().manifests))
    shuffled = project_graph(_source(manifests=reversed_manifests))
    assert ordered.graph_hash() == shuffled.graph_hash()


def test_projection_carries_types_capabilities_and_ports() -> None:
    graph = project_graph(_source())
    assert set(graph.subjects_of_type(CLASS_ATOMIC)) == {"alpha", "beta"}
    assert {obj.value for obj in graph.objects("alpha", HAS_CAPABILITY)} == {"compute@1"}
    assert {obj.value for obj in graph.objects("beta", IMPLEMENTS)} == {"harness.v1"}
    assert any(isinstance(obj, Term) for obj in graph.objects("alpha", INPUT))
    alpha_in = next(
        obj.name for obj in graph.objects("alpha", INPUT) if isinstance(obj, Term)
    )
    assert alpha_in == "urn:cbp:demo/port/alpha/in/x"
    assert {obj.value for obj in graph.objects(alpha_in, CORE + "portType")} == {"int"}


def test_projection_is_independent_of_assertion_order() -> None:
    graph = project_graph(_source())
    shuffled = SemanticGraph(
        scope=graph.scope,
        assertions=tuple(reversed(graph.assertions)),
        sources=graph.sources,
    )
    assert shuffled.graph_hash() == graph.graph_hash()


def test_projection_uses_lock_implements() -> None:
    lock = (
        LockEntry(
            name="alpha",
            repo_url="https://example.invalid/alpha",
            commit_sha=COMMIT,
            tree_sha256=TREE,
            implements=("extra.v1",),
        ),
    )
    graph = project_graph(_source(lock=lock))
    assert {obj.value for obj in graph.objects("alpha", IMPLEMENTS)} == {
        "harness.v1",
        "extra.v1",
    }


def test_projection_missing_manifest_fails_closed() -> None:
    source = ProjectionSource(
        scope="demo", network=_network(), manifests=(("alpha", _manifest("alpha", "atomic")),)
    )
    with pytest.raises(OntologyError) as exc:
        project_graph(source)
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_projection_invalid_network_fails_closed() -> None:
    source = ProjectionSource(
        scope="demo",
        network={"components": "not-a-list"},
        manifests=(("alpha", _manifest("alpha", "atomic")),),
    )
    with pytest.raises(OntologyError) as exc:
        project_graph(source)
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


# -- discovery --------------------------------------------------------------


def test_discovery_matches_class_and_subclasses() -> None:
    graph = project_graph(_source())
    ontology = base_ontology()
    result = discover(graph, ontology, Query(scope="demo", target_class=CLASS_COMPONENT))
    assert isinstance(result, DiscoveryResult)
    assert result.components == ("alpha", "beta")


def test_discovery_filters_by_capability() -> None:
    graph = project_graph(_source())
    ontology = base_ontology()
    result = discover(
        graph,
        ontology,
        Query(scope="demo", target_class=CLASS_COMPONENT, requires_capabilities=("compute@1",)),
    )
    assert result.components == ("alpha",)


def test_discovery_filters_by_port_types() -> None:
    graph = project_graph(_source())
    ontology = base_ontology()
    assert discover(
        graph, ontology, Query(scope="demo", target_class=CLASS_COMPONENT, input_types=("int",))
    ).components == ("alpha", "beta")
    assert discover(
        graph, ontology, Query(scope="demo", target_class=CLASS_COMPONENT, output_types=("str",))
    ).components == ("beta",)
    assert discover(
        graph, ontology, Query(scope="demo", target_class=CLASS_COMPONENT, output_types=("int",))
    ).components == ("alpha",)


def test_discovery_result_hash_is_stable() -> None:
    graph = project_graph(_source())
    ontology = base_ontology()
    query = Query(scope="demo", target_class=CLASS_COMPONENT, requires_capabilities=("compute@1",))
    first = discover(graph, ontology, query)
    second = discover(graph, ontology, query)
    assert first.result_hash() == second.result_hash()


def test_discovery_scope_escape_fails_closed() -> None:
    graph = project_graph(_source())
    with pytest.raises(OntologyError) as exc:
        discover(graph, base_ontology(), Query(scope="other", target_class=CLASS_COMPONENT))
    assert exc.value.kind == ErrorKind.SCOPE_ESCAPE


def test_discovery_undeclared_class_fails_closed() -> None:
    graph = project_graph(_source())
    with pytest.raises(OntologyError) as exc:
        discover(graph, base_ontology(), Query(scope="demo", target_class=CORE + "Nope"))
    assert exc.value.kind == ErrorKind.UNDECLARED_TERM


# -- shapes -----------------------------------------------------------------


def _capability_shape(*, min_count: int = 1, max_count: int | None = 1) -> Shape:
    return Shape(
        id="needs-compute",
        target=CLASS_COMPONENT,
        requirements=(
            ShapeRequirement(
                relation=HAS_CAPABILITY,
                min_count=min_count,
                max_count=max_count,
                datatype="str",
            ),
        ),
    )


def test_shape_passes_and_reports() -> None:
    graph = project_graph(_source())
    report = validate_shape(graph, base_ontology(), _capability_shape())
    assert report.ok is True
    assert report.violations == ()


def test_shape_detects_too_few() -> None:
    graph = project_graph(_source())
    report = validate_shape(graph, base_ontology(), _capability_shape(min_count=2, max_count=2))
    assert report.ok is False
    assert {violation["detail"] for violation in report.violations} == {"too-few"}


def test_shape_closed_detects_extra_relation() -> None:
    graph = project_graph(_source())
    shape = Shape(
        id="only-capability",
        target=CLASS_COMPONENT,
        requirements=(ShapeRequirement(relation=HAS_CAPABILITY, min_count=0),),
        closed=True,
    )
    report = validate_shape(graph, base_ontology(), shape)
    assert report.ok is False
    assert "closed" in {violation["detail"] for violation in report.violations}


def test_shape_undeclared_relation_fails_closed() -> None:
    graph = project_graph(_source())
    shape = Shape(
        id="bad",
        target=CLASS_COMPONENT,
        requirements=(ShapeRequirement(relation=CORE + "nope", min_count=1),),
    )
    with pytest.raises(OntologyError) as exc:
        validate_shape(graph, base_ontology(), shape)
    assert exc.value.kind == ErrorKind.UNDECLARED_TERM


def test_assert_shape_raises_shape_violation() -> None:
    graph = project_graph(_source())
    with pytest.raises(OntologyError) as exc:
        assert_shape(graph, base_ontology(), _capability_shape(min_count=2, max_count=2))
    assert exc.value.kind == ErrorKind.SHAPE_VIOLATION


# -- ontology (TBox) --------------------------------------------------------


def test_ontology_declaration_before_use() -> None:
    ontology = base_ontology()
    assert ontology.declared(CLASS_COMPONENT)
    with pytest.raises(OntologyError) as exc:
        ontology.assert_declared(CORE + "Missing", KIND_CLASS)
    assert exc.value.kind == ErrorKind.UNDECLARED_TERM


def test_ontology_subclass_cycle_fails_closed() -> None:
    with pytest.raises(OntologyError) as exc:
        Ontology(
            id="x",
            version="1",
            terms=(
                OntologyTerm(kind=KIND_CLASS, name="A", sub_class_of=("B",)),
                OntologyTerm(kind=KIND_CLASS, name="B", sub_class_of=("A",)),
            ),
        )
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_ontology_duplicate_term_fails_closed() -> None:
    with pytest.raises(OntologyError) as exc:
        Ontology(
            id="x",
            version="1",
            terms=(
                OntologyTerm(kind=KIND_CLASS, name="A"),
                OntologyTerm(kind=KIND_CLASS, name="A"),
            ),
        )
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_ontology_additive_only() -> None:
    previous = Ontology(
        id="x",
        version="1",
        terms=(OntologyTerm(kind=KIND_PROPERTY, name="p", domain=("A",)),),
    )
    grown = Ontology(
        id="x",
        version="2",
        terms=(
            OntologyTerm(kind=KIND_PROPERTY, name="p", domain=("A",), range=("B",)),
            OntologyTerm(kind=KIND_CLASS, name="B"),
        ),
    )
    grown.assert_additive(previous)  # adding a range is additive
    shrunk = Ontology(id="x", version="3", terms=())
    with pytest.raises(OntologyError) as exc:
        shrunk.assert_additive(previous)
    assert exc.value.kind == ErrorKind.NON_ADDITIVE_CHANGE


def test_ontology_import_resolution() -> None:
    ontology = Ontology(
        id="x", version="1", imports=(ImportRef(id="base", version="1", hash=TREE),)
    )
    ontology.assert_imports_resolved({"base": TREE})
    with pytest.raises(OntologyError) as missing:
        ontology.assert_imports_resolved({})
    assert missing.value.kind == ErrorKind.MISSING_IMPORT
    with pytest.raises(OntologyError) as conflict:
        ontology.assert_imports_resolved({"base": "c" * 64})
    assert conflict.value.kind == ErrorKind.VERSION_CONFLICT


# -- bounds + taxonomy + round-trips ----------------------------------------


def test_graph_overflow_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_centric.contracts.ontology as module

    monkeypatch.setattr(module, "MAX_CLOSURE_ASSERTIONS", 1)
    with pytest.raises(OntologyError) as exc:
        SemanticGraph(
            scope="demo",
            assertions=(
                Assertion("s", "p", Literal("str", "a")),
                Assertion("s", "p", Literal("str", "b")),
            ),
        )
    assert exc.value.kind == ErrorKind.CLOSURE_OVERFLOW


def test_unknown_error_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        OntologyError("not-a-kind", "x")


def test_error_taxonomy_is_fixed_and_unique() -> None:
    kinds = ErrorKind.all()
    assert len(kinds) == 9
    assert len(set(kinds)) == 9


def test_graph_round_trip() -> None:
    graph = project_graph(_source())
    assert SemanticGraph.from_dict(graph.to_dict()).graph_hash() == graph.graph_hash()


def test_ontology_round_trip() -> None:
    ontology = base_ontology()
    assert Ontology.from_dict(ontology.to_dict()).ontology_hash() == ontology.ontology_hash()


def test_shape_and_query_round_trip() -> None:
    shape = _capability_shape()
    assert Shape.from_dict(shape.to_dict()).shape_hash() == shape.shape_hash()
    query = Query(
        scope="demo",
        target_class=CLASS_COMPONENT,
        requires_capabilities=("b", "a"),
        schema=QUERY_SCHEMA,
    )
    restored = Query.from_dict(query.to_dict())
    assert restored.requires_capabilities == ("a", "b")
    assert restored.query_hash() == query.query_hash()
