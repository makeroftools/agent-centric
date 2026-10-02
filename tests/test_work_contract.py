"""Tests for the ``work.v1`` contract (SPEC-0021).

Deterministic and offline: the compile seam is pure, generated work is untrusted
until verified, and the dynamic task-load protocol is explicit and fail-closed
before any bind.
"""

from __future__ import annotations

import pytest

from agent_centric.contracts.capability import Capability
from agent_centric.contracts.component import (
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
)
from agent_centric.contracts.work import (
    CONTROL_CHANNEL,
    DATA_CHANNEL,
    INITIAL_CHANNELS,
    WORK_SCHEMA,
    BuildRecipe,
    GeneratorRef,
    SourceRef,
    TaskAnnouncement,
    TaskLoad,
    VerifierSpec,
    WorkCompilation,
    WorkEnvelope,
    WorkOrigin,
    WorkRecipe,
    WorkSourceKind,
    WorkVersion,
    admit_task_load,
    assert_work_executable,
    compile_work,
)

_D = "a" * 64
_L = "b" * 64


def _source() -> SourceRef:
    return SourceRef(
        repo_url="ssh://git/example/tool", commit="c" * 40, digest=_D, kind=WorkSourceKind.GIT
    )


def _generator(**overrides: object) -> GeneratorRef:
    base: dict[str, object] = {
        "ref": "model:generator",
        "digest": _D,
        "prompt": "write a parser",
        "seed": 7,
        "model": "stub",
    }
    base.update(overrides)
    return GeneratorRef(**base)  # type: ignore[arg-type]


def _recipe(**overrides: object) -> WorkRecipe:
    base: dict[str, object] = {
        "id": "tool",
        "build": BuildRecipe(language="python", runtime="python", toolchain=("python@3.13",)),
        "verifier": VerifierSpec(ref="verify:tool", digest=_L),
        "envelope": WorkEnvelope(timeout_seconds=10.0, max_steps=100),
        "source": _source(),
        "grants": (Capability(name="fs.read"),),
        "metadata": {"owner": "onprem"},
    }
    base.update(overrides)
    return WorkRecipe(**base)  # type: ignore[arg-type]


class TestSourceRef:
    def test_valid_and_round_trips(self) -> None:
        source = _source()
        assert SourceRef.from_dict(source.to_dict()) == source

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"repo_url": "", "commit": "c" * 40, "digest": _D},
            {"repo_url": "r", "commit": "short", "digest": _D},
            {"repo_url": "r", "commit": "c" * 40, "digest": "short"},
            {"repo_url": "r", "commit": "c" * 40, "digest": _D, "kind": "ftp"},
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            SourceRef(**kwargs)  # type: ignore[arg-type]


class TestGeneratorRef:
    def test_valid_and_round_trips(self) -> None:
        generator = _generator()
        assert GeneratorRef.from_dict(generator.to_dict()) == generator

    @pytest.mark.parametrize(
        "overrides",
        [
            {"ref": ""},
            {"digest": "short"},
            {"prompt": ""},
            {"seed": -1},
            {"seed": True},
            {"seed": "7"},
        ],
    )
    def test_invalid_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            _generator(**overrides)


class TestBuildRecipe:
    def test_valid_and_round_trips(self) -> None:
        build = BuildRecipe(language="rust", runtime="process", toolchain=("cargo@1.97",))
        assert BuildRecipe.from_dict(build.to_dict()) == build

    def test_unknown_runtime_rejected(self) -> None:
        with pytest.raises(ValueError):
            BuildRecipe(language="x", runtime="wasm")

    def test_duplicate_toolchain_rejected(self) -> None:
        with pytest.raises(ValueError):
            BuildRecipe(language="x", runtime="python", toolchain=("a", "a"))


class TestVerifierSpec:
    def test_valid_and_round_trips(self) -> None:
        verifier = VerifierSpec(ref="verify:x", digest=_D, conformance=True)
        assert VerifierSpec.from_dict(verifier.to_dict()) == verifier

    def test_bad_digest_rejected(self) -> None:
        with pytest.raises(ValueError):
            VerifierSpec(ref="verify:x", digest="short")


