"""Tests for the content-addressed, verify-on-read cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.cbp.cache import (
    CacheError,
    ContentAddressedCache,
    atomic_write,
    sha256_bytes,
)


class TestCache:
    def test_put_get_roundtrip(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path)
        digest = cache.put(b"hello")
        assert digest == sha256_bytes(b"hello")
        assert cache.get(digest) == b"hello"

    def test_put_is_idempotent(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path)
        assert cache.put(b"x") == cache.put(b"x")

    def test_missing_digest_is_none(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path)
        assert cache.get("0" * 64) is None
        assert cache.has("0" * 64) is False

    def test_corruption_fails_closed(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path)
        digest = sha256_bytes(b"good")
        path = tmp_path / digest[:2] / digest
        atomic_write(path, b"evil")
        with pytest.raises(CacheError):
            cache.get(digest)
        assert cache.has(digest) is False

    def test_atomic_write_creates_parents(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "file"
        atomic_write(target, b"data")
        assert target.read_bytes() == b"data"

    def test_bad_digest_rejected(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path)
        with pytest.raises(CacheError):
            cache.get("not-a-digest")

    def test_put_repairs_a_corrupt_entry(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path)
        digest = sha256_bytes(b"good")
        atomic_write(tmp_path / digest[:2] / digest, b"evil")
        assert cache.put(b"good") == digest
        assert cache.get(digest) == b"good"

    def test_atomic_write_leaves_no_temp_files(self, tmp_path: Path) -> None:
        target = tmp_path / "dir" / "file"
        atomic_write(target, b"data")
        leftovers = [
            entry.name for entry in (tmp_path / "dir").iterdir() if entry.name != "file"
        ]
        assert leftovers == []
