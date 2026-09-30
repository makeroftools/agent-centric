"""Deterministic component resolver (SPEC-0007 §4).

``lock -> fetch -> verify hash -> verify signature -> check conformance -> cache``

The order is deterministic (entries sorted by name) and every step **fails
closed**: a hash mismatch, a bad signature, or an unknown contract version
refuses resolution. Sources are local (a mirrored directory), so resolution is
offline — no network is touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..contracts.components_lock import ComponentsLock, LockEntry
from .cache import ContentAddressedCache, sha256_bytes
from .signing import SignatureError, SignatureVerifier


class ResolveError(Exception):
    """A component could not be resolved and verified (fail-closed)."""


class ComponentSource(Protocol):
    """A local, offline source of component artifacts."""

    def fetch(self, entry: LockEntry) -> bytes:
        """Return the artifact bytes for ``entry`` (raise on absence)."""
        ...


class DirectorySource:
    """An offline source: a directory of artifacts named ``<component><suffix>``."""

    def __init__(self, root: Path, *, suffix: str = ".tar") -> None:
        self._root = Path(root)
        self._suffix = suffix

    def fetch(self, entry: LockEntry) -> bytes:
        path = self._root / f"{entry.name}{self._suffix}"
        if not path.is_file():
            raise ResolveError(f"artifact for {entry.name!r} not found at {path}")
        return path.read_bytes()


@dataclass(frozen=True)
class ResolvedComponent:
    """A verified, cached component."""

    entry: LockEntry
    digest: str
    size: int


class Resolver:
    """Resolve and verify the pinned components of a lock."""

    def __init__(
        self,
        lock: ComponentsLock,
        cache: ContentAddressedCache,
        *,
        source: ComponentSource,
        verifier: SignatureVerifier | None,
    ) -> None:
        self._lock = lock
        self._cache = cache
        self._source = source
        self._verifier = verifier
        self._supported = frozenset(lock.harness_contracts)

    def resolve(self, name: str) -> ResolvedComponent:
        """Fetch, verify, and cache the component ``name`` (fail-closed)."""
        entry = self._lock.entry(name)
        data = self._source.fetch(entry)
        digest = sha256_bytes(data)
        if digest != entry.tree_sha256:
            raise ResolveError(
                f"{name}: tree hash mismatch "
                f"(expected {entry.tree_sha256}, got {digest})"
            )
        for contract in entry.implements:
            if contract not in self._supported:
                raise ResolveError(f"{name}: implements unknown contract {contract!r}")
        if self._verifier is None:
            raise ResolveError(
                f"{name}: signature verification required but no verifier configured"
            )
        try:
            self._verifier.verify(data, entry.signature.encode("utf-8"))
        except SignatureError as exc:
            raise ResolveError(f"{name}: signature verification failed: {exc}") from exc
        self._cache.put(data)
        return ResolvedComponent(entry=entry, digest=digest, size=len(data))

    def resolve_all(self) -> tuple[ResolvedComponent, ...]:
        """Resolve every entry in deterministic (name-sorted) order."""
        ordered = sorted(self._lock.entries, key=lambda e: e.name)
        return tuple(self.resolve(entry.name) for entry in ordered)
