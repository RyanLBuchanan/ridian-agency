"""Deliverable-intent gate — conversational input never triggers document
production, and a staged source never glues itself to small talk."""
from pathlib import Path

import pytest

from app.services import operator_service as osvc
from app.services.operator_context import OperatorContext
from app.services.operator_tools import _deliverable_gate, detect_deliverable_intent


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    record.setdefault("steps", [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


# --------------------------------------------------------------------------
# detect_deliverable_intent
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "How do you feel?",
    "hey",
    "what do you think about the Gulf Coast market?",
    "who do I have at the Chamber?",
    "",
])
def test_conversational_input_has_no_intent(cmd):
    assert detect_deliverable_intent(cmd) is False


@pytest.mark.parametrize("cmd", [
    "Build a competitor spreadsheet for Gulf Coast AI consultants",
    "Draft check-in emails to everyone I owe a reply",
    "research the newest agentic AI frameworks this week",
    "put together a one-pager on chamber membership",
    "make it a deck",
    "turn that brief into slides",
    "yes, build the deck",
    "Using only the attached PDF, write a benefits document",
])
def test_deliverable_requests_have_intent(cmd):
    assert detect_deliverable_intent(cmd) is True


# --------------------------------------------------------------------------
# _deliverable_gate
# --------------------------------------------------------------------------

def test_gate_blocks_conversational_run(tmp_path):
    op = _ctx(tmp_path, {"deliverable_intent": False})
    res = _deliverable_gate(op)
    assert res is not None and res["reason"] == "no_deliverable_request"


def test_gate_allows_deliverable_run(tmp_path):
    op = _ctx(tmp_path, {"deliverable_intent": True})
    assert _deliverable_gate(op) is None


def test_gate_defaults_open_when_key_absent(tmp_path):
    # Legacy/edge records without the flag must not break existing behavior.
    op = _ctx(tmp_path, {})
    assert _deliverable_gate(op) is None


# --------------------------------------------------------------------------
# staged source held (not consumed) on a conversational run
# --------------------------------------------------------------------------

def test_staged_source_held_without_intent(tmp_path):
    osvc.clear_staged_source()
    osvc.stage_source("Harborview benefits: Gold tier is $500/yr with ribbon cuttings.",
                      "Attached PDF: harborview.pdf")

    chat = _ctx(tmp_path, {"deliverable_intent": False})
    note = osvc._consume_staged_source(chat)
    assert note == ""                                   # nothing attached to small talk
    assert chat.record.get("grounding_ok") is None      # not grounded
    assert osvc.staged_source() is not None             # STILL staged for the next build

    build = _ctx(tmp_path, {"deliverable_intent": True})
    note2 = osvc._consume_staged_source(build)
    assert "Harborview" in note2                        # consumed by the real build
    assert build.record.get("grounding_ok") is True
    assert osvc.staged_source() is None
    osvc.clear_staged_source()
