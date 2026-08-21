"""Tests for Volley 027 — PDF → unverified bill drafts (fail-closed extraction).

These tests prove the PDF intake path is governed by the same mission-critical
gate as Volley 026: extracted money/schedule facts are always unverified until
an explicit human accept, and a PDF with no usable embedded text fails closed
(no draft is invented). Synthetic PDFs are built offline from :mod:`zlib` and
the dependency-free :mod:`pdf_text` extractor — no OCR, no network.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from agent_centric.contracts.bills_registry import BillsRegistry
from agent_centric.contracts.workspace import WorkspaceLayout
from agent_centric.control_plane.intake import IntakeOps, ensure_intake_layout, extract_drafts
from agent_centric.control_plane.tools import ToolExecutionError
from agent_centric.control_plane.workspace import Workspace

REGISTRY = {
    "version": "bills_registry.v1",
    "description": "existing registry",
    "bills": [
        {
            "id": "b1",
            "vendor": "NetCo",
            "amount_cents": 3000,
            "due_date": "2026-09-01",
            "status": "due",
        },
    ],
}


def make_pdf(text: str, *, compress: bool = True) -> bytes:
    """Build a minimal synthetic PDF whose content stream shows ``text``.

    The stream uses a single ``(...) Tj`` text-showing operator, optionally
    Flate-compressed — exactly the shape the dependency-free extractor handles.
    """
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    if compress:
        stream = zlib.compress(stream)
    body = b"stream\n" + stream + b"\nendstream"
    header = b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\n"
    return header + body + b"\nendobj\n%%EOF"


def _make_workspace(tmp_path: Path, inbox_files: dict[str, bytes] | None = None) -> Workspace:
    layout = ensure_intake_layout(WorkspaceLayout())
    ws = Workspace(tmp_path, layout)
    ws.create_workspace_dir("bills")
    ws.write_workspace_file("bills/registry.json", json.dumps(REGISTRY))
    ws.create_workspace_dir("inbox")
    for name, content in (inbox_files or {}).items():
        ws.write_workspace_file(f"inbox/{name}", content.decode("latin-1"))
    return ws


def _write_pdf(ws: Workspace, name: str, data: bytes) -> None:
    # write_workspace_file is text-only; write the PDF bytes directly under the
    # allowlisted inbox prefix (read back via read_bytes in the pipeline).
    target = ws.root / "inbox" / name
    target.write_bytes(data)


class TestPdfTextExtractor:
    def test_extracts_compressed_text(self) -> None:
        from agent_centric.control_plane.pdf_text import extract_text

        assert extract_text(make_pdf("Total: 123.45")) == "Total: 123.45"

    def test_extracts_raw_text(self) -> None:
        from agent_centric.control_plane.pdf_text import extract_text

        assert extract_text(make_pdf("Hello world", compress=False)) == "Hello world"

    def test_returns_empty_on_garbage(self) -> None:
        from agent_centric.control_plane.pdf_text import extract_text

        assert extract_text(b"not a pdf at all") == ""
        assert extract_text(b"") == ""


class TestPdfDraftExtraction:
    def test_pdf_with_clear_text_yields_unverified_draft(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        _write_pdf(
            ws,
            "bill.pdf",
            make_pdf("Vendor: Acme Co. Total: 123.45 Due date: 2026-09-01"),
        )
        out = extract_drafts(ws)
        assert out["count"] == 1
        assert out["unverified"] is True
        draft = out["drafts"][0]
        assert draft["unverified"] is True
        assert draft["vendor"] == "Acme Co."
        assert draft["amount_cents"] == 12345
        assert draft["due_date"] == "2026-09-01"
        assert draft["source_path"] == "inbox/bill.pdf"

    def test_pdf_with_no_usable_text_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        _write_pdf(ws, "empty.pdf", make_pdf("no structured fields here"))
        with pytest.raises(ToolExecutionError):
            extract_drafts(ws)

    def test_garbage_pdf_fails_closed(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        _write_pdf(ws, "garbage.pdf", b"this is not a pdf")
        with pytest.raises(ToolExecutionError):
            extract_drafts(ws)

    def test_pdf_amount_parses_cents(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        _write_pdf(ws, "amt.pdf", make_pdf("Vendor: GasCo Total: 99.9 Due date: 2026-10-01"))
        out = extract_drafts(ws)
        assert out["drafts"][0]["amount_cents"] == 9990


class TestPdfNoSilentCommit:
    def test_inventory_and_drafts_do_not_mutate_registry(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        _write_pdf(
            ws,
            "bill.pdf",
            make_pdf("Vendor: Acme Co. Total: 123.45 Due date: 2026-09-01"),
        )
        before = ws.read_workspace_file("bills/registry.json").content
        ops = IntakeOps(ws)
        ops.inventory()
        ops.drafts()
        after = ws.read_workspace_file("bills/registry.json").content
        assert before == after

    def test_accept_still_required_for_registry_mutation(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        _write_pdf(
            ws,
            "bill.pdf",
            make_pdf("Vendor: Acme Co. Total: 123.45 Due date: 2026-09-01"),
        )
        drafts = extract_drafts(ws)
        draft_id = drafts["drafts"][0]["draft_id"]
        # Registry unchanged before accept.
        before = json.loads(ws.read_workspace_file("bills/registry.json").content or "")
        assert len(BillsRegistry.from_mapping(before).bills) == 1
        # Explicit accept persists only the requested row.
        ops = IntakeOps(ws)
        result = ops.accept(drafts, [draft_id])
        assert result["accepted"] == [draft_id]
        after = json.loads(ws.read_workspace_file("bills/registry.json").content or "")
        merged = BillsRegistry.from_mapping(after)
        assert len(merged.bills) == 2
        assert [b.id for b in merged.bills] == ["b1", draft_id]