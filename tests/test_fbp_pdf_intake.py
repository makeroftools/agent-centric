"""Tests for the FBP PDF-intake capability (port of main's deterministic,
offline embedded-text extraction).

Extracted money/schedule facts are always **unverified** drafts that must pass
the human ``bills_accept`` gate; a PDF with no usable embedded text fails closed
(no draft invented). Synthetic PDFs are built offline from :mod:`zlib`.
"""

from __future__ import annotations

import zlib

import pytest

from agent_centric.fbp import BillsError, draft_from_pdf_text, extract_text


def make_pdf(text: str, *, compress: bool = True) -> bytes:
    """Build a minimal synthetic PDF whose content stream shows ``text``."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    if compress:
        stream = zlib.compress(stream)
    body = b"stream\n" + stream + b"\nendstream"
    header = b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\n"
    return header + body + b"\nendobj\n%%EOF"


class TestExtractText:
    def test_extracts_compressed_text(self) -> None:
        assert extract_text(make_pdf("Total: 123.45")) == "Total: 123.45"

    def test_extracts_raw_text(self) -> None:
        assert extract_text(make_pdf("Hello world", compress=False)) == "Hello world"

    def test_empty_pdf_fails_closed_empty(self) -> None:
        assert extract_text(b"not a pdf") == ""


class TestDraftFromPdfText:
    def test_builds_unverified_draft(self) -> None:
        text = (
            "Bill from GasCo\n"
            "vendor: GasCo\n"
            "amount_cents: 12345\n"
            "due_date: 2026-10-01\n"
        )
        draft = draft_from_pdf_text(make_pdf(text), source_path="inbox/bill1.pdf")
        # The draft is a normal unverified draft (no status / accept gate yet).
        assert draft["id"] == "inbox/bill1.pdf"
        assert draft["vendor"] == "GasCo"
        assert draft["amount_cents"] == 12345
        assert draft["due_date"] == "2026-10-01"
        assert "status" not in draft

    def test_accept_then_registry_via_fbp(self) -> None:
        """The extracted draft flows through the FBP human-gated accept."""
        import tempfile
        from pathlib import Path

        from agent_centric.fbp import FbpDriver, store
        from agent_centric.fbp.bills_agent import TASK_ACCEPT

        text = (
            "vendor: GasCo\namount_cents: 12345\ndue_date: 2026-10-01\n"
        )
        draft = draft_from_pdf_text(make_pdf(text), source_path="inbox/b1.pdf")
        assert draft["id"] == "inbox/b1.pdf"

        d = Path(tempfile.mkdtemp(prefix="fbp-pdf-"))
        registry = d / "registry.db"
        with FbpDriver() as driver:
            driver.spawn("bills", kind="bills")
            driver.run(
                "bills_setup",
                {"state": str(registry), "store_keys": ["inbox/b1.pdf"]},
                child="bills",
            )
            # Accept the PDF-extracted draft (human gate) -> registry.
            resp = driver.run(TASK_ACCEPT, {"draft": draft}, child="bills")
            assert resp.verified is True
        st = store.open_state(registry)
        bill = st.get("inbox/b1.pdf")
        assert bill["status"] == "open"
        assert bill["amount_cents"] == 12345
        st.close()

    def test_missing_fields_fails_closed(self) -> None:
        # No amount/date -> no draft invented.
        with pytest.raises(BillsError):
            draft_from_pdf_text(make_pdf("vendor: OnlyVendor\n"), source_path="x.pdf")

    def test_not_a_pdf_fails_closed(self) -> None:
        with pytest.raises(BillsError):
            draft_from_pdf_text(b"garbage bytes", source_path="x.pdf")