"""Ontology contract (versioned) — ``ontology.v1`` + ``semantic-graph.v1`` (SPEC-0023, M1).

This freezes the **pure** part of the semantic layer, fail-closed:

- A :class:`SemanticGraph` is a set of ``{s, p, o}`` assertions with a canonical
  byte form and a content address (:meth:`SemanticGraph.graph_hash`). In M1 it is
  a **derived projection** of existing contracts (never a second source of truth).
- An :class:`Ontology` fragment is a minimal TBox: declared classes and
  properties, acyclic ``subClassOf``, and ``domain``/``range`` used as validation.
  It is versioned, additive-only, and content-addressed.
- A :class:`Shape` is a pinned, deterministic, fail-closed gate over a graph.
- A :class:`Query` is a pinned, deterministic capability-discovery request.

There are **no blank nodes** and **no external RDF/OWL dependency**: object terms
are ``urn:cbp:`` names and literals use the existing value-type universe. The
module performs no I/O and holds no ambient authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

ONTOLOGY_SCHEMA = "ontology.v1"
GRAPH_SCHEMA = "semantic-graph.v1"
SHAPES_SCHEMA = "shapes.v1"
QUERY_SCHEMA = "ontology.query.v1"

CORE_NAMESPACE = "urn:cbp:core/"

# Bounds (SPEC-0023 S16): fail-closed before any unbounded materialization.
MAX_FRAGMENT_TERMS = 10_000
MAX_CLOSURE_ASSERTIONS = 100_000

KIND_CLASS = "class"
KIND_PROPERTY = "property"

# The value-type universe, mirrored from ``contracts/handoff.py`` (fail-closed on
# an unknown datatype).
_TYPE_NAMES = ("str", "int", "float", "bool", "dict", "list", "null", "any")

_UINT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ErrorKind:
    """The fixed, fail-closed ontology error taxonomy (SPEC-0023 S18)."""

    UNDECLARED_TERM = "undeclared-term"
    MISSING_IMPORT = "missing-import"
    VERSION_CONFLICT = "version-conflict"
    NON_ADDITIVE_CHANGE = "non-additive-change"
    SHAPE_VIOLATION = "shape-violation"
    CLOSURE_OVERFLOW = "closure-overflow"
    FRAGMENT_OVERFLOW = "fragment-overflow"
    MALFORMED_DOCUMENT = "malformed-document"
    SCOPE_ESCAPE = "scope-escape"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (
            cls.UNDECLARED_TERM,
            cls.MISSING_IMPORT,
            cls.VERSION_CONFLICT,
            cls.NON_ADDITIVE_CHANGE,
            cls.SHAPE_VIOLATION,
            cls.CLOSURE_OVERFLOW,
            cls.FRAGMENT_OVERFLOW,
            cls.MALFORMED_DOCUMENT,
            cls.SCOPE_ESCAPE,
        )


class OntologyError(ValueError):
    """A semantic-layer failure, tagged with a fixed :class:`ErrorKind`."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in ErrorKind.all():
            raise ValueError(f"unknown ontology error kind: {kind!r}")
        self.kind = kind
        super().__init__(f"{kind}: {message}")


def _canonical_bytes(document: Any) -> bytes:
    """The system's one canonicalization (sorted keys, tight separators, UTF-8)."""
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Graph objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    """A named object (a ``urn:cbp:`` term); never a blank node."""

    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "term name is empty")

    def to_dict(self) -> dict[str, Any]:
        return {"k": "t", "n": self.name}


@dataclass(frozen=True)
class Literal:
    """A typed literal from the value-type universe."""

    type: str
    value: Any

    def __post_init__(self) -> None:
        if self.type not in _TYPE_NAMES:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unknown literal datatype {self.type!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"k": "l", "t": self.type, "v": self.value}


Obj = Term | Literal


def object_from_dict(data: Any) -> Obj:
    """Parse an assertion object, fail-closed."""
    if not isinstance(data, dict):
        raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "object must be a mapping")
    kind = data.get("k")
    if kind == "t":
        return Term(name=str(data["n"]))
    if kind == "l":
        return Literal(type=str(data["t"]), value=data.get("v"))
    raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, f"unknown object kind {kind!r}")


@dataclass(frozen=True)
class Assertion:
    """A single ``{s, p, o}`` assertion."""

    s: str
    p: str
    o: Obj

    def __post_init__(self) -> None:
        if not self.s or not self.p:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "assertion subject/predicate is empty"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"s": self.s, "p": self.p, "o": self.o.to_dict()}


