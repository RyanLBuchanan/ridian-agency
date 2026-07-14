"""Search-lock gate — web search is excluded IN CODE on source-locked runs.

Prior state: the exclusion was prompt-only (the model obeyed "do NOT use web
search" in the staged-source note / planner HARD RULE). These tests pin the
code guarantee: on a locked run the registered web_research /
build_research_packet tools refuse before any search happens; grounding_ok
does NOT unlock search (a grounded "use only this page" run still must not
supplement from the web); only the operator's explicit resume override does.
"""
import asyncio
import json
from pathlib import Path

from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


def test_gate_blocks_locked_run(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y"})
    res = t._search_lock_gate(op)
    assert res is not None and res["reason"] == "search_locked"


def test_gate_blocks_even_when_grounded(tmp_path):
    # A successful read_url does NOT license web search on a locked run.
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y", "grounding_ok": True})
    res = t._search_lock_gate(op)
    assert res is not None and res["reason"] == "search_locked"


def test_gate_allows_operator_override(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y", "grounding_override": True})
    assert t._search_lock_gate(op) is None


def test_gate_allows_unlocked_run(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": ""})
    assert t._search_lock_gate(op) is None


def test_web_research_tool_refuses_on_locked_run(tmp_path):
    """Through the REAL registered tool object — the exact path the runner
    executes. No network: the refusal happens before run_text_agent."""
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y", "deliverable_intent": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("web_research").call({"topic": "anything"})))
    assert payload["reason"] == "search_locked"
    assert op.record["steps"] == []   # refused before the 'research' step ran


def test_build_research_packet_refuses_on_locked_run(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y", "deliverable_intent": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(
        _tool("build_research_packet").call({"topic": "anything"})
    ))
    assert payload["reason"] == "search_locked"


def test_web_research_runs_on_unlocked_run(tmp_path, monkeypatch):
    """Positive path wiring (search itself mocked): unlocked run reaches the
    sub-agent and produces a sources packet result."""
    from app.services.anthropic_runtime import TextAgentResult

    async def fake_agent(system, prompt, **kw):
        return TextAgentResult(
            text="### Source One\n- URL: https://real.example\n",
            searches=2, restarts=0,
        )
    monkeypatch.setattr(t, "run_text_agent", fake_agent)
    op = _ctx(tmp_path, {"source_locked_url": "", "deliverable_intent": True,
                         "research_plan_asked": True, "research_approved": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("web_research").call({"topic": "agentic AI"})))
    assert payload["sources_count"] == 1
    assert "Source One" in payload["sources_md"]