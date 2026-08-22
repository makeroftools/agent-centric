"""PDF intake for the FBP subsystem: dependency-free embedded-text extraction.

This is a port of ``main``'s deterministic, offline ``pdf_text`` capability into
 the FBP model as a pure **capability** (a registered callable, not an agent — it
is a read-only observation with no responsibility, no children, and no state).

The embedded-text extraction itself is **shared** with ``main``'s
``control_plane.pdf_text`` (one implementation, no divergence); this module adds
the FBP-specific ``draft_from_pdf_text`` that turns extracted text into an
**unverified** bill draft.

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
from typing import Any

from ..control_plane.pdf_text import extract_text
from .bills import BillsError, draft_from_intake

# Re-export the shared extractor so FBP consumers (intake, the package surface)
# keep importing it from this module, as before the deduplication.
__all__ = ["extract_text", "draft_from_pdf_text"]


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