def _assertion_key(assertion: Assertion) -> bytes:
    return _canonical_bytes(assertion.to_dict())


@dataclass(frozen=True)
class SemanticGraph:
    """A canonical, content-addressed set of assertions for one scope."""

    scope: str
    assertions: tuple[Assertion, ...] = ()
    sources: tuple[tuple[str, str], ...] = ()
    schema: str = GRAPH_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != GRAPH_SCHEMA:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unsupported graph schema {self.schema!r}"
            )
        if not self.scope:
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "graph scope is empty")
        # Canonical: dedupe and sort by canonical bytes so insertion order/dupes
        # cannot change the content address.
        unique: dict[bytes, Assertion] = {}
        for assertion in self.assertions:
            unique[_assertion_key(assertion)] = assertion
        ordered = tuple(unique[key] for key in sorted(unique))
        if len(ordered) > MAX_CLOSURE_ASSERTIONS:
            raise OntologyError(
                ErrorKind.CLOSURE_OVERFLOW,
                f"graph has {len(ordered)} assertions, exceeding "
                f"{MAX_CLOSURE_ASSERTIONS} (fail-closed)",
            )
        object.__setattr__(self, "assertions", ordered)
        object.__setattr__(self, "sources", tuple(sorted(self.sources)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scope": self.scope,
            "sources": {key: value for key, value in self.sources},
            "assertions": [assertion.to_dict() for assertion in self.assertions],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def graph_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def objects(self, subject: str, predicate: str) -> tuple[Obj, ...]:
        """All objects for ``(subject, predicate)`` in canonical order."""
        return tuple(
            a.o for a in self.assertions if a.s == subject and a.p == predicate
        )

    def subjects_of_type(self, type_name: str) -> tuple[str, ...]:
        """Subjects asserted to be of ``type_name``, sorted."""
        found = {
            a.s
            for a in self.assertions
            if a.p == CORE_NAMESPACE + "type"
            and isinstance(a.o, Term)
            and a.o.name == type_name
        }
        return tuple(sorted(found))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticGraph:
        if not isinstance(data, dict):
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "graph must be a mapping")
        schema = str(data.get("schema", GRAPH_SCHEMA))
        scope = data.get("scope")
        raw = data.get("assertions")
        raw_sources = data.get("sources") or {}
        if not isinstance(scope, str) or not isinstance(raw, list):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "graph requires a scope and assertions list"
            )
        if not isinstance(raw_sources, dict):
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "graph sources must be a map")
        return cls(
            scope=scope,
            assertions=tuple(
                Assertion(s=str(item["s"]), p=str(item["p"]), o=object_from_dict(item["o"]))
                for item in raw
            ),
            sources=tuple((str(k), str(v)) for k, v in raw_sources.items()),
            schema=schema,
        )


# ---------------------------------------------------------------------------
# Ontology (TBox fragment)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportRef:
    """A pinned import of another ontology fragment."""

    id: str
    version: str
    hash: str

    def __post_init__(self) -> None:
        if not self.id or not self.version:
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "import id/version empty")
        if not _SHA256_RE.match(self.hash):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"import hash must be 64-hex: {self.hash!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "hash": self.hash}


@dataclass(frozen=True)
class OntologyTerm:
    """A declared class or property of a fragment."""

    kind: str
    name: str
    sub_class_of: tuple[str, ...] = ()
    domain: tuple[str, ...] = ()
    range: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in (KIND_CLASS, KIND_PROPERTY):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unknown term kind {self.kind!r}"
            )
        if not self.name:
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "term name is empty")
        if self.kind == KIND_PROPERTY and self.sub_class_of:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "a property cannot declare sub_class_of"
            )
        if self.kind == KIND_CLASS and (self.domain or self.range):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "a class cannot declare domain/range"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "sub_class_of": list(self.sub_class_of),
            "domain": list(self.domain),
            "range": list(self.range),
        }


