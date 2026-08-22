"""PDF intake for the FBP subsystem: dependency-free embedded-text extraction.

This is a port of ``main``'s deterministic, offline ``pdf_text`` capability into
the FBP model as a pure **capability** (a registered callable, not an agent — it
is a read-only observation with no responsibility, no children, and no state).

It extracts plain text from *simple* PDFs that embed their text as content
streams using ``Tj`` / ``TJ`` text-showing operators, with an optional Flate
(zlib) compressor. It is intentionally boring and conservative:

- Not a general PDF parser: it does not follow object streams, xref tables, or
  ``Type3``/font glyph mappings.
- If embedded text is not present or unparseable, it returns ``""`` so the
  caller can fail closed (never invent facts).
- No OCR, no image processing, no network. Offline and deterministic.

The mission-critical invariant is preserved: extracting text from a PDF yields
an **unverified draft** that must pass the human ``bills_accept`` gate before it
enters the registry — no money or dates are ever auto-accepted.
"""

from __future__ import annotations

import re
import zlib
from typing import Any

from .bills import BillsError, draft_from_intake

# The two leading bytes of a zlib header are 0x78 followed by one of a small
# set of second bytes (0x01, 0x5E, 0x9C, 0xDA).
_ZLIB_SECOND_BYTES = (0x01, 0x5E, 0x9C, 0xDA)


def _is_zlib(data: bytes) -> bool:
    return len(data) > 2 and data[0] == 0x78 and data[1] in _ZLIB_SECOND_BYTES


def _decode_literal(s: bytes) -> bytes:
    """Decode a PDF string literal body, unescaping backslash escapes.

    Handles ``\n \r \t \b \f ( ) \\`` and octal ``\\ooo`` escapes. Returns raw
    bytes (the caller decodes to text with error tolerance).
    """
    out = bytearray()
    i = 0
    n = len(s)
    escapes = {ord("n"): ord("\n"), ord("r"): ord("\r"), ord("t"): ord("\t"),
               ord("b"): ord("\b"), ord("f"): ord("\f"),
               ord("("): ord("("), ord(")"): ord(")"), ord("\\"): ord("\\")}
    while i < n:
        b = s[i]
        if b == ord("\\") and i + 1 < n:
            nxt = s[i + 1]
            if nxt in escapes:
                out.append(escapes[nxt])
                i += 2
            elif 0o60 <= nxt <= 0o77:  # octal
                digits = bytes(s[i + 1: i + 4])
                if len(digits) == 3 and all(0o60 <= c <= 0o77 for c in digits):
                    out.append(int(digits, 8))
                    i += 4
                else:
                    out.append(nxt)
                    i += 2
            else:
                out.append(nxt)
                i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def _collect_stream_tokens(blob: bytes) -> list[str]:
    """Return decoded string literals found as ``(...) Tj`` / ``[...] TJ`` text."""
    tokens: list[str] = []
    i = 0
    n = len(blob)
    while i < n:
        if blob[i] != ord("("):
            i += 1
            continue
        j = i + 1
        depth = 1
        buf = bytearray()
        while j < n and depth > 0:
            c = blob[j]
            if c == ord("\\"):
                if j + 1 < n:
                    buf.append(c)
                    buf.append(blob[j + 1])
                    j += 2
                    continue
                buf.append(c)
                j += 1
                continue
            if c == ord("("):
                depth += 1
            elif c == ord(")"):
                depth -= 1
                if depth == 0:
                    break
            buf.append(c)
            j += 1
        decoded = _decode_literal(bytes(buf))
        try:
            tokens.append(decoded.decode("latin-1"))
        except UnicodeDecodeError:  # pragma: no cover - very unusual
            tokens.append(decoded.decode("latin-1", errors="replace"))
        i = j + 1 if depth == 0 else n
    return tokens


def extract_text(pdf: bytes) -> str:
    """Return embedded plain text from a simple PDF document.

    Returns ``""`` if no parseable embedded text is found (fail closed).
    """
    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.DOTALL):
        raw = match.group(1)
        blob = raw
        if _is_zlib(raw):
            try:
                blob = zlib.decompress(raw)
            except zlib.error:
                blob = raw
        chunks.extend(_collect_stream_tokens(blob))
    text = " ".join(chunks)
    return re.sub(r"\s+", " ", text).strip()


def draft_from_pdf_text(
    pdf: bytes, *, source_path: str = ""
) -> dict[str, Any]:
    """Turn embedded PDF text into an **unverified** bill draft (fail-closed).

    This is the FBP analog of ``main``'s PDF intake: it extracts embedded text,
    locates the first vendor / amount_cents / due_date ``key: value`` lines, and
    builds a draft via ``draft_from_intake``. The result is always **unverified**
    — it must pass the human's ``bills_accept`` gate to enter the registry.

    Args:
        pdf: The PDF bytes.
        source_path: A stable, deterministic source id (e.g. the file path).

    Raises:
        BillsError: If no usable embedded text/fields are found (nothing is
            invented from a PDF with no parseable content).
    """
    text = extract_text(pdf)
    fields: dict[str, Any] = {"id": source_path or "pdf-draft"}
    # Scan for ``key: value`` pairs anywhere in the extracted text (word-boundary
    # anchored), which handles both multi-line and single-line PDF content.
    patterns = {
        "vendor": r"vendor\s*:\s*([^\s]+)",
        "amount_cents": r"amount_cents\s*:\s*(-?\d+)",
        "due_date": r"due_date\s*:\s*(\d{4}-\d{2}-\d{2})",
    }
    for field, pattern in patterns.items():
        if field in fields:
            continue
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if not m:
            continue
        value = m.group(1)
        parsed: Any = int(value) if field == "amount_cents" else value
        if parsed not in (None, ""):
            fields[field] = parsed
    if "vendor" not in fields or "amount_cents" not in fields or "due_date" not in fields:
        raise BillsError(
            f"pdf draft {source_path or '?'!r}: insufficient embedded text "
            "(fail closed - no draft invented)"
        )
    return draft_from_intake(fields)