"""Semantic-graph runtime (SPEC-0023, M1+M2) — pure and deterministic.

Pure functions, no I/O and no ambient authority:

- :func:`project_graph` — derive a canonical :class:`SemanticGraph` from existing
  content-addressed contracts (a ``network.v1`` scope document, the referenced
  ``component.v1`` manifests, and resolved ``components.lock`` entries). The graph
  is a **view**, never a second source of truth (SPEC-0023 S10).
- :func:`discover` — answer a pinned :class:`Query` over a graph, matching a class
  (and its declared subclasses), required capabilities, and port-type
  compatibility. Exact matching only (no inference beyond subsumption).
- :func:`validate_shape` — evaluate a pinned :class:`Shape` as a deterministic,
  fail-closed gate and report the specific violations.
- :func:`compute_closure` (M2) — materialize the pinned entailment closure
  (positive Datalog + stratified negation, bounded, canonical-sorted).
- :class:`SemanticSubnet` (M2) — compose fragment children + a ``closure``
  child as a DAG-only ``network.v1`` exposing ``fragment``/``query`` in and
  ``result``/``closure`` out, with the fixpoint running inside the component.

Failures are :class:`OntologyError` tagged with an :class:`ErrorKind`. The module
never mutates its inputs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from ..contracts.closure import Closure
from ..contracts.component import ComponentManifest
from ..contracts.components_lock import LockEntry
from ..contracts.handoff import types_compatible
from ..contracts.ontology import (
    _KIND_CLASS_TERM,
    CLASS_INPUT_PORT,
    CLASS_OUTPUT_PORT,
    DIRECTION,
    DOMAIN,
    FEEDS,
    HAS_CAPABILITY,
    IMPLEMENTS,
    INPUT,
    KIND,
    KIND_CLASS,
    KIND_PROPERTY,
    MAX_CLOSURE_ASSERTIONS,
    OUTPUT,
    PORT_TYPE,
    RANGE,
    SUB_CLASS_OF,
    TYPE,
    Assertion,
    DiscoveryResult,
    ErrorKind,
    Literal,
    Obj,
    Ontology,
    OntologyError,
    Pattern,
    Query,
    Rule,
    RuleAtom,
    SemanticGraph,
    Shape,
    ShapeReport,
    Term,
    _assertion_key,
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


# ---------------------------------------------------------------------------
# Entailment closure (SPEC-0023 M2)
# ---------------------------------------------------------------------------

Bindings = dict[str, tuple[str, ...]]


def _canonical_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _val_term(name: str) -> tuple[str, ...]:
    return ("term", name)


def _val_obj(obj: Obj) -> tuple[str, ...]:
    if isinstance(obj, Term):
        return ("term", obj.name)
    return ("lit", obj.type, _canonical_value(obj.value))


def _unify(pattern: Pattern, value: tuple[str, ...], bindings: Bindings) -> bool:
    """Unify a rule pattern against a unified value (term or literal)."""
    if pattern.kind == "var":
        if pattern.name == "_":
            return True
        if pattern.name not in bindings:
            bindings[pattern.name] = value
            return True
        return bindings[pattern.name] == value
    if pattern.kind == "term":
        return value[0] == "term" and value[1] == pattern.name
    return (
        value[0] == "lit"
        and value[1] == pattern.datatype
        and value[2] == _canonical_value(pattern.value)
    )


def _atom_matches(atom: RuleAtom, assertion: Assertion, bindings: Bindings) -> bool:
    if atom.p != assertion.p:
        return False
    if not _unify(atom.s, _val_term(assertion.s), bindings):
        return False
    return _unify(atom.o, _val_obj(assertion.o), bindings)


def _negated_holds(
    atom: RuleAtom, facts: tuple[Assertion, ...], bindings: Bindings
) -> bool:
    """True iff some fact satisfies a negated atom (existential over wildcards)."""
    return any(_atom_matches(atom, assertion, dict(bindings)) for assertion in facts)


def _rule_bindings(rule: Rule, facts: tuple[Assertion, ...]) -> list[Bindings]:
    positives = [atom for atom in rule.body if not atom.negated]
    negatives = [atom for atom in rule.body if atom.negated]
    results: list[Bindings] = []

    def recurse(index: int, bindings: Bindings) -> None:
        if index == len(positives):
            for atom in negatives:
                if _negated_holds(atom, facts, bindings):
                    return
            results.append(dict(bindings))
            return
        atom = positives[index]
        for assertion in facts:
            extended = dict(bindings)
            if _atom_matches(atom, assertion, extended):
                recurse(index + 1, extended)

    recurse(0, {})
    return results


def _resolve_subject(pattern: Pattern, bindings: Bindings) -> str | None:
    if pattern.kind == "term":
        return pattern.name
    if pattern.kind != "var" or pattern.name not in bindings:
        return None
    value = bindings[pattern.name]
    if value[0] != "term":
        return None
    return value[1]


def _resolve_object(pattern: Pattern, bindings: Bindings) -> Obj:
    if pattern.kind == "term":
        return Term(pattern.name)
    if pattern.kind == "lit":
        return Literal(pattern.datatype, pattern.value)
    if pattern.name not in bindings:
        raise OntologyError(
            ErrorKind.MALFORMED_DOCUMENT, "rule head has an unbound variable"
        )
    value = bindings[pattern.name]
    if value[0] == "term":
        return Term(value[1])
    return Literal(value[1], json.loads(value[2]))


def _derive(head: RuleAtom, bindings: Bindings) -> Assertion:
    subject = _resolve_subject(head.s, bindings)
    if subject is None:
        raise OntologyError(
            ErrorKind.MALFORMED_DOCUMENT, "rule head subject does not resolve to a term"
        )
    return Assertion(subject, head.p, _resolve_object(head.o, bindings))


def _stratify(rules: tuple[Rule, ...], predicates: set[str]) -> dict[str, int]:
    """The least stratification (a negative edge forces a strictly higher stratum).

    Fail-closed on an unstratifiable program (a cycle through a negation).
    """
    keys = sorted(predicates)
    stratum = {key: 0 for key in keys}
    for _ in range(len(keys) + 1):
        changed = False
        for rule in rules:
            required = 0
            for atom in rule.body:
                required = max(
                    required, stratum.get(atom.p, 0) + (1 if atom.negated else 0)
                )
            if stratum.get(rule.head.p, 0) < required:
                stratum[rule.head.p] = required
                changed = True
        if not changed:
            return stratum
    raise OntologyError(
        ErrorKind.MALFORMED_DOCUMENT, "unstratifiable rule program (negation cycle)"
    )


def ontology_facts(ontology: Ontology) -> tuple[Assertion, ...]:
    """Lift the TBox into facts: kinds, ``subClassOf``, and ``domain``/``range``."""
    facts: list[Assertion] = []
    for term in ontology.terms:
        facts.append(Assertion(term.name, KIND, Literal("str", term.kind)))
        if term.kind == KIND_CLASS:
            for parent in term.sub_class_of:
                facts.append(Assertion(term.name, SUB_CLASS_OF, Term(parent)))
        else:
            for domain in term.domain:
                facts.append(Assertion(term.name, DOMAIN, Term(domain)))
            for range_name in term.range:
                facts.append(Assertion(term.name, RANGE, Term(range_name)))
    return tuple(facts)


def compute_closure(graph: SemanticGraph, ontology: Ontology) -> Closure:
    """Materialize the pinned entailment closure (bounded, canonical-sorted)."""
    collection: dict[bytes, Assertion] = {}
    for assertion in (*graph.assertions, *ontology_facts(ontology)):
        collection.setdefault(_assertion_key(assertion), assertion)
    if len(collection) > MAX_CLOSURE_ASSERTIONS:
        raise OntologyError(
            ErrorKind.CLOSURE_OVERFLOW,
            f"closure base has {len(collection)} assertions, exceeding "
            f"{MAX_CLOSURE_ASSERTIONS} (fail-closed)",
        )
    predicates: set[str] = {assertion.p for assertion in collection.values()}
    for rule in ontology.rules:
        predicates.add(rule.head.p)
        for atom in rule.body:
            predicates.add(atom.p)
    stratum = _stratify(ontology.rules, predicates)
    max_stratum = max(stratum.values(), default=0)
    for level in range(max_stratum + 1):
        level_rules = [
            rule for rule in ontology.rules if stratum.get(rule.head.p, 0) == level
        ]
        while True:
            snapshot = tuple(collection.values())
            added = False
            for rule in level_rules:
                for bindings in _rule_bindings(rule, snapshot):
                    assertion = _derive(rule.head, bindings)
                    key = _assertion_key(assertion)
                    if key not in collection:
                        collection[key] = assertion
                        added = True
            if not added:
                break
            if len(collection) > MAX_CLOSURE_ASSERTIONS:
                raise OntologyError(
                    ErrorKind.CLOSURE_OVERFLOW,
                    f"closure exceeded {MAX_CLOSURE_ASSERTIONS} assertions "
                    "(fail-closed)",
                )
    ordered = tuple(collection[key] for key in sorted(collection))
    return Closure(
        profile=f"{ontology.id}@{ontology.version}",
        graph_hash=graph.graph_hash(),
        ontology_hash=ontology.ontology_hash(),
        assertions=ordered,
    )


def closure_graph(closure: Closure, scope: str) -> SemanticGraph:
    """View a closure as a graph (for the closed-shape gate over the closure)."""
    return SemanticGraph(scope=scope, assertions=closure.assertions)


def assert_closure_shape(closure: Closure, ontology: Ontology, shape: Shape) -> None:
    """A gate passes only on a closed shape over the pinned closure (§7)."""
    assert_shape(closure_graph(closure, closure.profile), ontology, shape)


# ---------------------------------------------------------------------------
# Semantic subnet (SPEC-0023 M2) — a DAG-only composition
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class SemanticFragment:
    """One member fragment of a semantic subnet (a child content address)."""

    id: str
    graph: SemanticGraph


@dataclass(frozen=True)
class SemanticSubnet:
    """A composite whose children are fragment components feeding a closure child.

    The recursive entailment fixpoint runs **inside** the ``closure`` component,
    so the composition itself stays a DAG (SPEC-0023 §14). ``network()`` returns a
    ``network.v1`` document that is validated (DAG, external-port guard, typed
    edges); the parent's held subgraph is the derived :class:`Closure`, keyed by
    each child's graph content address (never a copy of child ABox state).
    """

    scope: str
    ontology: Ontology
    fragments: tuple[SemanticFragment, ...] = ()

    def __post_init__(self) -> None:
        if not _SAFE_ID_RE.match(self.scope):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT,
                f"semantic subnet scope {self.scope!r} is not a safe id",
            )
        ids = [fragment.id for fragment in self.fragments]
        if len(ids) != len(set(ids)):
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "duplicate fragment id")
        for fragment_id in ids:
            if not _SAFE_ID_RE.match(fragment_id):
                raise OntologyError(
                    ErrorKind.MALFORMED_DOCUMENT,
                    f"fragment id {fragment_id!r} is not a safe id",
                )

    def component_id(self) -> str:
        return "semantic-" + self.scope

    def sources(self) -> tuple[tuple[str, str], ...]:
        """Child identity -> child graph content address (the derived key)."""
        return tuple(
            sorted((fragment.id, fragment.graph.graph_hash()) for fragment in self.fragments)
        )

    def network(self) -> dict[str, Any]:
        """The ``network.v1`` composition document (validated by ``validate``)."""
        fragment_ids = [fragment.id for fragment in self.fragments]
        closure_in = ["query"] + [f"frag-{fid}" for fid in fragment_ids]
        components: list[dict[str, Any]] = [
            {
                "id": "intake",
                "task": "semantic-intake",
                "ports": {"in": ["fragment"], "out": ["graph"]},
                "port_types": {"in": {"fragment": "str"}, "out": {"graph": "str"}},
            },
            {
                "id": "closure",
                "task": "semantic-closure",
                "ports": {"in": closure_in, "out": ["closure", "result"]},
                "port_types": {
                    "in": {name: "str" for name in closure_in},
                    "out": {"closure": "str", "result": "str"},
                },
            },
        ]
        for fragment_id in fragment_ids:
            components.append(
                {
                    "id": f"frag-{fragment_id}",
                    "task": "semantic-fragment",
                    "ports": {"in": ["fragment"], "out": ["graph"]},
                    "port_types": {
                        "in": {"fragment": "str"},
                        "out": {"graph": "str"},
                    },
                }
            )
        edges: list[dict[str, Any]] = [
            {
                "source": "intake",
                "source_field": "graph",
                "target": f"frag-{fragment_id}",
                "target_arg": "fragment",
            }
            for fragment_id in fragment_ids
        ]
        edges += [
            {
                "source": f"frag-{fragment_id}",
                "source_field": "graph",
                "target": "closure",
                "target_arg": f"frag-{fragment_id}",
            }
            for fragment_id in fragment_ids
        ]
        return {
            "components": [
                {
                    "id": self.component_id(),
                    "task": "semantic-subnet",
                    "ports": {"in": ["fragment", "query"], "out": ["closure", "result"]},
                    "port_types": {
                        "in": {"fragment": "str", "query": "str"},
                        "out": {"closure": "str", "result": "str"},
                    },
                    "external": {
                        "fragment": "intake.fragment",
                        "query": "closure.query",
                        "closure": "closure.closure",
                        "result": "closure.result",
                    },
                    "subnet": {"components": components, "edges": edges, "iips": []},
                }
            ],
            "edges": [],
            "iips": [],
        }

    def validate(self) -> None:
        """Validate the composition (DAG-only, typed, external-port guarded)."""
        try:
            network_from_dict(self.network()).validate()
        except NetworkError as exc:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"invalid semantic subnet: {exc}"
            ) from exc

    def merge_graph(self) -> SemanticGraph:
        """The union of the member graphs, keyed by child content addresses."""
        assertions: list[Assertion] = []
        for fragment in self.fragments:
            assertions.extend(fragment.graph.assertions)
        return SemanticGraph(
            scope=self.scope, assertions=tuple(assertions), sources=self.sources()
        )

    def closure(self) -> Closure:
        return compute_closure(self.merge_graph(), self.ontology)

    def closure_graph(self) -> SemanticGraph:
        return closure_graph(self.closure(), self.scope)

    def discover(self, query: Query) -> DiscoveryResult:
        return discover(self.closure_graph(), self.ontology, query)
