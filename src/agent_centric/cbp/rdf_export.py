"""One-way RDF export of a semantic graph (SPEC-0023 M3).

A **standard export adapter**: a pure, deterministic function that renders the
bespoke canonical assertion model as RDF (N-Triples, and a Turtle form), so the
semantic graph interoperates with the wider RDF ecosystem. It is **one-way** —
there is no parser, so no untrusted RDF can re-enter the deterministic spine.

Guarantees:

- **Deterministic.** Triples are emitted in canonical ``(s, p, o)`` order; the
  same graph always exports to byte-identical text.
- **Fail-closed.** A literal whose datatype is not exportable (``float``, or a
  value that contradicts its declared type) is an explicit error, never a
  silent, lossy rendering.
- **ASCII-safe.** Non-ASCII is ``\\uXXXX``-escaped, so the output is byte-stable
  across locales/platforms.

The object terms are ``urn:cbp:`` IRIs and the literals use the system's value
universe mapped onto XSD (``string``/``integer``/``boolean``) with a
``urn:cbp:json`` datatype for the structured ``dict``/``list``/``null``/``any``
values. No I/O; no ambient authority.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from ..contracts.closure import Closure
from ..contracts.ontology import (
    Assertion,
    ErrorKind,
    Literal,
    OntologyError,
    SemanticGraph,
    Term,
    _assertion_key,
)

XSD = "http://www.w3.org/2001/XMLSchema#"
CBP_JSON = "urn:cbp:json"

# The datatypes that map onto a native XSD literal.
_XSD_TYPES = {"str": XSD + "string", "int": XSD + "integer", "bool": XSD + "boolean"}
# The structured datatypes rendered as a canonical-JSON literal.
_JSON_TYPES = ("dict", "list", "null", "any")


def _string_token(value: str) -> str:
    """A canonical N-Triples/Turtle string literal token (JSON escaping)."""
    return json.dumps(value, ensure_ascii=True)


def _iri(name: str) -> str:
    return "<" + name + ">"


def _literal_token(literal: Literal, *, turtle: bool) -> str:
    if literal.type == "str":
        datatype = _XSD_TYPES["str"]
        value = _string_token(str(literal.value))
    elif literal.type == "int":
        if not isinstance(literal.value, int) or isinstance(literal.value, bool):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                "an 'int' literal must hold an integer (fail-closed)",
            )
        datatype = _XSD_TYPES["int"]
        value = _string_token(str(literal.value))
    elif literal.type == "bool":
        if not isinstance(literal.value, bool):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                "a 'bool' literal must hold a boolean (fail-closed)",
            )
        datatype = _XSD_TYPES["bool"]
        value = _string_token("true" if literal.value else "false")
    elif literal.type == "float":
        raise OntologyError(
            ErrorKind.MALFORMED_DOCUMENT,
            "float literals are not exportable (canonical JSON is integers-only)",
        )
    else:
        canonical = json.dumps(
            literal.value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        datatype = CBP_JSON
        value = _string_token(canonical)
    if turtle:
        if datatype.startswith(XSD):
            return f"{value}^^xsd:{datatype[len(XSD):]}"
        return f"{value}^^cbpj:"
    return f"{value}^^<{datatype}>"


def _object_token(obj: Term | Literal, *, turtle: bool) -> str:
    if isinstance(obj, Term):
        return _iri(obj.name)
    return _literal_token(obj, turtle=turtle)


def _triple(assertion: Assertion, *, turtle: bool) -> str:
    return (
        f"{_iri(assertion.s)} {_iri(assertion.p)} "
        f"{_object_token(assertion.o, turtle=turtle)} ."
    )


def _assertions_of(source: SemanticGraph | Closure | Iterable[Assertion]) -> tuple[Assertion, ...]:
    if isinstance(source, (SemanticGraph, Closure)):
        return tuple(source.assertions)
    return tuple(source)


def _sorted(source: SemanticGraph | Closure | Iterable[Assertion]) -> tuple[Assertion, ...]:
    unique: dict[bytes, Assertion] = {}
    for assertion in _assertions_of(source):
        unique[_assertion_key(assertion)] = assertion
    return tuple(unique[key] for key in sorted(unique))


def to_ntriples(source: SemanticGraph | Closure | Iterable[Assertion]) -> str:
    """Render a semantic graph as canonical, sorted RDF N-Triples."""
    lines = [_triple(assertion, turtle=False) for assertion in _sorted(source)]
    return "".join(line + "\n" for line in lines)


def to_turtle(source: SemanticGraph | Closure | Iterable[Assertion]) -> str:
    """Render a semantic graph as deterministic Turtle (prefixed XSD/JSON)."""
    header = [
        f"@prefix xsd: <{XSD}> .",
        f"@prefix cbpj: <{CBP_JSON}> .",
        "",
    ]
    lines = [_triple(assertion, turtle=True) for assertion in _sorted(source)]
    return "".join(line + "\n" for line in header + lines)
