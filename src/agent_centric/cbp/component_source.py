"""Offline component sources and pinning (SPEC-0007 §4, Phase 1a).

A source yields a component bundle. ``GitSource`` materializes a bundle from a
**local git repository** at a pinned commit (offline, no network): it archives
the commit and canonicalizes it with :mod:`agent_centric.cbp.component_bundle`.
``pin_from_git`` computes the immutable pin (``commit_sha`` + ``tree_sha256``)
for a local repository.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..contracts.components_lock import LockEntry
from .component_bundle import build_bundle_from_dir, bundle_sha256
from .resolver import ResolveError


class GitError(RuntimeError):
    """A git operation failed (fail-closed)."""


@dataclass(frozen=True)
class PinnedComponent:
    """The immutable pin of a component: its commit and bundle hash."""

    commit_sha: str
    tree_sha256: str
    bundle: bytes


def _git(repo: Path, args: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, check=True
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise GitError("git is not available") from exc
    except subprocess.CalledProcessError as exc:
        raise GitError(exc.stderr.decode(errors="replace").strip()) from exc
    return completed.stdout


def git_commit(repo: Path, ref: str = "HEAD") -> str:
    """Resolve ``ref`` to an immutable commit SHA."""
    return _git(Path(repo), ["rev-parse", ref]).decode("utf-8").strip()


def bundle_at(repo: Path, commit: str) -> bytes:
    """Build a deterministic bundle from a local repo at ``commit`` (offline)."""
    raw = _git(Path(repo), ["archive", "--format=tar", commit])
    with tempfile.TemporaryDirectory(prefix="component-src-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            tar.extractall(tmp, filter="data")
        return build_bundle_from_dir(Path(tmp))


def pin_from_git(repo: Path, ref: str = "HEAD") -> PinnedComponent:
    """Pin a local component repository: commit SHA + bundle hash."""
    commit = git_commit(repo, ref)
    bundle = bundle_at(repo, commit)
    return PinnedComponent(commit_sha=commit, tree_sha256=bundle_sha256(bundle), bundle=bundle)


class GitSource:
    """Resolve components from local git repositories, keyed by component name."""

    def __init__(self, repos: Mapping[str, Path]) -> None:
        self._repos = {name: Path(path) for name, path in repos.items()}

    def fetch(self, entry: LockEntry) -> bytes:
        repo = self._repos.get(entry.name)
        if repo is None:
            raise ResolveError(f"no git repository configured for {entry.name!r}")
        return bundle_at(repo, entry.commit_sha)
