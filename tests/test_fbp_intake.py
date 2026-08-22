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


class TestDraftFromEmail:
    def test_email_builds_unverified_draft(self) -> None:
        from agent_centric.fbp import draft_from_email

        d = draft_from_email(
            {
                "folder": "inbox",
                "id": "msg1",
                "subject": "Invoice",
                "body": "from GasCo amount 123.45 due date 2026-10-01",
            }
        )
        assert d["vendor"] == "GasCo"
        assert d["amount_cents"] == 12345
        assert d["due_date"] == "2026-10-01"
        assert "status" not in d  # unverified

    def test_email_incomplete_fails_closed(self) -> None:
        from agent_centric.fbp import draft_from_email

        with pytest.raises(BillsError):
            draft_from_email(
                {"folder": "inbox", "id": "m2", "subject": "hi", "body": "no facts"}
            )

    def test_email_missing_id_fails_closed(self) -> None:
        from agent_centric.fbp import draft_from_email

        with pytest.raises(BillsError):
            draft_from_email({"folder": "inbox", "body": "x"})


class TestBillsAgentIntake:
    """The BillsAgent serves intake tasks (file/email/PDF) producing UNVERIFIED
    drafts reachable over the protocol and recorded/replayable."""

    def _setup(self, driver, tmp_path, keys):
        from pathlib import Path

        registry = Path(tmp_path) / "registry.db"
        driver.spawn("bills", kind="bills")
        driver.run(
            "bills_setup",
            {"state": str(registry), "store_keys": list(keys)},
            child="bills",
        )
        return registry

    def test_intake_file_and_accept(self, tmp_path) -> None:
        from agent_centric.fbp import FbpDriver, store
        from agent_centric.fbp.bills_agent import TASK_ACCEPT, TASK_INTAKE_FILE

        with FbpDriver() as driver:
            self._setup(driver, tmp_path, ["inbox/gasco.txt"])
            draft = driver.run(
                TASK_INTAKE_FILE,
                {
                    "source_path": "inbox/gasco.txt",
                    "content": "vendor: GasCo\namount_cents: 12345\ndue_date: 2026-10-01\n",
                },
                child="bills",
            )
            assert draft.verified is True
            assert draft.value["vendor"] == "GasCo"
            assert "status" not in draft.value  # unverified

            # Human-gated accept -> registry.
            accepted = driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            assert accepted.verified is True

        st = store.open_state(tmp_path / "registry.db")
        assert st.get("inbox/gasco.txt")["status"] == "open"
        st.close()

    def test_intake_email_and_accept(self, tmp_path) -> None:
        from agent_centric.fbp import FbpDriver, store
        from agent_centric.fbp.bills_agent import TASK_ACCEPT, TASK_INTAKE_EMAIL

        with FbpDriver() as driver:
            self._setup(driver, tmp_path, ["m1:2026-10-01"])
            draft = driver.run(
                TASK_INTAKE_EMAIL,
                {
                    "message": {
                        "folder": "inbox",
                        "id": "m1",
                        "subject": "Invoice",
                        "body": "from GasCo amount 123.45 due date 2026-10-01",
                    }
                },
                child="bills",
            )
            assert draft.verified is True
            assert draft.value["amount_cents"] == 12345
            accepted = driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            assert accepted.verified is True

        st = store.open_state(tmp_path / "registry.db")
        assert st.get("m1:2026-10-01")["status"] == "open"
        st.close()

    def test_intake_pdf_and_accept(self, tmp_path) -> None:
        import base64

        from agent_centric.fbp import FbpDriver, store
        from agent_centric.fbp.bills_agent import TASK_ACCEPT, TASK_INTAKE_PDF

        with FbpDriver() as driver:
            self._setup(driver, tmp_path, ["inbox/gasco.pdf"])
            draft = driver.run(
                TASK_INTAKE_PDF,
                {
                    "source_path": "inbox/gasco.pdf",
                    "pdf_b64": base64.b64encode(
                        make_pdf("Total: 123.45 vendor: GasCo due date: 2026-10-01")
                    ).decode("ascii"),
                },
                child="bills",
            )
            assert draft.verified is True
            assert draft.value["vendor"] == "GasCo"
            accepted = driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            assert accepted.verified is True

        st = store.open_state(tmp_path / "registry.db")
        assert st.get("inbox/gasco.pdf")["status"] == "open"
        st.close()

    def test_intake_calendar_replayable(self, tmp_path) -> None:
        from agent_centric.fbp import FbpDriver
        from agent_centric.fbp.bills_agent import TASK_ACCEPT, TASK_INTAKE_FILE

        ledger_path = tmp_path / "ledger.db"
        with FbpDriver(ledger_path=str(ledger_path)) as driver:
            self._setup(driver, tmp_path, ["b1"])
            draft = driver.run(
                TASK_INTAKE_FILE,
                {
                    "source_path": "b1",
                    "content": "vendor: NetCo\namount_cents: 3000\ndue_date: 2026-09-01\n",
                },
                child="bills",
            )
            driver.run(TASK_ACCEPT, {"draft": draft.value}, child="bills")
            result = driver.replay_session()
            assert result["ok"] is True, result["failed"]