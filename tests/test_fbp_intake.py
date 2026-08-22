"""Tests for structured FBP intake (unverified drafts from json/csv/txt/pdf)."""

from __future__ import annotations

import zlib

import pytest

from agent_centric.fbp import BillsError, draft_from_file


def make_pdf(text: str, *, compress: bool = True) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    if compress:
        stream = zlib.compress(stream)
    body = b"stream\n" + stream + b"\nendstream"
    header = b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\n"
    return header + body + b"\nendobj\n%%EOF"


class TestDraftFromFile:
    def test_json(self) -> None:
        d = draft_from_file(
            '{"vendor": "GasCo", "amount_cents": 12345, "due_date": "2026-10-01"}',
            source_path="inbox/gasco.json",
        )
        assert d["id"] == "inbox/gasco.json"
        assert d["vendor"] == "GasCo"
        assert d["amount_cents"] == 12345
        assert d["due_date"] == "2026-10-01"
        assert "status" not in d  # unverified

    def test_csv(self) -> None:
        d = draft_from_file(
            "vendor,amount_cents,due_date\nGasCo,12345,2026-10-01\n",
            source_path="inbox/gasco.csv",
        )
        assert d["vendor"] == "GasCo"
        assert d["amount_cents"] == 12345
        assert d["due_date"] == "2026-10-01"

    def test_txt(self) -> None:
        d = draft_from_file(
            "vendor: PostCo\namount_cents: 999\ndue_date: 2026-09-15\n",
            source_path="inbox/postco.txt",
        )
        assert d["vendor"] == "PostCo"
        assert d["amount_cents"] == 999
        assert d["due_date"] == "2026-09-15"

    def test_pdf(self) -> None:
        d = draft_from_file(
            make_pdf("Total: 123.45 vendor: GasCo due date: 2026-10-01"),
            source_path="inbox/gasco.pdf",
        )
        assert d["vendor"] == "GasCo"
        assert d["amount_cents"] == 12345  # "123.45" -> 12345 cents
        assert d["due_date"] == "2026-10-01"

    def test_unsupported_suffix_fails_closed(self) -> None:
        with pytest.raises(BillsError, match="unsupported"):
            draft_from_file("x", source_path="inbox/a.docx")

    def test_incomplete_fails_closed(self) -> None:
        with pytest.raises(BillsError):
            draft_from_file('{"vendor": "GasCo"}', source_path="inbox/a.json")