"""Content-addressed, atomic, verify-on-read cache (SPEC-0007 §6/§7).

The cache is keyed by the artifact's ``sha256`` and is written atomically
(temp file + ``fsync`` + ``os.replace``). Every read re-hashes the bytes and
fails closed on mismatch, so a corrupted cache cannot silently serve a wrong
component.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CacheError(Exception):
    """A cache operation violated an invariant (fail-closed)."""


def sha256_bytes(data: bytes) -> str:
    """Return the hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file's contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp + fsync + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class ContentAddressedCache:
    """A content-addressed store rooted at a directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, digest: str) -> Path:
        if not _SHA256_RE.match(digest):
            raise CacheError(f"Not a sha256 digest: {digest!r}")
        return self._root / digest[:2] / digest

    def put(self, data: bytes) -> str:
        """Store ``data``; return its digest. Idempotent."""
        digest = sha256_bytes(data)
        path = self._path(digest)
        if path.is_file() and sha256_file(path) == digest:
            return digest
        atomic_write(path, data)
        return digest

    def get(self, digest: str) -> bytes | None:
        """Return the bytes for ``digest``, verifying the hash (fail-closed)."""
        path = self._path(digest)
        if not path.is_file():
            return None
        data = path.read_bytes()
        if sha256_bytes(data) != digest:
            raise CacheError(f"Cache corruption: {digest}")
        return data

    def has(self, digest: str) -> bool:
        """Return whether ``digest`` is present **and** verifies."""
        try:
            return self.get(digest) is not None
        except CacheError:
            return False
