"""SPEC-0023 M3 — the authored-fact store: canonical log + derived index/cache.

Deterministic and offline. The mission-critical properties: an append-only,
content-addressed source of truth; idempotent appends; component namespace
sovereignty; a derived, rebuildable SQLite index that is never trusted over the
log; and fail-closed handling of malformed or non-canonical records.
"""

from __future__ import annotations

import os

import pytest

from agent_centric.cbp.component_state import StateError, StateScope
from agent_centric.cbp.ontology_store import OntologyStore
from agent_centric.contracts.component import StateDescriptor
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

PORT = "urn:cbp:demo/port/"


def _store(
    tmp_path, owner: str = "component-a", log_name: str = "assertions.jsonl"
) -> OntologyStore:
    scope = StateScope(root=tmp_path, owner=owner)
    descriptor = StateDescriptor(kind="sqlite", path="facts.sqlite")
    return OntologyStore(scope, descriptor, log_name=log_name)


def _sensor() -> Assertion:
    return Assertion("sensor", TYPE, Term(CLASS_ATOMIC))


def _capability() -> Assertion:
    return Assertion("sensor", HAS_CAPABILITY, Literal("str", "sense@1"))


# -- the canonical log ------------------------------------------------------


def test_append_is_idempotent(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.append(_sensor()) is True
    assert store.append(_sensor()) is False
    assert store.count() == 1


def test_append_all_counts_only_new(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.append_all([_sensor(), _capability(), _sensor()]) == 2
    assert store.count() == 2


def test_assertions_are_canonical_sorted_and_deduped(tmp_path) -> None:
    store = _store(tmp_path)
    store.append_all([_sensor(), _sensor(), _capability()])
    assertions = store.assertions()
    assert len(assertions) == 2
    import json

    keys = [a.to_dict() for a in assertions]
    canonical = lambda d: json.dumps(d, sort_keys=True, separators=(",", ":"))  # noqa: E731
    assert keys == sorted(keys, key=canonical)


def test_content_hash_is_insertion_order_independent(tmp_path) -> None:
    first = _store(tmp_path / "a")
    second = _store(tmp_path / "b")
    first.append_all([_sensor(), _capability()])
    second.append_all([_capability(), _sensor()])
    assert first.content_hash() == second.content_hash()


def test_content_hash_comes_from_the_log_not_the_database(tmp_path) -> None:
    store = _store(tmp_path)
    store.append(_sensor())
    before = store.content_hash()
    store.index()
    assert store.content_hash() == before


def test_graph_carries_the_log_address(tmp_path) -> None:
    store = _store(tmp_path)
    store.append(_sensor())
    graph = store.graph()
    assert graph.scope == "component-a"
    assert graph.sources == (("assertion_log_sha256", store.content_hash()),)


def test_reopening_the_store_reads_the_same_facts(tmp_path) -> None:
    store = _store(tmp_path)
    store.append_all([_sensor(), _capability()])
    reopened = _store(tmp_path)
    assert reopened.assertions() == store.assertions()
    assert reopened.content_hash() == store.content_hash()


def test_log_is_materialized_owner_only(tmp_path) -> None:
    store = _store(tmp_path)
    store.append(_sensor())
    assert os.stat(store.path).st_mode & 0o777 == 0o600


# -- sovereignty ------------------------------------------------------------


def test_two_owners_never_share_a_namespace(tmp_path) -> None:
    first = _store(tmp_path, owner="component-a")
    second = _store(tmp_path, owner="component-b")
    first.append(_sensor())
    assert first.path != second.path
    assert second.count() == 0


def test_unsafe_log_name_fails_closed(tmp_path) -> None:
    with pytest.raises(OntologyError) as exc:
        _store(tmp_path, log_name="../escape.jsonl")
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


# -- fail-closed on a bad log ----------------------------------------------


def test_malformed_log_line_fails_closed(tmp_path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(OntologyError) as exc:
        store.assertions()
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_non_canonical_log_line_fails_closed(tmp_path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"s": "x", "p": "y", "o": {"k": "t", "n": "z"}}\n', encoding="utf-8")
    with pytest.raises(OntologyError) as exc:
        store.assertions()
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_log_overflow_fails_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_centric.cbp.ontology_store as module

    monkeypatch.setattr(module, "MAX_CLOSURE_ASSERTIONS", 1)
    store = _store(tmp_path)
    store.append_all([_sensor(), _capability()])
    with pytest.raises(OntologyError) as exc:
        store.assertions()
    assert exc.value.kind == ErrorKind.CLOSURE_OVERFLOW


# -- the derived index ------------------------------------------------------


def test_index_is_idempotent_and_verifies(tmp_path) -> None:
    store = _store(tmp_path)
    store.append_all([_sensor(), _capability()])
    first = store.index()
    store.verify_index()
    second = store.index()
    assert first == second
    store.verify_index()


def test_verify_index_fails_after_the_log_changes(tmp_path) -> None:
    store = _store(tmp_path)
    store.append(_sensor())
    store.index()
    store.append(_capability())
    with pytest.raises(OntologyError) as exc:
        store.verify_index()
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT


def test_verify_index_requires_an_index(tmp_path) -> None:
    store = _store(tmp_path)
    store.append(_sensor())
    with pytest.raises(StateError):
        store.verify_index()


# -- closure + cache --------------------------------------------------------


def test_closure_over_authored_facts(tmp_path) -> None:
    store = _store(tmp_path)
    store.append_all([_sensor(), _capability()])
    closure = store.closure(base_ontology())
    types = {
        a.o.name
        for a in closure.assertions
        if a.p == TYPE and isinstance(a.o, Term)
    }
    assert CLASS_ATOMIC in types
    assert "urn:cbp:core/Component" in types


def test_closure_cache_round_trips(tmp_path) -> None:
    store = _store(tmp_path)
    store.append(_sensor())
    store.index()
    closure = store.closure(base_ontology())
    store.cache_closure(closure)
    cached = store.cached_closure_hash(closure.ontology_hash, closure.profile)
    assert cached == closure.closure_hash()
    assert store.cached_closure_hash("0" * 64, closure.profile) is None


def test_closure_cache_refuses_a_different_graph(tmp_path) -> None:
    store = _store(tmp_path)
    store.append(_sensor())
    other = SemanticGraph(scope="component-a", assertions=(_capability(),))
    from agent_centric.cbp.ontology_runtime import compute_closure

    closure = compute_closure(other, base_ontology())
    with pytest.raises(OntologyError) as exc:
        store.cache_closure(closure)
    assert exc.value.kind == ErrorKind.MALFORMED_DOCUMENT
