"""PDF-as-source + pasted-text grounding.

Covers the discipline the grounding gate demands: a real text PDF extracts and
grounds; an image-only/scanned PDF is REFUSED honestly; a spoofed non-PDF is
rejected on magic bytes; the size cap holds; pasted text grounds.
"""
from pathlib import Path

import pytest

from app.services import operator_service as osvc
from app.services import pdf_service
from app.services.operator_context import OperatorContext


# --------------------------------------------------------------------------
# Minimal valid PDFs (correct xref offsets so pypdf parses them)
# --------------------------------------------------------------------------

def _make_pdf(content_stream: bytes) -> bytes:
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content_stream)).encode() + b">>\nstream\n"
        + content_stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n" + f"0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<</Size " + str(len(objs) + 1).encode()
            + b"/Root 1 0 R>>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF")
    return bytes(out)


def _text_pdf(text="Gold membership is $500 a year and includes ribbon cuttings.") -> bytes:
    cs = b"BT /F1 18 Tf 72 700 Td (" + text.encode() + b") Tj ET"
    return _make_pdf(cs)


def _image_only_pdf() -> bytes:
    # A page whose content stream draws no text (no BT/Tj) -> extract_text == "".
    return _make_pdf(b"q 1 0 0 1 0 0 cm Q")


def _ctx(tmp_path, record=None):
    async def _emit(_ev):
        return None
    return OperatorContext(folder=Path(tmp_path), record=record or {}, emit=_emit)


# --------------------------------------------------------------------------
# pdf_service.validate_and_extract
# --------------------------------------------------------------------------

def test_valid_pdf_extracts_text():
    out = pdf_service.validate_and_extract(_text_pdf(), "benefits.pdf")
    assert "Gold membership is $500" in out["text"]
    assert out["pages"] == 1
    assert out["chars"] >= pdf_service.MIN_TEXT_CHARS


def test_image_only_pdf_refuses():
    with pytest.raises(pdf_service.PdfError) as ei:
        pdf_service.validate_and_extract(_image_only_pdf(), "scan.pdf")
    assert ei.value.reason == "no_text"       # honest failure, no garbage


def test_spoofed_non_pdf_rejected():
    # A ZIP/docx renamed to .pdf — real content doesn't start with %PDF-.
    with pytest.raises(pdf_service.PdfError) as ei:
        pdf_service.validate_and_extract(b"PK\x03\x04 not a pdf at all", "fake.pdf")
    assert ei.value.reason == "not_pdf"


def test_size_cap_enforced(monkeypatch):
    monkeypatch.setattr(pdf_service, "MAX_PDF_BYTES", 100)
    big = b"%PDF-1.4\n" + b"x" * 500
    with pytest.raises(pdf_service.PdfError) as ei:
        pdf_service.validate_and_extract(big, "big.pdf")
    assert ei.value.reason == "too_large"


def test_empty_upload_rejected():
    with pytest.raises(pdf_service.PdfError) as ei:
        pdf_service.validate_and_extract(b"", "empty.pdf")
    assert ei.value.reason == "empty"


# --------------------------------------------------------------------------
# grounding: pasted text + staged source both write source.md + grounding_ok
# --------------------------------------------------------------------------

def test_ground_with_text_writes_source_and_flags(tmp_path):
    op = _ctx(tmp_path, {})
    osvc._ground_with_text(op, "Real chamber benefits text, long enough.", "Attached PDF: x.pdf")
    assert op.record.get("grounding_ok") is True
    src = (Path(tmp_path) / "source.md").read_text(encoding="utf-8")
    assert "Attached PDF: x.pdf" in src
    assert "Real chamber benefits text" in src


def test_stage_and_consume_grounds_run(tmp_path):
    osvc.clear_staged_source()
    extracted = pdf_service.validate_and_extract(_text_pdf(), "benefits.pdf")
    osvc.stage_source(extracted["text"], "Attached PDF: benefits.pdf")
    assert osvc.staged_source()["chars"] > 0

    op = _ctx(tmp_path, {"source_locked_url": ""})
    note = osvc._consume_staged_source(op)

    assert op.record.get("grounding_ok") is True                 # run is grounded
    assert op.record.get("source_locked_url", "").startswith("attached:")  # and locked
    assert "Gold membership is $500" in note                     # text fed to the planner
    assert (Path(tmp_path) / "source.md").is_file()
    assert osvc.staged_source() is None                          # staged slot cleared


def test_stage_source_rejects_thin_text():
    osvc.clear_staged_source()
    with pytest.raises(ValueError):
        osvc.stage_source("too short", "x")
