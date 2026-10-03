"""Closure contract — ``closure.v1`` (SPEC-0023, M2).

A **materialized entailment closure** is a pure, content-addressed document:

    {graph hash, ontology hash, rule-profile id} -> closure hash

It carries the full canonical, deduplicated, sorted set of assertions
(extensional facts + entailed facts). The content address is computed from the
canonical bytes of the closure document, which includes the input hashes and the
sorted assertions, so identical inputs yield a byte-identical closure. No I/O;
no ambient authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .ontology import (
    CLOSURE_SCHEMA,
    MAX_CLOSURE_ASSERTIONS,
    Assertion,
    ErrorKind,
    OntologyError,
    _assertion_key,
    _canonical_bytes,
    object_from_dict,
)

_SHA256_RE_LEN = 64


@dataclass(frozen=True)
class Closure:
    """A pinned, canonical, content-addressed entailment closure."""

    profile: str
    graph_hash: str
    ontology_hash: str
    assertions: tuple[Assertion, ...] = ()
    schema: str = CLOSURE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != CLOSURE_SCHEMA:
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, f"unsupported closure schema {self.schema!r}"
            )
        if not self.profile:
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "closure profile is empty")
        for label, value in (
            ("graph_hash", self.graph_hash),
            ("ontology_hash", self.ontology_hash),
        ):
            if len(value) != _SHA256_RE_LEN or any(
                c not in "0123456789abcdef" for c in value
            ):
                raise OntologyError(
                    ErrorKind.MALFORMED_DOCUMENT,
                    f"closure {label} must be 64-hex: {value!r}",
                )
        unique: dict[bytes, Assertion] = {}
        for assertion in self.assertions:
            unique[_assertion_key(assertion)] = assertion
        ordered = tuple(unique[key] for key in sorted(unique))
        if len(ordered) > MAX_CLOSURE_ASSERTIONS:
            raise OntologyError(
                ErrorKind.CLOSURE_OVERFLOW,
                f"closure has {len(ordered)} assertions, exceeding "
                f"{MAX_CLOSURE_ASSERTIONS} (fail-closed)",
            )
        object.__setattr__(self, "assertions", ordered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "profile": self.profile,
            "graph_hash": self.graph_hash,
            "ontology_hash": self.ontology_hash,
            "assertions": [assertion.to_dict() for assertion in self.assertions],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def closure_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Closure:
        if not isinstance(data, dict):
            raise OntologyError(ErrorKind.MALFORMED_DOCUMENT, "closure must be a mapping")
        raw = data.get("assertions", [])
        if not isinstance(raw, list):
            raise OntologyError(
                ErrorKind.MALFORMED_DOCUMENT, "closure assertions must be a list"
            )
        return cls(
            profile=str(data["profile"]),
            graph_hash=str(data["graph_hash"]),
            ontology_hash=str(data["ontology_hash"]),
            assertions=tuple(
                Assertion(
                    s=str(item["s"]),
                    p=str(item["p"]),
                    o=object_from_dict(item["o"]),
                )
                for item in raw
            ),
            schema=str(data.get("schema", CLOSURE_SCHEMA)),
        )
