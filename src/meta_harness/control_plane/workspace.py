"""Allowlisted, Manager-mediated workspace file tools (Volley 023).

This module provides a local, agent-centric workspace: a root directory plus an
explicit allowlist of relative paths (``WorkspaceLayout``) that an agent is
permitted to touch. Every file tool is a deterministic, side-effect-bounded
function that resolves a requested relative path against the root and **rejects
any path that is not on the allowlist** (fail-closed). There is no deletion,
rename, or arbitrary traversal, so an agent can never gain broad filesystem
powers.

The tools are pure with respect to the agent: an agent can only *request* them
by name; execution happens in the control plane (the Manager / ToolRegistry).
The workspace is local-first and fully auditable through the normal trajectory
recording.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contracts.tool import ToolDescriptor
from ..contracts.workspace import (
    WorkspaceEntry,
    WorkspaceEntryKind,
    WorkspaceLayout,
)
from .tools import ToolExecutionError

if TYPE_CHECKING:
    from .tools import ToolRegistry


class WorkspaceError(Exception):
    """Raised when a workspace operation is not permitted or fails."""


class Workspace:
    """A local workspace rooted at a directory with an explicit allowlist.

    Attributes:
        root: The absolute workspace root directory.
        layout: The allowlist of relative paths an agent may access.
    """

    def __init__(self, root: str | Path, layout: WorkspaceLayout) -> None:
        self._root = Path(root).resolve()
        self._layout = layout

    @property
    def root(self) -> Path:
        return self._root

    @property
    def layout(self) -> WorkspaceLayout:
        return self._layout

    def _resolve(self, relative_path: str) -> Path:
        """Resolve ``relative_path`` against the root, rejecting traversal.

        Raises:
            WorkspaceError: If the path is not a valid relative path or escapes
                the workspace root.
        """
        if not isinstance(relative_path, str) or not relative_path:
            raise WorkspaceError("A relative path is required.")
        candidate = (self._root / relative_path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise WorkspaceError(f"Path {relative_path!r} escapes the workspace root.")
        return candidate

    def _require_allowed(self, relative_path: str) -> None:
        """Reject a path that is neither an allowed file nor under a prefix."""
        if not self._layout.allows_path(relative_path):
            raise WorkspaceError(
                f"Path {relative_path!r} is not on the workspace allowlist."
            )

    # -- allowlisted, mediated file tools --------------------------------------

    def list_workspace(self) -> dict[str, str]:
        """List the allowlisted entries that exist, as a deterministic mapping.

        Returns a mapping of ``relative_path -> kind`` for every allowlisted
        file and directory that currently exists under the root. Missing
        entries are omitted (they may be created later by a write tool).
        """
        result: dict[str, str] = {}
        for rel in self._layout.files:
            path = self._resolve(rel)
            if path.is_file():
                result[rel] = WorkspaceEntryKind.FILE.value
        for rel in self._layout.directories:
            path = self._resolve(rel)
            if path.is_dir():
                result[rel] = WorkspaceEntryKind.DIRECTORY.value
        return result

    def read_workspace_file(self, relative_path: str) -> WorkspaceEntry:
        """Read an allowed file (exact allowlist or under a prefix), rejecting others.

        Raises:
            WorkspaceError: If the path is not allowed, is not a file, or cannot
                be read.
        """
        self._require_allowed(relative_path)
        path = self._resolve(relative_path)
        if not path.is_file():
            raise WorkspaceError(f"Workspace file {relative_path!r} does not exist.")
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(
                f"Could not read workspace file {relative_path!r}: {exc}"
            ) from exc
        return WorkspaceEntry(
            relative_path=relative_path,
            kind=WorkspaceEntryKind.FILE,
            content=content,
        )

    def list_prefix(self, prefix: str) -> dict[str, str]:
        """List the files under an allowlisted directory prefix.

        Returns a mapping of ``relative_path -> kind`` for every file directly
        under ``prefix`` that exists. Only the allowlisted prefix is scanned;
        anything else is rejected (fail-closed).

        Raises:
            WorkspaceError: If ``prefix`` is not on the prefix allowlist.
        """
        if prefix not in self._layout.prefixes:
            raise WorkspaceError(f"Prefix {prefix!r} is not on the workspace allowlist.")
        base = self._resolve(prefix)
        if not base.is_dir():
            return {}
        result: dict[str, str] = {}
        for child in base.iterdir():
            if child.is_file():
                rel = f"{prefix}{child.name}"
                result[rel] = WorkspaceEntryKind.FILE.value
        return result

    def write_workspace_file(self, relative_path: str, content: str) -> WorkspaceEntry:
        """Write an allowlisted file, rejecting any other path.

        The file's parent directory must already exist (it must be created via
        ``create_workspace_dir`` first). This keeps writes explicit and
        fail-closed — no implicit directory creation.

        Raises:
            WorkspaceError: If the path is not allowed, its parent directory
                does not exist, or the write fails.
        """
        self._require_allowed(relative_path)
        path = self._resolve(relative_path)
        if not path.parent.is_dir():
            raise WorkspaceError(
                f"Parent directory of {relative_path!r} does not exist; "
                "create it with create_workspace_dir first."
            )
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(
                f"Could not write workspace file {relative_path!r}: {exc}"
            ) from exc
        return WorkspaceEntry(
            relative_path=relative_path,
            kind=WorkspaceEntryKind.FILE,
            content=content,
        )

    def create_workspace_dir(self, relative_path: str) -> WorkspaceEntry:
        """Create an allowlisted directory, rejecting any other path.

        Raises:
            WorkspaceError: If the path is not on the directory allowlist or
                cannot be created.
        """
        if not self._layout.allows_directory(relative_path):
            raise WorkspaceError(
                f"Path {relative_path!r} is not on the workspace directory allowlist."
            )
        path = self._resolve(relative_path)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(
                f"Could not create workspace dir {relative_path!r}: {exc}"
            ) from exc
        return WorkspaceEntry(
            relative_path=relative_path,
            kind=WorkspaceEntryKind.DIRECTORY,
            content=None,
        )


def _workspace_entry_to_mapping(entry: WorkspaceEntry) -> dict[str, object]:
    return entry.as_mapping()


def make_workspace_tools(workspace: Workspace) -> dict[str, Callable[..., Any]]:
    """Return the mediated workspace tool implementations bound to ``workspace``.

    Each tool is a callable ``(**args) -> mapping`` suitable for registration in
    the ``ToolRegistry``. Every tool resolves its path against the workspace
    allowlist and raises ``ToolExecutionError`` (via ``WorkspaceError``) on any
    disallowed or failed operation, so a disallowed path is always an explicit,
    audited, fail-closed failure.
    """

    def _guard(fn: Callable[..., Any]) -> Callable[..., Any]:
        def _wrapped(**args: Any) -> Any:
            try:
                return fn(**args)
            except WorkspaceError as exc:
                raise ToolExecutionError(str(exc)) from exc

        return _wrapped

    def _list() -> dict[str, str]:
        return workspace.list_workspace()

    def _read(relative_path: str) -> dict[str, object]:
        return _workspace_entry_to_mapping(workspace.read_workspace_file(relative_path))

    def _write(relative_path: str, content: str) -> dict[str, object]:
        return _workspace_entry_to_mapping(
            workspace.write_workspace_file(relative_path, content)
        )

    def _mkdir(relative_path: str) -> dict[str, object]:
        return _workspace_entry_to_mapping(workspace.create_workspace_dir(relative_path))

    return {
        "list_workspace": _guard(_list),
        "read_workspace_file": _guard(_read),
        "write_workspace_file": _guard(_write),
        "create_workspace_dir": _guard(_mkdir),
    }


def _workspace_descriptors() -> tuple[ToolDescriptor, ...]:
    """Return the ToolDescriptors for the workspace tools.

    Imported lazily to avoid a circular import at module load (the descriptors
    live in ``control_plane.tools``).
    """
    from .tools import (
        CREATE_WORKSPACE_DIR_DESCRIPTOR,
        LIST_WORKSPACE_DESCRIPTOR,
        READ_WORKSPACE_FILE_DESCRIPTOR,
        WRITE_WORKSPACE_FILE_DESCRIPTOR,
    )

    return (
        LIST_WORKSPACE_DESCRIPTOR,
        READ_WORKSPACE_FILE_DESCRIPTOR,
        WRITE_WORKSPACE_FILE_DESCRIPTOR,
        CREATE_WORKSPACE_DIR_DESCRIPTOR,
    )


def register_workspace_tools(
    registry: ToolRegistry, workspace: Workspace
) -> tuple[str, ...]:
    """Register the workspace tools on a ``ToolRegistry``.

    This is the explicit, opt-in wiring point: the caller (e.g. the CLI or an
    operator) supplies a ``Workspace`` and the tools are added to the registry
    exactly like local tools, subject to the same grant, policy, envelope,
    recording, and verification paths.

    Returns the names of the tools that were registered.
    """
    impls = make_workspace_tools(workspace)
    for descriptor in _workspace_descriptors():
        registry.register_impl(descriptor, impls[descriptor.name])
    return tuple(impls.keys())