class TestWorkEnvelope:
    def test_valid(self) -> None:
        envelope = WorkEnvelope(timeout_seconds=5.0, max_steps=10, max_step_seconds=1.0)
        assert WorkEnvelope.from_dict(envelope.to_dict()) == envelope

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"timeout_seconds": 0.0, "max_steps": 1},
            {"timeout_seconds": -1.0, "max_steps": 1},
            {"timeout_seconds": 1.0, "max_steps": 0},
            {"timeout_seconds": 10.0, "max_steps": 1, "max_step_seconds": -1.0},
            {"timeout_seconds": 1.0, "max_steps": 1, "max_step_seconds": 5.0},
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            WorkEnvelope(**kwargs)  # type: ignore[arg-type]


class TestWorkRecipe:
    def test_requires_exactly_one_origin(self) -> None:
        with pytest.raises(ValueError):
            _recipe(source=None)  # neither
        with pytest.raises(ValueError):
            _recipe(generator=_generator())  # both

    def test_origin_is_derived(self) -> None:
        assert _recipe().origin == WorkOrigin.OBTAIN
        assert _recipe().is_generated is False
        generated = _recipe(source=None, generator=_generator())
        assert generated.origin == WorkOrigin.GENERATE
        assert generated.is_generated is True

    def test_hash_is_deterministic_and_grant_order_independent(self) -> None:
        forward = _recipe(grants=(Capability(name="fs.read"), Capability(name="net")))
        reverse = _recipe(grants=(Capability(name="net"), Capability(name="fs.read")))
        assert forward.recipe_hash() == reverse.recipe_hash()
        assert forward.recipe_hash() == _recipe(
            grants=(Capability(name="fs.read"), Capability(name="net"))
        ).recipe_hash()

    def test_distinct_recipes_have_distinct_hashes(self) -> None:
        assert _recipe().recipe_hash() != _recipe(id="other").recipe_hash()

    def test_round_trips(self) -> None:
        recipe = _recipe()
        assert WorkRecipe.from_dict(recipe.to_dict()) == recipe
        assert WorkRecipe.from_dict(recipe.to_dict()).recipe_hash() == recipe.recipe_hash()

    def test_constants(self) -> None:
        assert WORK_SCHEMA == "work.v1"
        assert WorkVersion.V1 == "work.v1"
        assert set(WorkOrigin.all()) == {"obtain", "generate"}


class TestCompileWork:
    def test_compiles_to_a_component(self) -> None:
        compilation = compile_work(_recipe())
        component = compilation.component
        assert isinstance(component, ComponentManifest)
        assert component.version == ComponentVersion.V1
        assert component.name == "tool"
        assert component.kind == ComponentKind.ATOMIC
        assert component.implements == ("component.v1",)
        assert component.entry == EntryDescriptor(runtime="python", entrypoint="work:tool")
        assert component.capabilities == (Capability(name="fs.read"),)

    def test_identical_recipe_is_byte_identical(self) -> None:
        first = compile_work(_recipe())
        second = compile_work(_recipe())
        assert first.canonical_bytes() == second.canonical_bytes()
        assert first.artifact_hash() == second.artifact_hash()

    def test_generator_distinguishes_the_artifact(self) -> None:
        a = _recipe(source=None, generator=_generator(prompt="a"))
        b = _recipe(source=None, generator=_generator(prompt="b"))
        assert compile_work(a).artifact_hash() != compile_work(b).artifact_hash()

    def test_starts_untrusted_and_announced_on_the_control_channel(self) -> None:
        compilation = compile_work(_recipe())
        assert compilation.assurance == "A0"
        assert compilation.channel == CONTROL_CHANNEL

    def test_origin_is_recorded(self) -> None:
        assert compile_work(_recipe()).origin == WorkOrigin.OBTAIN
        generated = compile_work(_recipe(source=None, generator=_generator()))
        assert generated.origin == WorkOrigin.GENERATE

    def test_obtained_work_carries_source_provenance(self) -> None:
        component = compile_work(_recipe()).component
        assert component.provenance is not None
        assert component.provenance.commit_sha == "c" * 40
        assert component.provenance.tree_sha256 == _D

    def test_generated_work_carries_no_provenance_yet(self) -> None:
        component = compile_work(_recipe(source=None, generator=_generator())).component
        assert component.provenance is None

    def test_round_trips_through_dict(self) -> None:
        compilation = compile_work(_recipe())
        restored = WorkCompilation.from_dict(compilation.to_dict())
        assert restored == compilation
        assert restored.artifact_hash() == compilation.artifact_hash()

    def test_compiled_component_rejects_edges(self) -> None:
        document = compile_work(_recipe()).component.to_dict()
        document["edges"] = []
        with pytest.raises(ValueError):
            ComponentManifest.from_dict(document)