@dataclass(frozen=True)
class Ontology:
    """A composable ``ontology.v1`` fragment (minimal TBox, additive-only)."""

    id: str
    version: str
    imports: tuple[ImportRef, ...] = ()
    terms: tuple[OntologyTerm, ...] = ()
    schema: str = ONTOLOGY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ONTOLOGY_SCHEMA:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unsupported ontology schema {self.schema!r}"
            )
        if not self.id or not self.version:
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "ontology id/version empty")
        if len(self.terms) > MAX_FRAGMENT_TERMS:
            raise OntologyError(
                ErrorKind.FRAGMENT_OVERFLOW,
                f"fragment has {len(self.terms)} terms, exceeding "
                f"{MAX_FRAGMENT_TERMS} (fail-closed)",
            )
        names = [term.name for term in self.terms]
        if len(names) != len(set(names)):
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "duplicate term name")
        imported = [imp.id for imp in self.imports]
        if len(imported) != len(set(imported)):
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "duplicate import id")
        # Acyclic subClassOf over the declared classes (fail-closed on a cycle).
        self._assert_acyclic()
        # Canonical ordering for a stable content address.
        object.__setattr__(
            self,
            "imports",
            tuple(sorted(self.imports, key=lambda imp: (imp.id, imp.version, imp.hash))),
        )
        object.__setattr__(
            self, "terms", tuple(sorted(self.terms, key=lambda term: term.name))
        )

    def _assert_acyclic(self) -> None:
        parents: dict[str, tuple[str, ...]] = {
            term.name: term.sub_class_of
            for term in self.terms
            if term.kind == KIND_CLASS
        }
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            colour = state.get(node, 0)
            if colour == 1:
                raise OntologyError(
                    ErrorKind.MALFORMED_DOCUMENT,
                    f"subClassOf cycle at {node!r} (fail-closed)",
                )
            if colour == 2:
                return
            state[node] = 1
            for parent in parents.get(node, ()):
                if parent in parents:
                    visit(parent)
            state[node] = 2

        for name in parents:
            visit(name)

    # -- lookups -------------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        return tuple(term.name for term in self.terms)

    def term(self, name: str) -> OntologyTerm | None:
        for candidate in self.terms:
            if candidate.name == name:
                return candidate
        return None

    def declared(self, name: str) -> bool:
        return self.term(name) is not None

    def assert_declared(self, name: str, kind: str | None = None) -> OntologyTerm:
        """Fail-closed declaration-before-use (SPEC-0023 S8)."""
        found = self.term(name)
        if found is None:
            raise OntologyError(
                ErrorKind.UNDECLARED_TERM, f"term {name!r} is not declared"
            )
        if kind is not None and found.kind != kind:
            raise OntologyError(
                ErrorKind.UNDECLARED_TERM,
                f"term {name!r} is a {found.kind}, expected a {kind}",
            )
        return found

    def subclasses_of(self, name: str) -> tuple[str, ...]:
        """``name`` and every declared transitive subclass (sorted)."""
        descendants: set[str] = set()
        frontier = [name]
        while frontier:
            current = frontier.pop()
            if current in descendants:
                continue
            descendants.add(current)
            for term in self.terms:
                if term.kind == KIND_CLASS and current in term.sub_class_of:
                    frontier.append(term.name)
        return tuple(sorted(descendants))

    # -- import resolution ---------------------------------------------------

    def assert_imports_resolved(self, available: dict[str, str]) -> None:
        """Every import must be present and hash-exact (fail-closed)."""
        for imp in self.imports:
            if imp.id not in available:
                raise OntologyError(
                    ErrorKind.MISSING_IMPORT, f"import {imp.id!r} is not available"
                )
            if available[imp.id] != imp.hash:
                raise OntologyError(
                    ErrorKind.VERSION_CONFLICT,
                    f"import {imp.id!r} pins {imp.hash}, resolved {available[imp.id]}",
                )

    # -- additive-only -------------------------------------------------------

    def assert_additive(self, previous: Ontology) -> None:
        """Refuse a change that removes or alters any previously declared term."""
        current = {term.name: term for term in self.terms}
        for old in previous.terms:
            new = current.get(old.name)
            if new is None:
                raise OntologyError(
                    ErrorKind.NON_ADDITIVE_CHANGE, f"term {old.name!r} was removed"
                )
            if new.kind != old.kind:
                raise OntologyError(
                    ErrorKind.NON_ADDITIVE_CHANGE,
                    f"term {old.name!r} changed kind {old.kind!r} -> {new.kind!r}",
                )
            if not set(old.sub_class_of) <= set(new.sub_class_of):
                raise OntologyError(
                    ErrorKind.NON_ADDITIVE_CHANGE,
                    f"term {old.name!r} lost a superclass",
                )
            if not set(old.domain) <= set(new.domain) or not set(old.range) <= set(
                new.range
            ):
                raise OntologyError(
                    ErrorKind.NON_ADDITIVE_CHANGE,
                    f"property {old.name!r} lost domain/range",
                )

    # -- canonical form ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "version": self.version,
            "imports": [imp.to_dict() for imp in self.imports],
            "terms": [term.to_dict() for term in self.terms],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def ontology_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ontology:
        if not isinstance(data, dict):
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "ontology must be a mapping")
        raw_imports = data.get("imports", [])
        raw_terms = data.get("terms", [])
        if not isinstance(raw_imports, list) or not isinstance(raw_terms, list):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "imports/terms must be lists"
            )
        return cls(
            id=str(data["id"]),
            version=str(data["version"]),
            imports=tuple(
                ImportRef(
                    id=str(item["id"]),
                    version=str(item["version"]),
                    hash=str(item["hash"]),
                )
                for item in raw_imports
            ),
            terms=tuple(
                OntologyTerm(
                    kind=str(item["kind"]),
                    name=str(item["name"]),
                    sub_class_of=tuple(str(x) for x in item.get("sub_class_of", ())),
                    domain=tuple(str(x) for x in item.get("domain", ())),
                    range=tuple(str(x) for x in item.get("range", ())),
                )
                for item in raw_terms
            ),
            schema=str(data.get("schema", ONTOLOGY_SCHEMA)),
        )


