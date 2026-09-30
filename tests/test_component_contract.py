"""Tests for the ``component.v1`` contract."""

from __future__ import annotations

import pytest

from agent_centric.contracts.component import (
    ChildRef,
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
    Provenance,
    StateDescriptor,
)

_SHA = "a" * 64
_COMMIT = "b" * 40


def _entry() -> EntryDescriptor:
    return EntryDescriptor(runtime="python", entrypoint="pkg.mod:make")


class TestComponentManifest:
    def test_valid_atomic(self) -> None:
        m = ComponentManifest(
            version=ComponentVersion.V1,
            name="counter",
            kind=ComponentKind.ATOMIC,
            entry=_entry(),
            implements=("component.v1",),
            state=StateDescriptor(kind="sqlite", path="state.db"),
        )
        assert m.name == "counter"
        assert m.state is not None and m.state.single_writer

    def test_unknown_version_rejected(self) -> None:
        with pytest.raises(ValueError):
            ComponentManifest(
                version="component.v99",
                name="x",
                kind=ComponentKind.ATOMIC,
                entry=_entry(),
                implements=("component.v1",),
            )

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError):
            ComponentManifest(
                version=ComponentVersion.V1,
                name="x",
                kind="widget",
                entry=_entry(),
                implements=("component.v1",),
            )

    def test_implements_required(self) -> None:
        with pytest.raises(ValueError):
            ComponentManifest(
                version=ComponentVersion.V1,
                name="x",
                kind=ComponentKind.ATOMIC,
                entry=_entry(),
            )

    def test_children_only_on_composites(self) -> None:
        with pytest.raises(ValueError):
            ComponentManifest(
                version=ComponentVersion.V1,
                name="x",
                kind=ComponentKind.ATOMIC,
                entry=_entry(),
                implements=("component.v1",),
                children=(ChildRef(ref="child"),),
            )

    def test_composite_children_ok(self) -> None:
        m = ComponentManifest(
            version=ComponentVersion.V1,
            name="suite",
            kind=ComponentKind.COMPOSITE,
            entry=_entry(),
            implements=("component.v1",),
            children=(ChildRef(embed="children/a"), ChildRef(ref="counter")),
        )
        assert [c.mode for c in m.children] == ["embed", "ref"]

    def test_to_dict_shape(self) -> None:
        m = ComponentManifest(
            version=ComponentVersion.V1,
            name="counter",
            kind=ComponentKind.ATOMIC,
            entry=_entry(),
            implements=("component.v1",),
        )
        d = m.to_dict()
        assert d["version"] == "component.v1"
        assert d["entry"] == {"runtime": "python", "entrypoint": "pkg.mod:make"}


class TestChildRef:
    def test_exactly_one_of_embed_ref(self) -> None:
        with pytest.raises(ValueError):
            ChildRef(embed="a", ref="b")
        with pytest.raises(ValueError):
            ChildRef()
        assert ChildRef(ref="r").target == "r"


class TestStateDescriptor:
    def test_rejects_unknown_kind_and_not_single_writer(self) -> None:
        with pytest.raises(ValueError):
            StateDescriptor(kind="postgres", path="x")
        with pytest.raises(ValueError):
            StateDescriptor(kind="sqlite", path="x", single_writer=False)


class TestProvenance:
    def test_accepts_sha1_and_sha256_commits(self) -> None:
        assert Provenance("ssh://x", _COMMIT, _SHA).commit_sha == _COMMIT
        assert Provenance("ssh://x", _SHA, _SHA).tree_sha256 == _SHA

    def test_rejects_bad_hashes(self) -> None:
        with pytest.raises(ValueError):
            Provenance("ssh://x", "zz", _SHA)
        with pytest.raises(ValueError):
            Provenance("ssh://x", _COMMIT, "short")
