"""Deterministic source-lock grounding gate.

The gate must hold regardless of what the planner LLM does, so these tests hit
the pure detection + gate logic directly (no agent, no network).
"""
import asyncio
from pathlib import Path

import pytest

from app.services.operator_context import OperatorContext
from app.services.operator_tools import _grounding_gate, detect_source_lock


# --------------------------------------------------------------------------
# detect_source_lock
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cmd,expected", [
    ("Read mygulfcoastchamber.com/membership and use only what's on those pages, "
     "then build a deck", "https://mygulfcoastchamber.com/membership"),
    ("Build a doc using https://example.com/x as the source", "https://example.com/x"),
    ("read the page at gulfchamber.org/join and only use that",
     "https://gulfchamber.org/join"),
])
def test_detect_source_lock_locks(cmd, expected):
    assert detect_source_lock(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "Build a competitor spreadsheet for Gulf Coast AI consultants",
    "Draft an email to jane@acme.com about the news",   # domain but no grounding intent
    "Make a slide deck about our services",
    "",
])
def test_detect_source_lock_does_not_lock(cmd):
    assert detect_source_lock(cmd) == ""


# --------------------------------------------------------------------------
# _grounding_gate
# --------------------------------------------------------------------------

def _ctx(tmp_path, record):
    async def _emit(_ev):   # noop SSE sink
        return None
    record.setdefault("steps", [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def test_gate_blocks_locked_ungrounded(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y"})
    res = asyncio.run(_grounding_gate(op))
    assert res is not None
    assert res["reason"] == "grounding_required"
    assert op.record["grounding_needs_input_emitted"] is True
    assert len(op.record.get("needs_input", [])) == 1
    steps = {s["name"]: s for s in op.record["steps"]}
    assert steps["grounding_gate"]["status"] == "skipped"   # grey, not red


def test_gate_dedupes_needs_input(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y"})
    asyncio.run(_grounding_gate(op))
    asyncio.run(_grounding_gate(op))   # a second build-tool call in the same run
    assert len(op.record.get("needs_input", [])) == 1   # only one card


def test_gate_allows_when_grounded(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y", "grounding_ok": True})
    assert asyncio.run(_grounding_gate(op)) is None


def test_gate_allows_when_override(tmp_path):
    # Resume path: operator authorized general research → lock lifted for this run.
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y", "grounding_override": True})
    assert asyncio.run(_grounding_gate(op)) is None


def test_gate_emits_structured_options(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y"})
    asyncio.run(_grounding_gate(op))
    opts = op.record["needs_input"][0].get("options", [])
    assert [o.get("action") for o in opts] == ["submit", "compose", "disabled"]
    submit = next(o for o in opts if o["action"] == "submit")
    assert submit.get("value") and len(submit["value"]) < 120   # not mistaken for a paste
    compose = next(o for o in opts if o["action"] == "compose")
    assert compose.get("placeholder")   # composer reveal carries a placeholder


def test_gate_allows_when_not_locked(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": ""})
    assert asyncio.run(_grounding_gate(op)) is None