# ---------------------------------------------------------------------------
# Shapes (gates)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShapeRequirement:
    """A single closed-shape requirement over one relation."""

    relation: str
    min_count: int = 1
    max_count: int | None = None
    datatype: str | None = None
    allowed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.relation:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "shape relation is empty"
            )
        if self.min_count < 0:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "shape min_count must be >= 0"
            )
        if self.max_count is not None and self.max_count < self.min_count:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "shape max_count < min_count"
            )
        if self.datatype is not None and self.datatype not in _TYPE_NAMES:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unknown shape datatype {self.datatype!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "min_count": self.min_count,
            "max_count": self.max_count,
            "datatype": self.datatype,
            "allowed": list(self.allowed),
        }


@dataclass(frozen=True)
class Shape:
    """A pinned, deterministic closed shape (SPEC-0023 S12)."""

    id: str
    target: str
    requirements: tuple[ShapeRequirement, ...] = ()
    closed: bool = False
    schema: str = SHAPES_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SHAPES_SCHEMA:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unsupported shape schema {self.schema!r}"
            )
        if not self.id or not self.target:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "shape id/target is empty"
            )
        relations = [req.relation for req in self.requirements]
        if len(relations) != len(set(relations)):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "duplicate shape relation"
            )
        object.__setattr__(
            self,
            "requirements",
            tuple(sorted(self.requirements, key=lambda req: req.relation)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "target": self.target,
            "closed": self.closed,
            "requirements": [req.to_dict() for req in self.requirements],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def shape_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Shape:
        raw = data.get("requirements", [])
        if not isinstance(raw, list):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "shape requirements must be a list"
            )
        return cls(
            id=str(data["id"]),
            target=str(data["target"]),
            requirements=tuple(
                ShapeRequirement(
                    relation=str(item["relation"]),
                    min_count=int(item.get("min_count", 1)),
                    max_count=(
                        int(item["max_count"]) if item.get("max_count") is not None else None
                    ),
                    datatype=(
                        str(item["datatype"]) if item.get("datatype") is not None else None
                    ),
                    allowed=tuple(str(x) for x in item.get("allowed", ())),
                )
                for item in raw
            ),
            closed=bool(data.get("closed", False)),
            schema=str(data.get("schema", SHAPES_SCHEMA)),
        )


@dataclass(frozen=True)
class ShapeReport:
    """The deterministic outcome of validating a shape over a graph."""

    ok: bool
    shape_id: str
    violations: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "shape_id": self.shape_id,
            "violations": [dict(v) for v in self.violations],
        }


