"""Semantic-graph runtime (SPEC-0023, M1) — pure and deterministic.

Three pure functions, no I/O and no ambient authority:

- :func:`project_graph` — derive a canonical :class:`SemanticGraph` from existing
  content-addressed contracts (a ``network.v1`` scope document, the referenced
  ``component.v1`` manifests, and resolved ``components.lock`` entries). The graph
  is a **view**, never a second source of truth (SPEC-0023 S10).
- :func:`discover` — answer a pinned :class:`Query` over a graph, matching a class
  (and its declared subclasses), required capabilities, and port-type
  compatibility. Exact matching only (no inference beyond subsumption).
- :func:`validate_shape` — evaluate a pinned :class:`Shape` as a deterministic,
  fail-closed gate and report the specific violations.

Failures are :class:`OntologyError` tagged with an :class:`ErrorKind`. The module
never mutates its inputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..contracts.component import ComponentManifest
from ..contracts.components_lock import LockEntry
from ..contracts.handoff import types_compatible
from ..contracts.ontology import (
    _KIND_CLASS_TERM,
    CLASS_INPUT_PORT,
    CLASS_OUTPUT_PORT,
    DIRECTION,
    FEEDS,
    HAS_CAPABILITY,
    IMPLEMENTS,
    INPUT,
    KIND,
    KIND_CLASS,
    KIND_PROPERTY,
    OUTPUT,
    PORT_TYPE,
    TYPE,
    Assertion,
    DiscoveryResult,
    ErrorKind,
    Literal,
    Obj,
    Ontology,
    OntologyError,
    Query,
    SemanticGraph,
    Shape,
    ShapeReport,
    Term,
)
from .network import NetworkError, network_from_dict


@dataclass(frozen=True)
class ProjectionSource:
    """The validated inputs a projection reads (no hidden state)."""

    scope: str
    network: dict[str, Any]
    manifests: tuple[tuple[str, ComponentManifest], ...] = ()
    lock: tuple[LockEntry, ...] = ()


def _port_term(scope: str, component_id: str, direction: str, port: str) -> str:
    return f"urn:cbp:{scope}/port/{component_id}/{direction}/{port}"


def _lock_hash(lock: tuple[LockEntry, ...]) -> str:
    if not lock:
        return ""
    payload = [entry.to_dict() for entry in sorted(lock, key=lambda e: e.name)]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def project_graph(source: ProjectionSource) -> SemanticGraph:
    """Project existing contracts into a canonical semantic graph (fail-closed)."""
    if not source.scope:
        raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "projection scope is empty")
    try:
        net = network_from_dict(source.network)
        net.validate()
    except NetworkError as exc:
        raise OntologyError(
            ErrorKind.MALFORMED_DOCUMENT, f"invalid scope network: {exc}"
        ) from exc

    manifests = dict(source.manifests)
    lock_by_name = {entry.name: entry for entry in source.lock}
    assertions: list[Assertion] = []

    for comp in sorted(net.components(), key=lambda c: c.id):
        manifest = manifests.get(comp.id)
        if manifest is None:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                f"no component.v1 manifest for {comp.id!r}",
            )
        component_class = _KIND_CLASS_TERM.get(manifest.kind)
        if component_class is None:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                f"component {comp.id!r} has unknown kind {manifest.kind!r}",
            )
        assertions.append(Assertion(comp.id, TYPE, Term(component_class)))
        assertions.append(Assertion(comp.id, KIND, Literal("str", manifest.kind)))
        for capability in manifest.capabilities:
            assertions.append(
                Assertion(
                    comp.id,
                    HAS_CAPABILITY,
                    Literal("str", f"{capability.name}@{capability.version}"),
                )
            )
        interfaces = set(manifest.implements)
        entry = lock_by_name.get(manifest.name)
        if entry is not None:
            interfaces |= set(entry.implements)
        for interface in sorted(interfaces):
            assertions.append(Assertion(comp.id, IMPLEMENTS, Literal("str", interface)))

        for direction, predicate, port_class in (
            ("in", INPUT, CLASS_INPUT_PORT),
            ("out", OUTPUT, CLASS_OUTPUT_PORT),
        ):
            names = comp.ports.get(direction, ())
            types = comp.port_types.get(direction, {})
            for name in names:
                port_term = _port_term(source.scope, comp.id, direction, name)
                assertions.append(Assertion(comp.id, predicate, Term(port_term)))
                assertions.append(Assertion(port_term, TYPE, Term(port_class)))
                assertions.append(
                    Assertion(port_term, DIRECTION, Literal("str", direction))
                )
                assertions.append(
                    Assertion(
                        port_term, PORT_TYPE, Literal("str", types.get(name, "any"))
                    )
                )

    for edge in net.edges():
        out_port = _port_term(source.scope, edge.source, "out", edge.source_field)
        in_port = _port_term(source.scope, edge.target, "in", edge.target_arg)
        assertions.append(Assertion(out_port, FEEDS, Term(in_port)))

    sources = [
        ("network_sha256", net.content_hash()),
        ("lock_hash", _lock_hash(source.lock)),
    ]
    return SemanticGraph(
        scope=source.scope,
        assertions=tuple(assertions),
        sources=tuple((key, value) for key, value in sources if value),
    )


def _literal_values(graph: SemanticGraph, subject: str, predicate: str) -> set[str]:
    return {
        str(obj.value)
        for obj in graph.objects(subject, predicate)
        if isinstance(obj, Literal)
    }


def _port_terms(graph: SemanticGraph, component_id: str, predicate: str) -> tuple[str, ...]:
    return tuple(
        obj.name
        for obj in graph.objects(component_id, predicate)
        if isinstance(obj, Term)
    )


def _port_types(graph: SemanticGraph, port_term: str) -> tuple[str, ...]:
    return tuple(
        str(obj.value)
        for obj in graph.objects(port_term, PORT_TYPE)
        if isinstance(obj, Literal)
    )


def _accepts_types(graph: SemanticGraph, component_id: str, wanted: tuple[str, ...]) -> bool:
    """True iff every wanted *input* type can feed some declared input port."""
    ports = _port_terms(graph, component_id, INPUT)
    for type_name in wanted:
        if not any(
            types_compatible(type_name, declared)
            for port in ports
            for declared in _port_types(graph, port)
        ):
            return False
    return True


def _produces_types(graph: SemanticGraph, component_id: str, wanted: tuple[str, ...]) -> bool:
    """True iff some declared output port can produce each wanted type."""
    ports = _port_terms(graph, component_id, OUTPUT)
    for type_name in wanted:
        if not any(
            types_compatible(declared, type_name)
            for port in ports
            for declared in _port_types(graph, port)
        ):
            return False
    return True


def discover(graph: SemanticGraph, ontology: Ontology, query: Query) -> DiscoveryResult:
    """Answer a discovery query deterministically (fail-closed)."""
    if query.scope != graph.scope:
        raise OntologyError(
            ErrorKind.SCOPE_ESCAPE,
            f"query scope {query.scope!r} does not match graph scope {graph.scope!r}",
        )
    ontology.assert_declared(query.target_class, KIND_CLASS)

    candidates: set[str] = set()
    for class_name in ontology.subclasses_of(query.target_class):
        candidates.update(graph.subjects_of_type(class_name))

    result: list[str] = []
    required = set(query.requires_capabilities)
    for component_id in sorted(candidates):
        if not required <= _literal_values(graph, component_id, HAS_CAPABILITY):
            continue
        if not _accepts_types(graph, component_id, query.input_types):
            continue
        if not _produces_types(graph, component_id, query.output_types):
            continue
        result.append(component_id)

    return DiscoveryResult(
        scope=query.scope,
        target_class=query.target_class,
        query_hash=query.query_hash(),
        components=tuple(result),
    )


def validate_shape(graph: SemanticGraph, ontology: Ontology, shape: Shape) -> ShapeReport:
    """Evaluate a closed shape over a graph; report the specific violations."""
    ontology.assert_declared(shape.target, KIND_CLASS)
    for requirement in shape.requirements:
        ontology.assert_declared(requirement.relation, KIND_PROPERTY)

    subjects: set[str] = set()
    for class_name in ontology.subclasses_of(shape.target):
        subjects.update(graph.subjects_of_type(class_name))

    declared_relations = {requirement.relation for requirement in shape.requirements}
    violations: list[dict[str, Any]] = []
    for subject in sorted(subjects):
        for requirement in shape.requirements:
            objects = graph.objects(subject, requirement.relation)
            if len(objects) < requirement.min_count:
                violations.append(
                    {
                        "component": subject,
                        "relation": requirement.relation,
                        "detail": "too-few",
                        "count": len(objects),
                        "expected": requirement.min_count,
                    }
                )
            if requirement.max_count is not None and len(objects) > requirement.max_count:
                violations.append(
                    {
                        "component": subject,
                        "relation": requirement.relation,
                        "detail": "too-many",
                        "count": len(objects),
                        "expected": requirement.max_count,
                    }
                )
            allowed = set(requirement.allowed)
            for obj in objects:
                if requirement.datatype is not None and (
                    not isinstance(obj, Literal) or obj.type != requirement.datatype
                ):
                    violations.append(
                        {
                            "component": subject,
                            "relation": requirement.relation,
                            "detail": "datatype",
                            "expected": requirement.datatype,
                        }
                    )
                if allowed and _object_value(obj) not in allowed:
                    violations.append(
                        {
                            "component": subject,
                            "relation": requirement.relation,
                            "detail": "allowed",
                            "value": _object_value(obj),
                        }
                    )
        if shape.closed:
            used = {assertion.p for assertion in graph.assertions if assertion.s == subject}
            extra = sorted(used - declared_relations - {TYPE})
            for relation in extra:
                violations.append(
                    {
                        "component": subject,
                        "relation": relation,
                        "detail": "closed",
                    }
                )

    ordered = tuple(
        sorted(
            violations,
            key=lambda v: json.dumps(v, sort_keys=True, separators=(",", ":")),
        )
    )
    return ShapeReport(ok=not ordered, shape_id=shape.id, violations=ordered)


def _object_value(obj: Obj) -> str:
    return str(obj.value) if isinstance(obj, Literal) else obj.name


def assert_shape(graph: SemanticGraph, ontology: Ontology, shape: Shape) -> None:
    """Raise a fail-closed ``shape-violation`` if the graph violates ``shape``."""
    report = validate_shape(graph, ontology, shape)
    if not report.ok:
        raise OntologyError(
            ErrorKind.SHAPE_VIOLATION,
            f"shape {shape.id!r} violated by {len(report.violations)} requirement(s)",
        )
