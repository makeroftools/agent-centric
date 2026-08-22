"""Structured intake for the FBP subsystem: unverified bill drafts.

This is a port of ``main``'s deterministic, offline intake heuristics into the
FBP model as pure **capabilities** (registered callables, read-only — no state,
no children, no responsibility beyond their own verification).

Supported sources all produce an **unverified** bill draft that must pass the
human ``bills_accept`` gate before entering the registry:

- ``.json`` — a mapping ``{vendor, amount_cents, due_date, ...}``.
- ``.csv`` — a single data row (first non-empty row) with those columns.
- ``.txt`` — ``key: value`` lines (vendor / amount_cents / due_date).
- ``.pdf`` — embedded text via :mod:`.pdf_intake`.

A malformed or incomplete source fails closed (``BillsError``) — nothing is
invented, no money or dates auto-enter the registry.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from .bills import BillsError, draft_from_intake
from .pdf_intake import extract_text as extract_pdf_text

_SUPPORTED_SUFFIXES = (".json", ".csv", ".txt", ".pdf")


def _draft(
    raw: dict[str, Any], *, source_path: str
) -> dict[str, Any]:
    raw = dict(raw)
    raw.setdefault("id", source_path)
    # Coerce string values from CSV/TXT into native types so draft_from_intake
    # validates correctly (money is integer cents; a bad amount fails closed).
    if isinstance(raw.get("amount_cents"), str) and raw["amount_cents"].strip():
        try:
            raw["amount_cents"] = int(raw["amount_cents"])
        except ValueError:
            raw["amount_cents"] = None
    for key in ("vendor", "due_date"):
        if isinstance(raw.get(key), str):
            raw[key] = raw[key].strip()
    return draft_from_intake(raw)


def _extract_from_json(source_path: str, content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BillsError(f"{source_path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise BillsError(f"{source_path}: JSON must be an object")
    return _draft(data, source_path=source_path)


def _extract_from_csv(source_path: str, content: str) -> dict[str, Any]:
    try:
        rows = list(csv.DictReader(io.StringIO(content)))
    except Exception as exc:  # noqa: BLE001 - mapped below
        raise BillsError(f"{source_path}: could not parse CSV ({exc})") from exc
    if not rows:
        raise BillsError(f"{source_path}: CSV has no data rows")
    row = rows[0]
    raw: dict[str, Any] = {}
    for key, value in row.items():
        k = (key or "").strip()
        if not k:
            continue
        raw[k] = value.strip() if isinstance(value, str) else value
    return _draft(raw, source_path=source_path)


def _extract_from_txt(source_path: str, content: str) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for line in content.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        raw[key.strip().lower()] = value.strip()
    return _draft(raw, source_path=source_path)


_AMOUNT_RE = re.compile(
    r"\b(?:total|amount)\s*[:$]?\s*\$?([0-9]+(?:\.[0-9]{1,2})?)\b",
    re.IGNORECASE,
)
_DUE_RE = re.compile(
    r"\b(?:due date|due|pay by)\s*[:]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\b",
    re.IGNORECASE,
)


def _parse_money(value: str) -> int | None:
    v = value.strip()
    if v.count(".") == 1:
        whole, _, frac = v.partition(".")
        if frac and len(frac) <= 2 and whole.isdigit() and frac.isdigit():
            return int(whole) * 100 + int(frac.ljust(2, "0"))
        return None
    if v.isdigit():
        return int(v)
    return None


def _extract_from_pdf(source_path: str, content: bytes) -> dict[str, Any]:
    text = extract_pdf_text(content)
    vendor = ""
    amount_cents: int | None = None
    due_date = ""
    if text:
        m = _AMOUNT_RE.search(text)
        if m:
            amount_cents = _parse_money(m.group(1))
        dm = _DUE_RE.search(text)
        if dm:
            due_date = dm.group(1)
        vm = re.search(
            r"\b(vendor|from|billed to)\s*[:\-]?\s*"
            r"([A-Za-z0-9&.'\-]+(?:\s+(?!total|amount|due|pay)[A-Za-z0-9&.'\-]+){0,3})",
            text,
            re.IGNORECASE,
        )
        if vm:
            vendor = vm.group(2).strip()
    if not vendor or amount_cents is None or not due_date:
        raise BillsError(
            f"{source_path}: PDF embedded text lacks a complete vendor/amount/due "
            "date (fail closed - no draft invented)"
        )
    return draft_from_intake(
        {
            "id": source_path,
            "vendor": vendor,
            "amount_cents": amount_cents,
            "due_date": due_date,
        }
    )


def draft_from_file(
    content: str | bytes, *, source_path: str
) -> dict[str, Any]:
    """Turn one intake file (by suffix) into an **unverified** draft.

    Args:
        content: The file bytes (or text for json/csv/txt).
        source_path: The file's path (its suffix selects the parser).

    Raises:
        BillsError: On an unsupported suffix or a malformed/incomplete file
            (fail closed — no draft invented).
    """
    suffix = next((s for s in _SUPPORTED_SUFFIXES if source_path.lower().endswith(s)), None)
    if suffix is None:
        raise BillsError(
            f"{source_path}: unsupported intake format "
            f"(expected {', '.join(_SUPPORTED_SUFFIXES)})"
        )
    if suffix == ".pdf":
        if not isinstance(content, bytes):
            raise BillsError(f"{source_path}: PDF intake requires bytes")
        return _extract_from_pdf(source_path, content)
    text = _as_text(content)
    if suffix == ".json":
        return _extract_from_json(source_path, text)
    if suffix == ".csv":
        return _extract_from_csv(source_path, text)
    return _extract_from_txt(source_path, text)


def _as_text(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")