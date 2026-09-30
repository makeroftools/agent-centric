"""Append-only, tamper-evident transparency log (SPEC-0007 §6, Phase 0.5b).

Every signed release is appended to a hash-chained log, so an altered or
unauthorized signature is detectable and history cannot be silently rewritten
(Law 10: evidence is immutable). The log is a single JSON-lines file; each record
carries the previous record's hash in ``prev``, so editing any record breaks the
chain at every later record, and a pinned expected head detects truncation.

Records carry **no wall-clock time**, so the log is deterministic and
reproducible. Verification is fail-closed: a malformed line, a broken link, a bad
signature, or an unknown signing key is an explicit :class:`TransparencyError`,
never a partial success.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .signing import SignatureError, SignatureVerifier

LOG_SCHEMA = "transparency.log/v1"
GENESIS = "0" * 64

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class TransparencyError(Exception):
    """The transparency log is malformed, tampered, or unverifiable (fail-closed)."""


@dataclass(frozen=True)
class TransparencyEntry:
    """One release record: a signed ``lock_hash`` chained to its predecessor."""

    seq: int
    prev: str
    lock_hash: str
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise TransparencyError("transparency entry seq must be non-negative")
        if not _HEX64_RE.match(self.prev):
            raise TransparencyError("transparency entry prev must be 64-hex")
        if not _HEX64_RE.match(self.lock_hash):
            raise TransparencyError("transparency entry lock_hash must be 64-hex")
        if not self.key_id:
            raise TransparencyError("transparency entry key_id must be non-empty")
        if not self.signature:
            raise TransparencyError("transparency entry signature must be non-empty")

    def _core(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "prev": self.prev,
            "lock_hash": self.lock_hash,
            "key_id": self.key_id,
            "signature": self.signature,
        }

    def canonical_bytes(self) -> bytes:
        """Canonical serialization of the record's core (the hashed content)."""
        return json.dumps(
            self._core(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def entry_hash(self) -> str:
        """This record's content address (sha256, hex) — the next record's ``prev``."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_line(self) -> str:
        """Serialize the record as one JSON line (schema-tagged)."""
        document = {"schema": LOG_SCHEMA, **self._core()}
        return json.dumps(document, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_line(
        cls, line: str, *, expected_seq: int, expected_prev: str
    ) -> TransparencyEntry:
        """Parse one log line and check its position in the chain (fail-closed)."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TransparencyError(
                f"log record {expected_seq} is not JSON: {exc}"
            ) from exc
        if not isinstance(data, dict) or data.get("schema") != LOG_SCHEMA:
            raise TransparencyError(f"log record {expected_seq} has a bad schema")
        try:
            entry = cls(
                seq=int(data["seq"]),
                prev=str(data["prev"]),
                lock_hash=str(data["lock_hash"]),
                key_id=str(data["key_id"]),
                signature=str(data["signature"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TransparencyError(
                f"log record {expected_seq} is malformed: {exc}"
            ) from exc
        if entry.seq != expected_seq:
            raise TransparencyError(
                f"log record out of order: expected seq {expected_seq}, got {entry.seq}"
            )
        if entry.prev != expected_prev:
            raise TransparencyError(
                f"log record {expected_seq} breaks the chain "
                f"(expected prev {expected_prev}, got {entry.prev})"
            )
        return entry


class TransparencyLog:
    """An append-only, hash-chained transparency log backed by a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """The backing file path."""
        return self._path

    def read(self) -> tuple[TransparencyEntry, ...]:
        """Read and validate the chain (links and ordering), without signatures.

        Raises:
            TransparencyError: On a blank/malformed line or a broken chain.
        """
        if not self._path.is_file():
            return ()
        entries: list[TransparencyEntry] = []
        prev = GENESIS
        for index, raw in enumerate(self._path.read_text(encoding="utf-8").splitlines()):
            if not raw.strip():
                raise TransparencyError(f"log record {index} is blank")
            entry = TransparencyEntry.from_line(raw, expected_seq=index, expected_prev=prev)
            entries.append(entry)
            prev = entry.entry_hash()
        return tuple(entries)

    def head(self) -> str:
        """The current chain head (``GENESIS`` when the log is empty)."""
        entries = self.read()
        return entries[-1].entry_hash() if entries else GENESIS

    def append(self, lock_hash: str, key_id: str, signature: str) -> TransparencyEntry:
        """Append a signed release, chained to the current head (durable).

        The existing chain is validated first, so a corrupt or tampered log is
        never extended. The write is ``fsync``\\ ed.

        Raises:
            TransparencyError: If the log is corrupt or the record is invalid.
        """
        entries = self.read()
        entry = TransparencyEntry(
            seq=len(entries),
            prev=entries[-1].entry_hash() if entries else GENESIS,
            lock_hash=lock_hash,
            key_id=key_id,
            signature=signature,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_line() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def verify(
        self,
        verifier: SignatureVerifier | Mapping[str, SignatureVerifier],
        *,
        expected_head: str | None = None,
    ) -> tuple[TransparencyEntry, ...]:
        """Verify the whole log: chain, signatures, and (optionally) the head.

        Args:
            verifier: One verifier for every record, or a mapping from ``key_id``
                to verifier (supports rotation; an unknown key fails closed).
            expected_head: When given, the computed head must equal it — this
                detects truncation or a rewrite.

        Raises:
            TransparencyError: On any chain, signature, key, or head failure.
        """
        entries = self.read()
        mapping: Mapping[str, SignatureVerifier] | None
        single: SignatureVerifier | None
        if isinstance(verifier, Mapping):
            mapping = verifier
            single = None
        else:
            mapping = None
            single = verifier
        for entry in entries:
            if mapping is not None:
                selected = mapping.get(entry.key_id)
                if selected is None:
                    raise TransparencyError(
                        f"log record {entry.seq} uses unknown signing key "
                        f"{entry.key_id!r}"
                    )
            else:
                assert single is not None
                selected = single
            try:
                selected.verify(
                    entry.lock_hash.encode("utf-8"), entry.signature.encode("utf-8")
                )
            except SignatureError as exc:
                raise TransparencyError(
                    f"log record {entry.seq} signature is invalid: {exc}"
                ) from exc
        if expected_head is not None:
            actual = entries[-1].entry_hash() if entries else GENESIS
            if actual != expected_head:
                raise TransparencyError(
                    "transparency head mismatch (truncation or rewrite): "
                    f"expected {expected_head}, got {actual}"
                )
        return entries
