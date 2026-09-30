"""Components lock contract (versioned) — ``components.lock/v1``.

The lock is the pinned, signed set of external components the harness resolves.
It is the **deterministic lock**: `lock_hash` = sha256 of its canonical
serialization, so the umbrella commit + `lock_hash` is the whole-system content
address (see ``specs/SPEC-0007-harness-shell-component-distribution.md`` §3.2).

Invariants enforced here:

- every entry is pinned by an immutable ``commit_sha`` and a ``tree_sha256``;
- entry names are unique;
- the declared ``root`` (the shell component) is present in the entries;
- ``harness_contracts`` lists the exact contract versions the harness supports.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

LOCK_SCHEMA = "components.lock/v1"

_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LockError(ValueError):
    """A components lock is malformed or violates an invariant (fail-closed)."""


@dataclass(frozen=True)
class LockEntry:
    """A single pinned, signed component in the lock."""

    name: str
    repo_url: str
    commit_sha: str
    tree_sha256: str
    implements: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise LockError("Lock entry name must be non-empty.")
        if not self.repo_url:
            raise LockError(f"{self.name}: repo_url must be non-empty.")
        if not _COMMIT_RE.match(self.commit_sha):
            raise LockError(f"{self.name}: commit_sha must be 40/64-hex.")
        if not _SHA256_RE.match(self.tree_sha256):
            raise LockError(f"{self.name}: tree_sha256 must be 64-hex.")
        if not self.implements:
            raise LockError(f"{self.name}: implements must be non-empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo_url": self.repo_url,
            "commit_sha": self.commit_sha,
            "tree_sha256": self.tree_sha256,
            "implements": list(self.implements),
            "capabilities": list(self.capabilities),
            "signature": self.signature,
        }


@dataclass(frozen=True)
class ComponentsLock:
    """The pinned component set."""

    root: str
    entries: tuple[LockEntry, ...]
    harness_contracts: tuple[str, ...]
    schema: str = LOCK_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != LOCK_SCHEMA:
            raise LockError(f"Unsupported lock schema: {self.schema!r}")
        if not self.root:
            raise LockError("Lock root must be non-empty.")
        if not self.entries:
            raise LockError("Lock must contain at least one entry.")
        if not self.harness_contracts:
            raise LockError("Lock must declare harness_contracts.")
        names = [e.name for e in self.entries]
        if len(names) != len(set(names)):
            raise LockError("Lock entry names must be unique.")
        if self.root not in set(names):
            raise LockError(f"Lock root {self.root!r} is not among the entries.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "harness_contracts": list(self.harness_contracts),
            "root": self.root,
            "entries": [e.to_dict() for e in self.entries],
        }

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for hashing/signing."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def lock_hash(self) -> str:
        """The content address of this lock (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def entry(self, name: str) -> LockEntry:
        for candidate in self.entries:
            if candidate.name == name:
                return candidate
        raise LockError(f"No lock entry named {name!r}.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentsLock:
        try:
            schema = str(data["schema"])
            root = str(data["root"])
            raw_entries = data["entries"]
            harness = tuple(str(x) for x in data["harness_contracts"])
        except KeyError as exc:
            raise LockError(f"Lock missing field: {exc!r}") from exc
        if not isinstance(raw_entries, list):
            raise LockError("Lock entries must be a list.")
        entries: list[LockEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise LockError("Each lock entry must be an object.")
            entries.append(
                LockEntry(
                    name=str(raw.get("name", "")),
                    repo_url=str(raw.get("repo_url", "")),
                    commit_sha=str(raw.get("commit_sha", "")),
                    tree_sha256=str(raw.get("tree_sha256", "")),
                    implements=tuple(str(x) for x in raw.get("implements", ())),
                    capabilities=tuple(str(x) for x in raw.get("capabilities", ())),
                    signature=str(raw.get("signature", "")),
                )
            )
        return cls(root=root, entries=tuple(entries), harness_contracts=harness, schema=schema)
