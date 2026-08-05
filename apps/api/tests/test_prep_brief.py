"""prep_brief (v5.0 Phase 4) — the hard source gate.

Pins:
  1. the gate is PURE, DETERMINISTIC code: a factual line with no [src:]
     is stripped; a line citing a URL the search did NOT return (a
     fabricated source) is stripped identically; cited lines survive;
     structure (headers, questions, "Nothing found.") passes;
  2. zero retrieved sources = NO brief file, an honest error, full stop;
  3. the tool writes brief.md only from gate-surviving content and reports
     how many lines were stripped (honest accounting, never silent);
  4. (v5.1) the query set is DETERMINISTIC: same target → the same five
     fixed angles, injected verbatim as REQUIRED SEARCHES — coverage no
     longer depends on what the research model improvises; queries the
     model actually ran are reported back, and skipped angles are named;
  5. (v5.1) record["sources_count"] — what operation_log.json reports —
     matches the retrieved-source count instead of staying 0.
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


# --------------------------------------------------------------------------
# v5.1 — deterministic query set + honest sources_count
# --------------------------------------------------------------------------

def test_prep_queries_are_deterministic_and_cover_the_five_angles():
    a = t._prep_queries("CPC Texas")
    b = t._prep_queries("CPC Texas")
    assert a == b and len(a) == 5                    # same target → same list
    joined = " ".join(a).lower()
    for needle in ("overview", "leadership", "size", "news", "events"):
        assert needle in joined, f"angle {needle!r} missing from {a}"
    assert all('"CPC Texas"' in q for q in a)        # target pinned in every query


def test_required_queries_ride_the_research_input_verbatim(tmp_path, monkeypatch):
    _op(tmp_path)
    seen = {}

    async def capture(system, user_input, **kw):
        seen["user_input"] = user_input
        return TextAgentResult(text=_BRIEF, searches=5, restarts=0,
                               source_urls=_RETRIEVED,
                               queries=tuple(t._prep_queries("CPC Texas")))

    monkeypatch.setattr(t, "run_text_agent", capture)
    out = _call("prep_brief", company_or_person="CPC Texas")
    assert "REQUIRED SEARCHES" in seen["user_input"]
    for q in t._prep_queries("CPC Texas"):
        assert q in seen["user_input"]               # injected exactly, in full
    assert out["required_queries"] == t._prep_queries("CPC Texas")
    assert out["queries_run"] == t._prep_queries("CPC Texas")


def test_skipped_required_angles_are_named_in_the_step(tmp_path, monkeypatch):
    op = _op(tmp_path)
    ran = tuple(t._prep_queries("CPC Texas")[:3])    # news + events never ran

    async def fake(system, user_input, **kw):
        return TextAgentResult(text=_BRIEF, searches=3, restarts=0,
                               source_urls=_RETRIEVED, queries=ran)

    monkeypatch.setattr(t, "run_text_agent", fake)
    _call("prep_brief", company_or_person="CPC Texas")
    detail = op.record["steps"][-1]["detail"]
    assert "angles not searched as required" in detail
    assert "recent news" in detail and "events" in detail


def test_sources_count_reaches_the_operation_record(tmp_path, monkeypatch):
    """operation_log.json reads record["sources_count"]; before v5.1 a
    27-source prep run logged 0."""
    op = _op(tmp_path)
    monkeypatch.setattr(t, "run_text_agent", _fake_agent(_BRIEF, 3, _RETRIEVED))
    _call("prep_brief", company_or_person="CPC Texas")
    assert op.record["sources_count"] == 2           # the two retrieved URLs