# ---------------------------------------------------------------------------
# Queries (capability discovery, M1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Query:
    """A pinned, deterministic capability-discovery request (SPEC-0023 S13)."""

    scope: str
    target_class: str
    requires_capabilities: tuple[str, ...] = ()
    input_types: tuple[str, ...] = ()
    output_types: tuple[str, ...] = ()
    schema: str = QUERY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != QUERY_SCHEMA:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unsupported query schema {self.schema!r}"
            )
        if not self.scope or not self.target_class:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "query scope/target_class is empty"
            )
        for value in (*self.input_types, *self.output_types):
            if value not in _TYPE_NAMES:
                raise OntologyError(
                    ErrorKind.MALFORMED_DOCUMENT, f"unknown query type {value!r}"
                )
        object.__setattr__(
            self, "requires_capabilities", tuple(sorted(set(self.requires_capabilities)))
        )
        object.__setattr__(self, "input_types", tuple(sorted(set(self.input_types))))
        object.__setattr__(self, "output_types", tuple(sorted(set(self.output_types))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scope": self.scope,
            "target_class": self.target_class,
            "requires_capabilities": list(self.requires_capabilities),
            "input_types": list(self.input_types),
            "output_types": list(self.output_types),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def query_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Query:
        return cls(
            scope=str(data["scope"]),
            target_class=str(data["target_class"]),
            requires_capabilities=tuple(
                str(x) for x in data.get("requires_capabilities", ())
            ),
            input_types=tuple(str(x) for x in data.get("input_types", ())),
            output_types=tuple(str(x) for x in data.get("output_types", ())),
            schema=str(data.get("schema", QUERY_SCHEMA)),
        )


@dataclass(frozen=True)
class DiscoveryResult:
    """The canonical, content-addressed result of a discovery query."""

    scope: str
    target_class: str
    query_hash: str
    components: tuple[str, ...] = ()
    schema: str = "ontology.result.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scope": self.scope,
            "target_class": self.target_class,
            "query_hash": self.query_hash,
            "components": list(self.components),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def result_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# The base vocabulary (shipped with M1)
# ---------------------------------------------------------------------------

CORE = CORE_NAMESPACE
TYPE = CORE + "type"
KIND = CORE + "kind"
HAS_CAPABILITY = CORE + "hasCapability"
IMPLEMENTS = CORE + "implements"
INPUT = CORE + "input"
OUTPUT = CORE + "output"
PORT_TYPE = CORE + "portType"
DIRECTION = CORE + "direction"
FEEDS = CORE + "feeds"

CLASS_COMPONENT = CORE + "Component"
CLASS_ATOMIC = CORE + "AtomicComponent"
CLASS_COMPOSITE = CORE + "CompositeComponent"
CLASS_MODEL = CORE + "ModelComponent"
CLASS_CAPABILITY = CORE + "Capability"
CLASS_PORT = CORE + "Port"
CLASS_INPUT_PORT = CORE + "InputPort"
CLASS_OUTPUT_PORT = CORE + "OutputPort"

_KIND_CLASS_TERM = {
    "atomic": CLASS_ATOMIC,
    "composite": CLASS_COMPOSITE,
    "model": CLASS_MODEL,
}


def base_ontology() -> Ontology:
    """The pinned M1 base vocabulary (``urn:cbp:core`` fragment v1).

    Minimal TBox: component/capability/port classes and the compatibility
    relations, with acyclic ``subClassOf`` and property ``domain``/``range``.
    """
    return Ontology(
        id=CORE + "core",
        version="1",
        terms=(
            OntologyTerm(kind=KIND_CLASS, name=CLASS_COMPONENT),
            OntologyTerm(
                kind=KIND_CLASS, name=CLASS_ATOMIC, sub_class_of=(CLASS_COMPONENT,)
            ),
            OntologyTerm(
                kind=KIND_CLASS, name=CLASS_COMPOSITE, sub_class_of=(CLASS_COMPONENT,)
            ),
            OntologyTerm(
                kind=KIND_CLASS, name=CLASS_MODEL, sub_class_of=(CLASS_COMPONENT,)
            ),
            OntologyTerm(kind=KIND_CLASS, name=CLASS_CAPABILITY),
            OntologyTerm(kind=KIND_CLASS, name=CLASS_PORT),
            OntologyTerm(
                kind=KIND_CLASS, name=CLASS_INPUT_PORT, sub_class_of=(CLASS_PORT,)
            ),
            OntologyTerm(
                kind=KIND_CLASS, name=CLASS_OUTPUT_PORT, sub_class_of=(CLASS_PORT,)
            ),
            OntologyTerm(
                kind=KIND_PROPERTY, name=HAS_CAPABILITY, domain=(CLASS_COMPONENT,)
            ),
            OntologyTerm(kind=KIND_PROPERTY, name=IMPLEMENTS, domain=(CLASS_COMPONENT,)),
            OntologyTerm(kind=KIND_PROPERTY, name=INPUT, domain=(CLASS_COMPONENT,)),
            OntologyTerm(kind=KIND_PROPERTY, name=OUTPUT, domain=(CLASS_COMPONENT,)),
            OntologyTerm(kind=KIND_PROPERTY, name=PORT_TYPE, domain=(CLASS_PORT,)),
            OntologyTerm(kind=KIND_PROPERTY, name=DIRECTION, domain=(CLASS_PORT,)),
            OntologyTerm(kind=KIND_PROPERTY, name=FEEDS),
        ),
    )
