"""Extract text from an uploaded PDF — honest failure on image-only scans.

Same discipline as the grounding gate: a PDF grounds a run ONLY if it yields
real extractable text. An image-only / scanned PDF (no text layer) FAILS
honestly (``reason="no_text"``) so the run refuses-and-asks rather than building
ungrounded. We do NOT OCR — no text means no grounding, never garbage.

Validation is provenance-grade: the bytes must actually START with the ``%PDF-``
header (a renamed .docx or an image won't pass), and there's a hard size cap.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger("ridian.pdf")

MAX_PDF_BYTES = 25 * 1024 * 1024   # 25 MB upload cap
MIN_TEXT_CHARS = 40                # below this ⇒ treat as image-only / empty
MAX_TEXT_CHARS = 40_000            # cap on the text handed to the model


class PdfError(Exception):
    """Renderer-safe PDF failure. ``reason`` is a stable machine code
    (not_pdf | too_large | empty | no_text | unreadable)."""

    def __init__(self, detail: str, *, reason: str = "invalid", status: int = 400):
        self.detail = detail
        self.reason = reason
        self.status = status
        super().__init__(detail)


def validate_and_extract(data: bytes, filename: str = "upload.pdf") -> dict:
    """Validate + extract text from PDF ``data``.

    Returns ``{"text", "pages", "chars", "truncated"}`` on success. Raises
    ``PdfError`` with a machine ``reason`` on any failure — including an
    image-only/scanned PDF that yields no extractable text.
    """
    if not data:
        raise PdfError("The uploaded file is empty.", reason="empty")
    if len(data) > MAX_PDF_BYTES:
        raise PdfError(
            f"That PDF is too large ({len(data) // (1024 * 1024)} MB; the limit is "
            f"{MAX_PDF_BYTES // (1024 * 1024)} MB).",
            reason="too_large",
        )

    # Magic bytes — a real PDF starts with "%PDF-". This rejects a file that is
    # merely NAMED .pdf (a renamed .docx, an image, a zip) — extension spoofing.
    head = data[:1024].lstrip(b"\x00 \t\r\n")
    if not head.startswith(b"%PDF-"):
        raise PdfError(
            "That file isn't a PDF (it's missing the %PDF header). Check the file "
            "and try again.",
            reason="not_pdf",
        )

    try:
        import pypdf  # noqa: PLC0415 — optional-at-import, real dependency
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = len(reader.pages)
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — one bad page shouldn't kill the rest
                continue
        text = "\n\n".join(p.strip() for p in parts if p and p.strip()).strip()
    except PdfError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PdfError(
            f"Couldn't read that PDF ({type(exc).__name__}) — it may be corrupt or "
            "password-protected.",
            reason="unreadable",
        ) from exc

    if len(text) < MIN_TEXT_CHARS:
        raise PdfError(
            "This PDF has no extractable text — it looks image-only or scanned. "
            "I won't guess at its contents. Paste the text instead, or provide a "
            "text-based PDF.",
            reason="no_text",
        )

    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS].rstrip() + "\n\n[... truncated ...]"
    log.info("pdf.extracted file=%s pages=%d chars=%d", filename, pages, len(text))
    return {"text": text, "pages": pages, "chars": len(text), "truncated": truncated}
