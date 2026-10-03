"""SPEC-0023 M3 — the one-way RDF export adapter.

Deterministic and offline. The export is canonical-sorted, ASCII-safe, and
fail-closed on a non-exportable literal; the M2 semantic subnet's payload can be
rendered as standard RDF without ever parsing RDF back in.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp.ontology_runtime import compute_closure
from agent_centric.cbp.rdf_export import XSD, to_ntriples, to_turtle
from agent_centric.contracts.ontology import (
    CLASS_ATOMIC,
    HAS_CAPABILITY,
    TYPE,
    Assertion,
    ErrorKind,
    Literal,
    OntologyError,
    SemanticGraph,
    Term,
    base_ontology,
)

CORE = "urn:cbp:core/"


def _graph(*assertions: Assertion) -> SemanticGraph:
    return SemanticGraph(scope="demo", assertions=assertions)


def test_export_is_deterministic_and_order_independent() -> None:
    first = _graph(
        Assertion("a", TYPE, Term(CLASS_ATOMIC)),
        Assertion("a", HAS_CAPABILITY, Literal("str", "x@1")),
    )
    second = _graph(
        Assertion("a", HAS_CAPABILITY, Literal("str", "x@1")),
        Assertion("a", TYPE, Term(CLASS_ATOMIC)),
    )
    assert to_ntriples(first) == to_ntriples(second)


def test_export_is_canonically_sorted() -> None:
    text = to_ntriples(
        _graph(
            Assertion("z", TYPE, Term(CLASS_ATOMIC)),
            Assertion("a", TYPE, Term(CLASS_ATOMIC)),
        )
    )
    assert text.splitlines() == sorted(text.splitlines())


def test_terms_are_iris() -> None:
    text = to_ntriples(_graph(Assertion("a", TYPE, Term(CLASS_ATOMIC))))
    assert f"<a> <{CORE}type> <{CLASS_ATOMIC}> ." in text


def test_scalar_literals_map_to_xsd() -> None:
    text = to_ntriples(
        _graph(
            Assertion("a", CORE + "s", Literal("str", "hi")),
            Assertion("a", CORE + "i", Literal("int", 3)),
            Assertion("a", CORE + "b", Literal("bool", True)),
        )
    )
    assert f'"hi"^^<{XSD}string>' in text
    assert f'"3"^^<{XSD}integer>' in text
    assert f'"true"^^<{XSD}boolean>' in text


def test_structured_literals_map_to_json() -> None:
    text = to_ntriples(
        _graph(
            Assertion("a", CORE + "d", Literal("dict", {"b": 2, "a": 1})),
            Assertion("a", CORE + "n", Literal("null", None)),
        )
    )
    assert '"{\\"a\\":1,\\"b\\":2}"^^<urn:cbp:json>' in text
    assert '"null"^^<urn:cbp:json>' in text


def test_float_is_not_exportable() -> None:
    with pytest.raises(OntologyError) as exc:
        to_ntriples(_graph(Assertion("a", CORE + "f", Literal("float", 1.5))))
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_int_and_bool_value_mismatch_fail_closed() -> None:
    with pytest.raises(OntologyError):
        to_ntriples(_graph(Assertion("a", CORE + "i", Literal("int", "not-an-int"))))
    with pytest.raises(OntologyError):
        to_ntriples(_graph(Assertion("a", CORE + "b", Literal("bool", "yes"))))


def test_non_ascii_is_escaped() -> None:
    text = to_ntriples(_graph(Assertion("a", CORE + "s", Literal("str", "caf\u00e9"))))
    assert "caf\\u00e9" in text
    assert text.isascii()


def test_quotes_and_newlines_are_escaped() -> None:
    text = to_ntriples(_graph(Assertion("a", CORE + "s", Literal("str", 'a"b\nc'))))
    assert '\\"b\\nc' in text


def test_turtle_prefixes_and_agrees_with_ntriples() -> None:
    graph = _graph(
        Assertion("a", TYPE, Term(CLASS_ATOMIC)),
        Assertion("a", HAS_CAPABILITY, Literal("str", "x@1")),
    )
    turtle = to_turtle(graph)
    assert f"@prefix xsd: <{XSD}> ." in turtle
    assert "^^xsd:string" in turtle
    ntriples_lines = set(to_ntriples(graph).splitlines())
    # Turtle prefixed datatypes differ textually from N-Triples, but the subject,
    # predicate, and IRI-object lines are identical.
    for line in ntriples_lines:
        if "<http://www.w3.org/2001/XMLSchema#string>" not in line:
            assert line in turtle


def test_empty_graph() -> None:
    assert to_ntriples(_graph()) == ""
    assert to_turtle(_graph()).startswith("@prefix xsd:")


def test_export_accepts_a_closure() -> None:
    graph = _graph(Assertion("sensor", TYPE, Term(CLASS_ATOMIC)))
    closure = compute_closure(graph, base_ontology())
    text = to_ntriples(closure)
    assert f"<sensor> <{CORE}type> <{CLASS_ATOMIC}> ." in text
    assert to_ntriples(closure) == to_ntriples(_graph(*closure.assertions))
