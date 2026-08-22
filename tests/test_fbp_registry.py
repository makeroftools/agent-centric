"""Tests for the FBP passive registry (``registry.py``).

The registry is a **passive metadata catalog** — it stores and serves record
metadata (name, kind, source location) and can reconstruct an importable
callable from its recorded ``module``/``qualname`` so a fresh process can
re-resolve a directive without re-seeding by hand. These tests cover the
``callable_from_source`` reconstruction path and its fail-closed branches.
"""

from __future__ import annotations

from agent_centric.fbp.registry import Registry, RegistryEntry


def _twofold(value: int) -> int:
    return value * 2


class TestRegistryCallableFromSource:
    def test_reconstructs_from_recorded_importable_source(self) -> None:
        reg = Registry()
        reg.register_entry(
            RegistryEntry(
                name="twofold",
                callable=None,
                module="tests.test_fbp_registry",
                qualname="_twofold",
            )
        )
        fn = reg.callable_from_source("twofold")
        assert callable(fn)
        assert fn is not None
        assert fn(21) == 42  # type: ignore[misc]

    def test_returns_none_for_unknown_name(self) -> None:
        assert Registry().callable_from_source("ghost") is None

    def test_returns_callable_when_already_registered(self) -> None:
        reg = Registry()
        reg.register("_two", _twofold)
        assert reg.callable_from_source("_two") is _twofold

    def test_returns_none_without_module_or_qualname(self) -> None:
        reg = Registry()
        reg.register_entry(RegistryEntry(name="_x"))
        assert reg.callable_from_source("_x") is None

    def test_returns_none_on_import_error(self) -> None:
        reg = Registry()
        reg.register_entry(
            RegistryEntry(name="_y", module="no_such_module_xyz", qualname="f")
        )
        assert reg.callable_from_source("_y") is None

    def test_returns_none_when_qualname_resolves_noncallable(self) -> None:
        reg = Registry()
        reg.register_entry(
            RegistryEntry(name="_z", module="tests.test_fbp_registry", qualname="_NONCALLABLE")
        )
        assert reg.callable_from_source("_z") is None


_NONCALLABLE = 42