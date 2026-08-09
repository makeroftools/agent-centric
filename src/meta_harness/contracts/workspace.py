"""Workspace contracts (versioned).

This module defines the structured contracts for the workspace specialty agent
(Volley 023). The design is deliberately narrow and fail-closed:

- ``WorkspaceLayout`` — an immutable allowlist of relative paths (files and
  directories) that an agent is permitted to touch. It is the single source of
  truth for what the mediated file tools may access.
- ``WorkspaceEntry`` — a resolved, validated entry (file or directory) within
  the workspace, with its relative path and (for files) its content.

The workspace is local-first and agent-centric: agents never gain broad
filesystem powers. Every mediated file tool resolves a requested relative path
against the workspace root and rejects any path that is not on the allowlist.
There is no deletion, rename, or arbitrary traversal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WorkspaceVersion(StrEnum):
    """Version of the workspace contract."""

    V1 = "workspace.v1"


class WorkspaceEntryKind(StrEnum):
    """Kind of a workspace entry."""

    FILE = "file"
    DIRECTORY = "directory"


def _require_relative_path(value: Any, name: str) -> str:
    """Coerce ``value`` to a valid relative path string, rejecting bad data.

    A valid relative path is a non-empty string, must not be absolute, must not
    contain ``..`` traversal, and must not be empty after stripping separators.

    Raises:
        ValueError: If ``value`` is not a valid relative path.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}.")
    if value.startswith("/") or value.startswith("\\"):
        raise ValueError(f"{name} must be relative, got {value!r}.")
    parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"{name} must not contain '..' traversal, got {value!r}.")
    return value


@dataclass(frozen=True)
class WorkspaceLayout:
    """An immutable allowlist of relative paths an agent may access.

    Attributes:
        files: The relative paths of files the agent may read/write.
        directories: The relative paths of directories the agent may list/create.
    """

    files: tuple[str, ...] = ()
    directories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.files:
            _require_relative_path(name, "file path")
        for name in self.directories:
            _require_relative_path(name, "directory path")

    def allows_file(self, relative_path: str) -> bool:
        """Return True if ``relative_path`` is on the file allowlist."""
        return relative_path in self.files

    def allows_directory(self, relative_path: str) -> bool:
        """Return True if ``relative_path`` is on the directory allowlist."""
        return relative_path in self.directories


@dataclass(frozen=True)
class WorkspaceEntry:
    """A resolved, validated entry within the workspace.

    Attributes:
        relative_path: The allowlisted relative path of the entry.
        kind: Whether the entry is a file or a directory.
        content: For a file, its text content; for a directory, None.
    """

    relative_path: str
    kind: WorkspaceEntryKind
    content: str | None = None

    def __post_init__(self) -> None:
        _require_relative_path(self.relative_path, "relative_path")
        if self.kind is WorkspaceEntryKind.FILE and self.content is None:
            raise ValueError("A file entry must carry content.")
        if self.kind is WorkspaceEntryKind.DIRECTORY and self.content is not None:
            raise ValueError("A directory entry must not carry content.")

    def as_mapping(self) -> dict[str, Any]:
        """Return the entry as a plain mapping (JSON-serialisable)."""
        return {
            "relative_path": self.relative_path,
            "kind": self.kind.value,
            "content": self.content,
        }