class TestWorkExecutionGate:
    def test_unverified_work_refuses(self) -> None:
        with pytest.raises(ValueError):
            assert_work_executable(_recipe(), verifier_ok=False)

    def test_verified_work_without_conformance_allows(self) -> None:
        assert_work_executable(_recipe(), verifier_ok=True)

    def test_generated_conformance_verifier_requires_a_pass(self) -> None:
        recipe = _recipe(
            source=None,
            generator=_generator(),
            verifier=VerifierSpec(ref="verify:tool", digest=_L, conformance=True),
        )
        with pytest.raises(ValueError):
            assert_work_executable(recipe, verifier_ok=True, conformance_ok=False)
        assert_work_executable(recipe, verifier_ok=True, conformance_ok=True)


class TestTaskLoadProtocol:
    def test_announcement_only_over_the_control_channel(self) -> None:
        announcement = TaskAnnouncement(task="parse", content_hash=_D)
        assert announcement.channel == CONTROL_CHANNEL
        assert announcement.identity == _D
        with pytest.raises(ValueError):
            TaskAnnouncement(task="parse", content_hash=_D, channel=DATA_CHANNEL)

    def test_admit_requires_declared_and_pinned(self) -> None:
        load = TaskLoad(task="parse", content_hash=_D, artifact="work:parse")
        admitted = admit_task_load(load, declared_tasks=("parse",), expected_hash=_D)
        assert admitted.task == "parse"
        assert admitted.identity == _D

    def test_undeclared_task_refuses_before_any_bind(self) -> None:
        load = TaskLoad(task="ghost", content_hash=_D, artifact="work:ghost")
        with pytest.raises(ValueError):
            admit_task_load(load, declared_tasks=("parse",), expected_hash=_D)

    def test_unpinned_task_refuses_before_any_bind(self) -> None:
        load = TaskLoad(task="parse", content_hash=_D, artifact="work:parse")
        with pytest.raises(ValueError):
            admit_task_load(load, declared_tasks=("parse",), expected_hash=_L)
        with pytest.raises(ValueError):
            admit_task_load(load, declared_tasks=("parse",), expected_hash="nope")

    def test_identity_is_the_content_hash_never_ports(self) -> None:
        load = TaskLoad(task="parse", content_hash=_D, artifact="work:parse")
        assert load.identity == load.content_hash
        assert "ports" not in load.to_dict()

    def test_load_rejects_edges(self) -> None:
        with pytest.raises(ValueError):
            TaskLoad.from_dict(
                {
                    "task": "parse",
                    "content_hash": _D,
                    "artifact": "work:parse",
                    "edges": [],
                }
            )

    def test_load_channel_must_be_initial(self) -> None:
        assert CONTROL_CHANNEL in INITIAL_CHANNELS
        with pytest.raises(ValueError):
            TaskLoad(task="parse", content_hash=_D, artifact="work:parse", channel="adhoc")

    def test_round_trips(self) -> None:
        load = TaskLoad(task="parse", content_hash=_D, artifact="work:parse")
        assert TaskLoad.from_dict(load.to_dict()) == load
