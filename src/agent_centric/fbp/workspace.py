"""Allowlisted workspace capability for the FBP subsystem.

This is a port of ``main``'s hardened, allowlisted focus capability into the
FBP model as a pure **capability** (a managed, mediated file-resource holder —
no deletion, no traversal, no arbitrary path access).

The trust model is the security-relevant one: an agent may access **only** the
paths on an explicit allowlist (exact files, exact directories, and prefix
directories). Anything else — including any path that escapes the workspace
root — fails closed. Writes require the parent directory to already exist (no
implicit directory creation), and there is no delete/move. This is the resource
guard for a managed agent environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Entry kinds in a workspace listing.
FILE = "file"
DIRECTORY = "directory"


class WorkspaceError(RuntimeError):
    """A workspace access was denied or failed (fail-closed)."""


@dataclass(frozen=True)
class WorkspaceLayout:
    """The explicit allowlist of relative paths a workspace may access.

    Attributes:
        files: Exact relative file paths an agent may read/write.
        directories: Exact relative directories that may be created/listed.
        prefixes: Relative directory prefixes under which files may be read.
    """

    files: tuple[str, ...] = ()
    directories: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()

    def allows(self, rel: str) -> bool:
        """True if ``rel`` is an exact allowlisted file/dir, or lies under an
        allowlisted prefix (with or without a trailing slash)."""
        if rel in self.files or rel in self.directories:
            return True
        for p in self.prefixes:
            prefix = p if p.endswith("/") else f"{p}/"
            if rel.startswith(prefix):
                return True
        return False

    def allows_directory(self, rel: str) -> bool:
        return rel in self.directories


@dataclass
class WorkspaceEntry:
    """A read/written workspace entry (JSON-ready)."""

    relative_path: str
    kind: str
    content: Any = None

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "kind": self.kind,
            "content": self.content,
        }


class WorkspaceFS:
    """A local workspace rooted at a directory with an explicit allowlist.

    Mediates every access under ``root`` so an agent can only touch allowed
    paths (fail-closed). No deletion, no traversal, no implicit directory
    creation.
    """

    def __init__(self, root: str | Path, layout: WorkspaceLayout | None = None) -> None:
        self._root = Path(root).resolve()
        self._layout = layout or WorkspaceLayout()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def layout(self) -> WorkspaceLayout:
        return self._layout

    def _resolve(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel:
            raise WorkspaceError("A relative path is required.")
        candidate = (self._root / rel).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise WorkspaceError(f"Path {rel!r} escapes the workspace root.")
        return candidate

    def _require_allowed(self, rel: str) -> None:
        if not self._layout.allows(rel):
            raise WorkspaceError(f"Path {rel!r} is not on the workspace allowlist.")

    # -- allowlisted, mediated tools -----------------------------------------

    def list_workspace(self) -> dict[str, str]:
        """List the allowlisted entries that exist, as ``rel -> kind``."""
        result: dict[str, str] = {}
        for rel in self._layout.files:
            if self._resolve(rel).is_file():
                result[rel] = FILE
        for rel in self._layout.directories:
            if self._resolve(rel).is_dir():
                result[rel] = DIRECTORY
        return result

    def read_bytes(self, rel: str) -> bytes:
        """Return raw bytes of an allowed file (e.g. a PDF)."""
        path = self._resolve(rel)  # traversal guard first (fail-closed)
        self._require_allowed(rel)
        if not path.is_file():
            raise WorkspaceError(f"Workspace file {rel!r} does not exist.")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"Could not read workspace file {rel!r}: {exc}") from exc

    def read_text(self, rel: str) -> WorkspaceEntry:
        """Read an allowed file's text (exact allowlist or under a prefix)."""
        path = self._resolve(rel)  # traversal guard first (fail-closed)
        self._require_allowed(rel)
        if not path.is_file():
            raise WorkspaceError(f"Workspace file {rel!r} does not exist.")
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(f"Could not read workspace file {rel!r}: {exc}") from exc
        return WorkspaceEntry(rel, FILE, content)

    def list_prefix(self, prefix: str) -> dict[str, str]:
        """List the files directly under an allowlisted directory prefix."""
        if prefix not in self._layout.prefixes:
            raise WorkspaceError(f"Prefix {prefix!r} is not on the workspace allowlist.")
        base = self._resolve(prefix)
        if not base.is_dir():
            return {}
        result: dict[str, str] = {}
        for child in base.iterdir():
            if child.is_file():
                result[f"{prefix}{child.name}"] = FILE
        return result

    def write_text(self, rel: str, content: str) -> WorkspaceEntry:
        """Write an allowlisted file (parent dir must already exist)."""
        path = self._resolve(rel)  # traversal guard first (fail-closed)
        self._require_allowed(rel)
        if not path.parent.is_dir():
            raise WorkspaceError(
                f"Parent directory of {rel!r} does not exist; create it first."
            )
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(f"Could not write workspace file {rel!r}: {exc}") from exc
        return WorkspaceEntry(rel, FILE, content)

    def create_dir(self, rel: str) -> WorkspaceEntry:
        """Create an allowlisted directory (fail-closed if not allowed)."""
        if not self._layout.allows_directory(rel):
            raise WorkspaceError(f"Path {rel!r} is not on the workspace directory allowlist.")
        path = self._resolve(rel)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"Could not create workspace dir {rel!r}: {exc}") from exc
        return WorkspaceEntry(rel, DIRECTORY, None)