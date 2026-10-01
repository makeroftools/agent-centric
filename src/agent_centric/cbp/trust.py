"""Rotating trust store — overlapping trust windows (SPEC-0007 §6).

Signing keys **rotate**: each key has a validity window, and during a rotation the
outgoing and incoming keys **overlap**. In that overlap the outgoing key is still
trusted to *verify* (so a lock signed just before the change stays valid), while
only the incoming key is authorized to *sign* new releases. Every release records
the ``key_id`` that signed it (the transparency log), so a historical record is
verified by exactly the key that made it.

Determinism: time is **explicit**. A rotation happens "at" a caller-supplied,
recorded instant (a release epoch), never a wall-clock read; the same inputs
always select the same signing key and verify identically.

This module is the key-rotation remainder of SPEC-0007 §6. It composes with the
existing primitives: a :class:`TrustStore` is a :class:`SignatureVerifier`, so it
can be passed straight to the resolver and to ``boot_from_lock``; and
:meth:`TrustStore.log_verifiers` builds the ``key_id -> verifier`` mapping the
transparency log already accepts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .signing import SignatureError, SignatureVerifier
from .signing_service import Signer


class TrustError(Exception):
    """A trust window or key selection violated an invariant (fail-closed)."""


@dataclass(frozen=True)
class TrustWindow:
    """A signing key's validity window.

    Attributes:
        key_id: The key's identifier (recorded in the transparency log).
        verifier: The verifier for this key's detached signatures.
        not_before: The instant (inclusive) the key becomes trusted.
        not_after: The instant (exclusive) the key stops being trusted to sign.
        signer: The signer for this key, or ``None`` if the key may only verify
            (the outgoing key during a rotation overlap).
    """

    key_id: str
    verifier: SignatureVerifier
    not_before: int
    not_after: int
    signer: Signer | None = None

    def __post_init__(self) -> None:
        if not self.key_id:
            raise TrustError("trust window key_id must be non-empty")
        if self.not_before >= self.not_after:
            raise TrustError(
                f"{self.key_id}: not_before must be < not_after "
                f"({self.not_before} >= {self.not_after})"
            )
        if self.signer is not None and self.signer.key_id != self.key_id:
            raise TrustError(
                f"{self.key_id}: signer key_id {self.signer.key_id!r} does not match"
            )

    def covers(self, at: int) -> bool:
        """Return whether ``at`` falls within this key's trust window."""
        return self.not_before <= at < self.not_after


class TrustStore:
    """A deterministic ring of rotating keys with overlapping trust windows."""

    def __init__(self, windows: Iterable[TrustWindow]) -> None:
        ordered = tuple(sorted(windows, key=lambda window: window.key_id))
        if not ordered:
            raise TrustError("a trust store needs at least one key")
        seen: set[str] = set()
        for window in ordered:
            if window.key_id in seen:
                raise TrustError(f"duplicate trust window for key {window.key_id!r}")
            seen.add(window.key_id)
        self._windows = ordered
        self._by_id = {window.key_id: window for window in ordered}

    @property
    def key_ids(self) -> tuple[str, ...]:
        """All trusted key ids, in deterministic (name-sorted) order."""
        return tuple(window.key_id for window in self._windows)

    def active_key_ids(self, at: int) -> tuple[str, ...]:
        """The key ids whose trust window covers ``at`` (may overlap)."""
        return tuple(
            window.key_id for window in self._windows if window.covers(at)
        )

    def window(self, key_id: str) -> TrustWindow:
        """Return the window for ``key_id`` (fail-closed if unknown)."""
        window = self._by_id.get(key_id)
        if window is None:
            raise TrustError(f"unknown trusted key {key_id!r}")
        return window

    def signer_at(self, at: int) -> Signer:
        """Return the single key authorized to sign at ``at`` (fail-closed).

        Exactly one key covering ``at`` must carry a signer: none (the instant is
        outside every window) or more than one (an ambiguous overlap) refuses.
        """
        candidates = [
            window
            for window in self._windows
            if window.covers(at) and window.signer is not None
        ]
        if not candidates:
            raise TrustError(f"no signing key is valid at {at}")
        if len(candidates) > 1:
            raise TrustError(
                "ambiguous signing keys at "
                f"{at}: {[window.key_id for window in candidates]}"
            )
        signer = candidates[0].signer
        assert signer is not None
        return signer

    def verifier_for(self, key_id: str) -> SignatureVerifier:
        """Return the verifier for a (possibly retired) key id."""
        return self.window(key_id).verifier

    def log_verifiers(self) -> Mapping[str, SignatureVerifier]:
        """A ``key_id -> verifier`` mapping for transparency-log verification."""
        return {window.key_id: window.verifier for window in self._windows}

    def verified_key(
        self, payload: bytes, signature: bytes, *, at: int | None = None
    ) -> str:
        """Return the key id that verifies a detached signature, else refuse.

        With ``at`` given, only keys whose window covers ``at`` are tried; without
        it, every trusted key is tried (any era). A key is **revoked** by removing
        it from the store.
        """
        candidates = (
            self._windows
            if at is None
            else tuple(window for window in self._windows if window.covers(at))
        )
        for window in candidates:
            try:
                window.verifier.verify(payload, signature)
            except SignatureError:
                continue
            return window.key_id
        raise SignatureError("signature verifies under no trusted key")

    def verify(self, payload: bytes, signature: bytes) -> None:
        """Verify against any trusted key (the :class:`SignatureVerifier` surface)."""
        self.verified_key(payload, signature)
