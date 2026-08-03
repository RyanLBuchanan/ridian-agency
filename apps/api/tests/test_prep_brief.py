"""prep_brief (v5.0 Phase 4) — the hard source gate.

Pins:
  1. the gate is PURE, DETERMINISTIC code: a factual line with no [src:]
     is stripped; a line citing a URL the search did NOT return (a
     fabricated source) is stripped identically; cited lines survive;
     structure (headers, questions, "Nothing found.") passes;
  2. zero retrieved sources = NO brief file, an honest error, full stop;
  3. the tool writes brief.md only from gate-surviving content and reports
     how many lines were stripped (honest accounting, never silent).
"""
import asyncio
import json

import pytest

from app.services import operator_tools as t
from app.services.anthropic_runtime import TextAgentResult
from app.services.operator_context import OperatorContext, set_current_operator


def _op(tmp_path):
    async def _emit(_ev):
        return None
    record = {"id": "op_prep1", "steps": [], "tools_used": [],
              "artifacts": [], "errors": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


_BRIEF = """# Prep brief: CPC Texas

## What they do
- Commercial plumbing across central Texas. [src: https://cpc-tx.com/about]
- They dominate the Austin market with 90% share. [src: https://totally-invented.example/market]
- They were founded by a former astronaut.

## Recent news
- Won a hospital contract in March. [src: https://news.example/cpc-hospital]
- Nothing found.

## Three opening questions
1. How are you handling estimating today?
2. What does scheduling look like across crews?
3. Where does paperwork pile up?
"""

_RETRIEVED = ("https://cpc-tx.com/about/", "https://news.example/cpc-hospital")


# --------------------------------------------------------------------------
# The pure gate
# --------------------------------------------------------------------------

def test_fabricated_and_uncited_claims_are_stripped_not_softened():
    gated, stripped = t._gate_brief(_BRIEF, _RETRIEVED)
    # THE required test: a fabricated claim with no source is REMOVED.
    assert "former astronaut" not in gated
    # A plausible-looking but non-retrieved URL is removed identically.
    assert "90% share" not in gated
    assert "totally-invented.example" not in gated
    # Cited claims survive verbatim (trailing-slash normalization holds).
    assert "Commercial plumbing across central Texas." in gated
    assert "Won a hospital contract in March." in gated
    # Structure passes untouched: headers, questions, honesty marker.
    assert "# Prep brief: CPC Texas" in gated
    assert "How are you handling estimating today?" in gated
    assert "- Nothing found." in gated
    assert stripped == 2


def test_gate_with_no_retrieved_urls_strips_every_claim():
    gated, stripped = t._gate_brief(_BRIEF, [])
    assert "[src:" not in gated
    assert stripped == 4          # all four fact lines — even the cited ones


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------

def _fake_agent(text, searches, urls):
    async def fake(system, user_input, **kw):
        assert "enforced by CODE" in system          # the prep prompt rode along
        return TextAgentResult(text=text, searches=searches, restarts=0,
                               source_urls=tuple(urls))
    return fake


def test_prep_brief_writes_only_gated_content_and_accounts(tmp_path, monkeypatch):
    _op(tmp_path)
    monkeypatch.setattr(t, "run_text_agent", _fake_agent(_BRIEF, 3, _RETRIEVED))
    out = _call("prep_brief", company_or_person="CPC Texas")
    assert out["stripped_uncited_lines"] == 2
    assert out["searches"] == 3
    body = (tmp_path / "brief.md").read_text(encoding="utf-8")
    assert "former astronaut" not in body
    assert "totally-invented.example" not in body
    assert "Commercial plumbing across central Texas." in body


def test_prep_brief_stops_when_search_returns_nothing(tmp_path, monkeypatch):
    _op(tmp_path)
    monkeypatch.setattr(t, "run_text_agent",
                        _fake_agent("## What they do\n- Made up stuff.", 0, ()))
    out = _call("prep_brief", company_or_person="Ghost LLC")
    assert "error" in out and "no sources" in out["error"].lower()
    assert not (tmp_path / "brief.md").exists()      # no file, full stop


def test_prep_brief_stops_when_every_line_fails_the_gate(tmp_path, monkeypatch):
    _op(tmp_path)
    text = "# Prep brief: X\n\n## What they do\n- Uncited claim one.\n- Uncited claim two."
    monkeypatch.setattr(t, "run_text_agent",
                        _fake_agent(text, 2, ("https://real.example/page",)))
    out = _call("prep_brief", company_or_person="X")
    assert "error" in out and "source gate" in out["error"]
    assert not (tmp_path / "brief.md").exists()
