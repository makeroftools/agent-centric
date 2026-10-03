"""SPEC-0023 M2 — the pinned entailment closure and the composite semantic subnet.

Every test is deterministic and offline. The mission-critical properties:
content-addressed, bounded, canonical-sorted, stratified (fail-closed on an
unstratifiable program), declaration-before-use for rules, non-conflation of the
semantic and process graphs, and a DAG-only semantic subnet whose fixpoint runs
inside its component.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.network import network_from_dict
from agent_centric.cbp.ontology_runtime import (
    ProjectionSource,
    SemanticFragment,
    SemanticSubnet,
    assert_closure_shape,
    compute_closure,
    discover,
    ontology_facts,
    project_graph,
    validate_shape,
)
from agent_centric.contracts.closure import Closure
from agent_centric.contracts.component import ComponentManifest
from agent_centric.contracts.ontology import (
    CLASS_ATOMIC,
    CLASS_COMPONENT,
    CLASS_SINK,
    CLASS_SOURCE,
    CLOSURE_SCHEMA,
    HAS_CAPABILITY,
    KIND_CLASS,
    SUB_CLASS_OF,
    TYPE,
    Assertion,
    ErrorKind,
    Literal,
    Ontology,
    OntologyError,
    OntologyTerm,
    Pattern,
    Query,
    Rule,
    RuleAtom,
    SemanticGraph,
    Shape,
    ShapeRequirement,
    Term,
    base_ontology,
)

CORE = "urn:cbp:core/"
SPECIAL = CORE + "SpecialAtomic"
PORT = "urn:cbp:demo/port/"


def _graph() -> SemanticGraph:
    """sensor (out only) => Source; sink (in only) => Sink; hub => neither."""
    return SemanticGraph(
        scope="demo",
        assertions=(
            Assertion("sensor", TYPE, Term(SPECIAL)),
            Assertion("sensor", "urn:cbp:core/output", Term(PORT + "sensor/out/r")),
            Assertion("sink", TYPE, Term(CLASS_ATOMIC)),
            Assertion("sink", "urn:cbp:core/input", Term(PORT + "sink/in/r")),
            Assertion("hub", TYPE, Term(CLASS_ATOMIC)),
            Assertion("hub", "urn:cbp:core/input", Term(PORT + "hub/in/r")),
            Assertion("hub", "urn:cbp:core/output", Term(PORT + "hub/out/r")),
            Assertion("sensor", HAS_CAPABILITY, Literal("str", "sense@1")),
        ),
    )


def _ontology() -> Ontology:
    """The base ontology extended with ``SpecialAtomic subClassOf Atomic``."""
    base = base_ontology()
    return Ontology(
        id=base.id,
        version=base.version,
        terms=base.terms
        + (
            OntologyTerm(
                kind=KIND_CLASS, name=SPECIAL, sub_class_of=(CLASS_ATOMIC,)
            ),
        ),
        rules=base.rules,
    )


def _types(closure: Closure, class_name: str) -> list[str]:
    return sorted(
        assertion.s
        for assertion in closure.assertions
        if assertion.p == TYPE
        and isinstance(assertion.o, Term)
        and assertion.o.name == class_name
    )


# -- closure: determinism, content addressing -------------------------------


def test_closure_is_deterministic_and_order_independent() -> None:
    graph = _graph()
    shuffled = SemanticGraph(
        scope=graph.scope,
        assertions=tuple(reversed(graph.assertions)),
        sources=graph.sources,
    )
    assert compute_closure(graph, _ontology()).closure_hash() == compute_closure(
        shuffled, _ontology()
    ).closure_hash()


def test_closure_carries_the_input_content_addresses() -> None:
    graph = _graph()
    ontology = _ontology()
    closure = compute_closure(graph, ontology)
    assert closure.profile == f"{ontology.id}@{ontology.version}"
    assert closure.graph_hash == graph.graph_hash()
    assert closure.ontology_hash == ontology.ontology_hash()
    assert closure.schema == CLOSURE_SCHEMA


def test_closure_round_trip() -> None:
    closure = compute_closure(_graph(), _ontology())
    assert Closure.from_dict(closure.to_dict()).closure_hash() == closure.closure_hash()


def test_closure_includes_extensional_and_tbox_facts() -> None:
    closure = compute_closure(_graph(), _ontology())
    triples = {(a.s, a.p, a.o.to_dict().get("n")) for a in closure.assertions}
    assert ("sensor", TYPE, SPECIAL) in triples
    assert (CLASS_ATOMIC, SUB_CLASS_OF, CLASS_COMPONENT) in triples


# -- entailment -------------------------------------------------------------


def test_subclass_transitivity() -> None:
    ontology = _ontology()
    graph = SemanticGraph(
        scope="demo",
        assertions=(
            Assertion(SPECIAL, SUB_CLASS_OF, Term(CLASS_ATOMIC)),
            Assertion("x", TYPE, Term(SPECIAL)),
        ),
    )
    closure = compute_closure(graph, ontology)
    assert "x" in _types(closure, CLASS_ATOMIC)
    assert "x" in _types(closure, CLASS_COMPONENT)


def test_type_subsumption_over_the_hierarchy() -> None:
    closure = compute_closure(_graph(), _ontology())
    assert "sensor" in _types(closure, CLASS_ATOMIC)
    assert "sensor" in _types(closure, CLASS_COMPONENT)


def test_stratified_negation_classifies_sources_and_sinks() -> None:
    closure = compute_closure(_graph(), _ontology())
    assert _types(closure, CLASS_SOURCE) == ["sensor"]
    assert _types(closure, CLASS_SINK) == ["sink"]


def test_closed_world_is_monotone() -> None:
    base = compute_closure(_graph(), _ontology())
    assert "hub" not in _types(base, CLASS_SOURCE)


def test_adding_an_input_removes_the_source_classification() -> None:
    ontology = _ontology()
    graph = SemanticGraph(
        scope="demo",
        assertions=(
            Assertion("sensor", TYPE, Term(CLASS_ATOMIC)),
            Assertion("sensor", "urn:cbp:core/input", Term(PORT + "sensor/in/r")),
        ),
    )
    assert _types(compute_closure(graph, ontology), CLASS_SOURCE) == []


def test_different_graphs_have_different_closures() -> None:
    ontology = _ontology()
    a = compute_closure(_graph(), ontology)
    b = compute_closure(
        SemanticGraph(scope="demo", assertions=(Assertion("z", TYPE, Term(CLASS_ATOMIC)),)),
        ontology,
    )
    assert a.closure_hash() != b.closure_hash()


# -- fail-closed errors -----------------------------------------------------


def test_unstratifiable_program_fails_closed() -> None:
    x, y = Pattern.var("x"), Pattern.var("y")
    bad = Ontology(
        id=CORE + "bad",
        version="1",
        terms=base_ontology().terms,
        rules=(
            Rule(
                id=CORE + "rule/a",
                head=RuleAtom(x, "p", y),
                body=(RuleAtom(x, "q", y), RuleAtom(x, "r", y, negated=True)),
            ),
            Rule(id=CORE + "rule/b", head=RuleAtom(x, "r", y), body=(RuleAtom(x, "p", y),)),
        ),
    )
    with pytest.raises(OntologyError) as exc:
        compute_closure(SemanticGraph(scope="demo", assertions=()), bad)
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_unbound_head_variable_fails_closed() -> None:
    x, y = Pattern.var("x"), Pattern.var("y")
    with pytest.raises(OntologyError) as exc:
        Rule(
            id=CORE + "rule/bad",
            head=RuleAtom(x, "p", y),
            body=(RuleAtom(x, "q", Pattern.term("k")),),
        )
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_negated_head_fails_closed() -> None:
    x = Pattern.var("x")
    with pytest.raises(OntologyError) as exc:
        Rule(
            id=CORE + "rule/bad",
            head=RuleAtom(x, "p", Pattern.any(), negated=True),
            body=(RuleAtom(x, "q", Pattern.any()),),
        )
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_closure_overflow_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_centric.cbp.ontology_runtime as runtime

    monkeypatch.setattr(runtime, "MAX_CLOSURE_ASSERTIONS", 4)
    with pytest.raises(OntologyError) as exc:
        compute_closure(_graph(), _ontology())
    assert exc.value.kind == ErrorKind.CLOSURE_OVERFLOW


def test_rule_count_overflow_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_centric.contracts.ontology as module

    monkeypatch.setattr(module, "MAX_RULE_COUNT", 1)
    x = Pattern.var("x")
    with pytest.raises(OntologyError) as exc:
        Ontology(
            id=CORE + "x",
            version="1",
            rules=(
                Rule(
                    id=CORE + "rule/a",
                    head=RuleAtom(x, "p", Pattern.any()),
                    body=(RuleAtom(x, "q", Pattern.any()),),
                ),
                Rule(
                    id=CORE + "rule/b",
                    head=RuleAtom(x, "r", Pattern.any()),
                    body=(RuleAtom(x, "s", Pattern.any()),),
                ),
            ),
        )
    assert exc.value.kind == ErrorKind.FRAGMENT_OVERFLOW


def test_duplicate_rule_id_fails_closed() -> None:
    x = Pattern.var("x")
    rule = Rule(
        id=CORE + "rule/a",
        head=RuleAtom(x, "p", Pattern.any()),
        body=(RuleAtom(x, "q", Pattern.any()),),
    )
    with pytest.raises(OntologyError) as exc:
        Ontology(id=CORE + "x", version="1", rules=(rule, rule))
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


# -- rules in the ontology contract -----------------------------------------


def test_rule_and_pattern_round_trip() -> None:
    rule = base_ontology().rules[0]
    assert Rule.from_dict(rule.to_dict()).to_dict() == rule.to_dict()


def test_ontology_with_rules_round_trips() -> None:
    ontology = _ontology()
    assert Ontology.from_dict(ontology.to_dict()).ontology_hash() == ontology.ontology_hash()


def test_ontology_rules_are_additive_only() -> None:
    previous = _ontology()
    grown = Ontology(
        id=previous.id,
        version="2",
        terms=previous.terms,
        rules=previous.rules,
    )
    grown.assert_additive(previous)  # same rules + more terms is additive
    x = Pattern.var("x")
    changed_rules = tuple(
        (
            Rule(
                id=CORE + "rule/type-subsumption",
                head=RuleAtom(x, "p", Pattern.any()),
                body=(RuleAtom(x, "q", Pattern.any()),),
            )
            if rule.id == CORE + "rule/type-subsumption"
            else rule
        )
        for rule in previous.rules
    )
    changed = Ontology(
        id=previous.id,
        version="3",
        terms=previous.terms,
        rules=changed_rules,
    )
    with pytest.raises(OntologyError) as exc:
        changed.assert_additive(previous)
    assert exc.value.kind == ErrorKind.NON_ADDITIVE_CHANGE


def test_ontology_facts_lift_tbox_relations() -> None:
    ontology = _ontology()
    facts = ontology_facts(ontology)
    triples = {(a.s, a.p, a.o.to_dict().get("n")) for a in facts}
    assert (CLASS_ATOMIC, SUB_CLASS_OF, CLASS_COMPONENT) in triples


# -- gate over the pinned closure -------------------------------------------


def test_gate_over_the_closure() -> None:
    closure = compute_closure(_graph(), _ontology())
    shape = Shape(
        id="needs-capability",
        target=CLASS_COMPONENT,
        requirements=(ShapeRequirement(relation=HAS_CAPABILITY, min_count=1),),
    )
    report = validate_shape(
        SemanticGraph(scope=closure.profile, assertions=closure.assertions),
        _ontology(),
        shape,
    )
    assert report.ok is False


def test_gate_refuses_a_violated_shape() -> None:
    closure = compute_closure(_graph(), _ontology())
    shape = Shape(
        id="needs-two",
        target=CLASS_COMPONENT,
        requirements=(ShapeRequirement(relation=HAS_CAPABILITY, min_count=2),),
    )
    with pytest.raises(OntologyError) as exc:
        assert_closure_shape(closure, _ontology(), shape)
    assert exc.value.kind == ErrorKind.SHAPE_VIOLATION


# -- semantic subnet --------------------------------------------------------


def _subnet(*fragments: SemanticFragment) -> SemanticSubnet:
    if not fragments:
        fragments = (SemanticFragment(id="f1", graph=_graph()),)
    return SemanticSubnet(scope="demo", ontology=_ontology(), fragments=fragments)


def test_semantic_subnet_network_is_a_valid_dag() -> None:
    subnet = _subnet()
    subnet.validate()
    body = network_from_dict(subnet.network()["components"][0]["subnet"])
    order = body.topological_order()
    assert "closure" in order
    assert order.index("closure") == len(order) - 1


def test_semantic_subnet_exposes_exactly_the_declared_ports() -> None:
    composite = _subnet().network()["components"][0]
    assert set(composite["ports"]["in"]) == {"fragment", "query"}
    assert set(composite["ports"]["out"]) == {"closure", "result"}
    assert set(composite["external"]) == {
        "fragment",
        "query",
        "closure",
        "result",
    }


def test_semantic_subnet_edges_are_not_semantic_relations() -> None:
    network = _subnet().network()["components"][0]["subnet"]
    for edge in network["edges"]:
        assert set(edge) == {"source", "source_field", "target", "target_arg"}


def test_semantic_subnet_sources_are_child_content_addresses() -> None:
    one = _subnet(SemanticFragment(id="f1", graph=_graph()))
    expected = (("f1", _graph().graph_hash()),)
    assert one.sources() == expected


def test_semantic_subnet_closure_recomputes_after_a_child_changes() -> None:
    first = _subnet(SemanticFragment(id="f1", graph=_graph()))
    second_graph = SemanticGraph(
        scope="demo",
        assertions=_graph().assertions
        + (Assertion("extra", TYPE, Term(CLASS_ATOMIC)),),
    )
    second = _subnet(SemanticFragment(id="f1", graph=second_graph))
    assert first.sources() != second.sources()
    assert first.closure().closure_hash() != second.closure().closure_hash()


def test_semantic_subnet_discovery_over_the_closure() -> None:
    subnet = _subnet()
    result = discover(
        subnet.closure_graph(),
        subnet.ontology,
        Query(scope="demo", target_class=CLASS_SOURCE),
    )
    assert result.components == ("sensor",)


def test_semantic_subnet_rejects_an_unsafe_scope() -> None:
    with pytest.raises(OntologyError) as exc:
        SemanticSubnet(scope="bad scope", ontology=_ontology())
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_semantic_subnet_rejects_duplicate_fragment_ids() -> None:
    with pytest.raises(OntologyError) as exc:
        _subnet(
            SemanticFragment(id="f1", graph=_graph()),
            SemanticFragment(id="f1", graph=_graph()),
        )
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_semantic_subnet_scope_escape_fails_closed() -> None:
    subnet = _subnet()
    with pytest.raises(OntologyError) as exc:
        discover(
            subnet.closure_graph(),
            subnet.ontology,
            Query(scope="other", target_class=CLASS_COMPONENT),
        )
    assert exc.value.kind == ErrorKind.SCOPE_ESCAPE


# -- non-conflation with the process graph ----------------------------------


def test_projection_never_conflates_a_semantic_relation_with_an_edge() -> None:
    manifest = ComponentManifest.from_dict(
        {
            "version": "component.v1",
            "name": "alpha",
            "kind": "atomic",
            "entry": {"runtime": "python", "entrypoint": "demo:alpha"},
            "implements": ["harness.v1"],
            "capabilities": [{"name": "compute", "version": "1"}],
            "ports": {"in": ["x"], "out": ["y"]},
        }
    )
    source = ProjectionSource(
        scope="demo",
        network={
            "components": [
                {
                    "id": "alpha",
                    "task": "alpha",
                    "ports": {"in": ["x"], "out": ["y"]},
                    "port_types": {"in": {"x": "int"}, "out": {"y": "int"}},
                }
            ],
            "edges": [],
            "iips": [],
        },
        manifests=(("alpha", manifest),),
    )
    graph = project_graph(source)
    closure = compute_closure(graph, _ontology())
    # The declared data-flow relation ``feeds`` appears only when there is an
    # actual edge; semantic relations are assertions, never edges.
    feeds_assertions = [a for a in closure.assertions if a.p == CORE + "feeds"]
    assert feeds_assertions == []
