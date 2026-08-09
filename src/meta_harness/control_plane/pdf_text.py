"""Minimal, dependency-free PDF embedded-text extraction (Volley 027).

This module extracts plain text from *simple* PDFs that embed their text as
content streams using ``Tj`` / ``TJ`` text-showing operators, with an optional
Flate (zlib) compressor. It is intentionally boring and conservative:

- It is **not** a general PDF parser and does not follow object streams, xref
  tables, or ``Type3``/font glyph mappings.
- If embedded text is not present, compressed with an unsupported codec, or
  otherwise unparseable, it returns ``""`` so the caller can fail closed (an
  empty/partial ``BillDraft`` with notes) rather than invent facts.
- No OCR, no image processing, no network. Everything runs offline and is
  fixture-testable with synthetic PDFs built from :mod:`zlib`.
"""

from __future__ import annotations

import re
import zlib

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
                digits = bytes(s[i + 1 : i + 4])
                oct_digits = digits[:3]
                if len(oct_digits) == 3 and all(0o60 <= c <= 0o77 for c in oct_digits):
                    out.append(int(oct_digits, 8))
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
    """Return decoded string literals found as ``(...) Tj`` / ``[...] TJ`` text.

    We scan the (decompressed) content stream and collect every balanced,
    parenthesis-delimited string literal. For simple text PDFs these are the
    shown text chunks. If none are found we return an empty list.
    """
    tokens: list[str] = []
    i = 0
    n = len(blob)
    while i < n:
        if blob[i] != ord("("):
            i += 1
            continue
        # Read a balanced literal: track nesting and backslash escapes.
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
        # The buffer consumed the escape sequences verbatim (slashes retained)
        # so we can decode them now via _decode_literal.
        decoded = _decode_literal(bytes(buf))
        try:
            tokens.append(decoded.decode("latin-1"))
        except UnicodeDecodeError:  # pragma: no cover - very unusual
            tokens.append(decoded.decode("latin-1", errors="replace"))
        i = j + 1 if depth == 0 else n
    return tokens


def extract_text(pdf: bytes) -> str:
    """Return embedded plain text from a simple PDF document.

    Returns ``""`` if no parseable embedded text is found (fail closed). The
    result is whitespace-normalised but otherwise verbatim from the document.
    """
    chunks: list[str] = []
    # Split on stream...endstream blocks.
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.DOTALL):
        raw = match.group(1)
        blob = raw
        if _is_zlib(raw):
            try:
                blob = zlib.decompress(raw)
            except zlib.error:
                # Not a valid zlib stream after all; treat as raw.
                blob = raw
        chunks.extend(_collect_stream_tokens(blob))
    text = " ".join(chunks)
    return re.sub(r"\s+", " ", text).strip()