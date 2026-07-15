"""Zero-search research is UNGROUNDED — flagged in code, not left to trust.

If the research sub-agent performs ZERO live web searches, its "sources" came
from model memory, and the run must say so instead of presenting the packet
confidently. These tests pin the guarantee through the REAL registered tools
(the exact objects the tool runner executes): the artifact carries a warning
banner, the step detail says UNGROUNDED, the record is flagged, and the tool
returns ungrounded=true so the planner's receipt tells the truth.
"""
import asyncio
import json
from pathlib import Path

from app.services import operator_tools as t
from app.services.anthropic_runtime import TextAgentResult
from app.services.operator_context import OperatorContext, set_current_operator


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


_PACKET_BODY = ("**Audio Overview focus:** what changed.\n\n"
                "## Source One\nhttps://real.example/a\n\nTight summary.\n")
_SOURCES_BODY = "### Source One\n- URL: https://real.example/a\n"


def _fake_agent(text, searches):
    async def fake(system, prompt, **kw):
        return TextAgentResult(text=text, searches=searches, restarts=0)
    return fake


# --------------------------------------------------------------------------
# build_research_packet
# --------------------------------------------------------------------------

def test_build_research_packet_flags_zero_search(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "run_text_agent", _fake_agent(_PACKET_BODY, searches=0))
    op = _ctx(tmp_path, {"deliverable_intent": True,
                         "research_plan_asked": True, "research_approved": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(
        _tool("build_research_packet").call({"topic": "agentic AI"})
    ))
    assert payload["ungrounded"] is True
    text = (Path(tmp_path) / "research_packet.md").read_text(encoding="utf-8")
    assert "UNGROUNDED" in text
    # banner sits between the deterministic header and the body
    assert text.index("Prepared by Ridian") < text.index("UNGROUNDED") \
        < text.index("Source One")
    assert op.record["ungrounded_research"] is True
    step = next(s for s in op.record["steps"] if s["name"] == "research_packet")
    assert "UNGROUNDED" in step["detail"]


def test_build_research_packet_clean_when_searches_ran(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "run_text_agent", _fake_agent(_PACKET_BODY, searches=6))
    op = _ctx(tmp_path, {"deliverable_intent": True,
                         "research_plan_asked": True, "research_approved": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(
        _tool("build_research_packet").call({"topic": "agentic AI"})
    ))
    assert payload["ungrounded"] is False
    text = (Path(tmp_path) / "research_packet.md").read_text(encoding="utf-8")
    assert "UNGROUNDED" not in text
    assert "ungrounded_research" not in op.record


# --------------------------------------------------------------------------
# web_research
# --------------------------------------------------------------------------

def test_web_research_flags_zero_search(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "run_text_agent", _fake_agent(_SOURCES_BODY, searches=0))
    op = _ctx(tmp_path, {"deliverable_intent": True, "source_locked_url": "",
                         "research_plan_asked": True, "research_approved": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("web_research").call({"topic": "x"})))
    assert payload["ungrounded"] is True
    assert payload["sources_md"].lstrip().startswith(">")   # banner leads the packet
    assert "UNGROUNDED" in payload["sources_md"]
    assert payload["sources_count"] == 1   # counting still works under the banner
    assert op.record["ungrounded_research"] is True
    step = next(s for s in op.record["steps"] if s["name"] == "research")
    assert "UNGROUNDED" in step["detail"]


def test_web_research_clean_when_searches_ran(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "run_text_agent", _fake_agent(_SOURCES_BODY, searches=3))
    op = _ctx(tmp_path, {"deliverable_intent": True, "source_locked_url": "",
                         "research_plan_asked": True, "research_approved": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("web_research").call({"topic": "x"})))
    assert payload["ungrounded"] is False
    assert "UNGROUNDED" not in payload["sources_md"]
    assert "ungrounded_research" not in op.record
