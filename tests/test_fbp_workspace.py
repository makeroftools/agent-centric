"""Tests for the allowlisted FBP workspace capability (port of main's hardened
workspace: fail-closed path allowlist, no traversal, no deletion)."""

from __future__ import annotations

import pytest

from agent_centric.fbp import (
    FILE,
    WorkspaceError,
    WorkspaceFS,
    WorkspaceLayout,
)


class TestWorkspaceFS:
    def _ws(self, tmp_path):
        ws = WorkspaceFS(
            tmp_path,
            WorkspaceLayout(
                files=("bills/registry.json", "notes.txt"),
                directories=("bills", "inbox"),
                prefixes=("inbox/",),
            ),
        )
        ws.create_dir("bills")
        ws.create_dir("inbox")
        return ws

    def test_write_read_roundtrip(self, tmp_path) -> None:
        ws = self._ws(tmp_path)
        ws.write_text("bills/registry.json", '{"bills": []}')
        entry = ws.read_text("bills/registry.json")
        assert entry.kind == FILE
        assert entry.content == '{"bills": []}'
        assert "bills/registry.json" in ws.list_workspace()

    def test_prefix_list(self, tmp_path) -> None:
        ws = self._ws(tmp_path)
        ws.write_text("inbox/a.txt", "a")
        ws.write_text("inbox/b.txt", "b")
        listing = ws.list_prefix("inbox/")
        assert listing == {"inbox/a.txt": FILE, "inbox/b.txt": FILE}

    def test_disallowed_path_fails_closed(self, tmp_path) -> None:
        ws = self._ws(tmp_path)
        with pytest.raises(WorkspaceError, match="allowlist"):
            ws.write_text("secret.txt", "x")

    def test_traversal_fails_closed(self, tmp_path) -> None:
        ws = self._ws(tmp_path)
        with pytest.raises(WorkspaceError, match="escape"):
            ws.read_text("../outside.txt")

    def test_write_requires_existing_parent(self, tmp_path) -> None:
        ws = WorkspaceFS(tmp_path, WorkspaceLayout(files=("deep/file.txt",)))
        with pytest.raises(WorkspaceError, match="Parent directory"):
            ws.write_text("deep/file.txt", "x")

    def test_no_delete(self, tmp_path) -> None:
        ws = self._ws(tmp_path)
        ws.write_text("notes.txt", "hello")
        assert not hasattr(ws, "delete")
        # The allowlist has no delete operation; only read/write/list/create.
        assert ws.read_text("notes.txt").content == "hello"

    def test_read_missing_fails_closed(self, tmp_path) -> None:
        ws = self._ws(tmp_path)
        with pytest.raises(WorkspaceError, match="does not exist"):
            ws.read_text("notes.txt")