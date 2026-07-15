"""Per-path model split — research sub-agents on Sonnet, planner untouched.

The research/packet sub-agents spend a multi-minute foreground wait on
web-search round-trips + summarizing; Sonnet-tier holds up there. The
planner (tool selection, gate context, receipts) and every other sub-agent
stay on default_model(). These tests pin the wiring so a Settings/env change
can never silently downgrade the planner, and the research override can
never silently fall back to Opus.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.agents import default_model, research_model
from app.services import anthropic_runtime, operator_service
from app.services import operator_tools as t
from app.services.anthropic_runtime import TextAgentResult
from app.services.operator_context import OperatorContext, set_current_operator


def test_research_model_default_and_env_override(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)
    assert research_model() == "claude-sonnet-5"
    monkeypatch.setenv("ANTHROPIC_RESEARCH_MODEL", "claude-haiku-4-5-20251001")
    assert research_model() == "claude-haiku-4-5-20251001"
    # the planner's model source is a separate knob
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert default_model() == "claude-opus-4-8"


def _fake_client():
    fake = SimpleNamespace(kwargs_seen=[])

    async def create(**kwargs):
        fake.kwargs_seen.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text="ok")])
    fake.messages = SimpleNamespace(create=create)
    return fake


def test_run_text_agent_model_override_and_default(monkeypatch):
    fake = _fake_client()
    monkeypatch.setattr(anthropic_runtime, "get_client", lambda: fake)
    asyncio.run(anthropic_runtime.run_text_agent("sys", "hi", model="claude-sonnet-5"))
    asyncio.run(anthropic_runtime.run_text_agent("sys", "hi"))
    assert fake.kwargs_seen[0]["model"] == "claude-sonnet-5"
    assert fake.kwargs_seen[1]["model"] == default_model()   # no override → default


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


def _capture_agent(text, seen):
    async def fake(system, prompt, **kw):
        seen.append(kw)
        return TextAgentResult(text=text, searches=3, restarts=0)
    return fake


def test_research_tools_pass_research_model(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)
    seen: list = []
    monkeypatch.setattr(t, "run_text_agent", _capture_agent(
        "### Source One\n- URL: https://real.example/a\n", seen))
    op = _ctx(tmp_path, {"deliverable_intent": True, "source_locked_url": ""})
    set_current_operator(op)
    json.loads(asyncio.run(_tool("web_research").call({"topic": "x"})))
    json.loads(asyncio.run(_tool("build_research_packet").call({"topic": "x"})))
    assert len(seen) == 2
    for kw in seen:
        assert kw["model"] == "claude-sonnet-5"
        assert kw["use_web_search"] is True


# --------------------------------------------------------------------------
# v3: per-run override from the composer selector
# --------------------------------------------------------------------------

def test_per_run_override_reaches_the_research_tools(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)
    seen: list = []
    monkeypatch.setattr(t, "run_text_agent", _capture_agent(
        "### Source One\n- URL: https://real.example/a\n", seen))
    op = _ctx(tmp_path, {"deliverable_intent": True, "source_locked_url": "",
                         "research_model_override": "claude-opus-4-8"})
    set_current_operator(op)
    json.loads(asyncio.run(_tool("web_research").call({"topic": "x"})))
    json.loads(asyncio.run(_tool("build_research_packet").call({"topic": "x"})))
    assert [kw["model"] for kw in seen] == ["claude-opus-4-8", "claude-opus-4-8"]


def test_empty_override_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)
    op = _ctx(tmp_path, {"research_model_override": ""})
    assert t._effective_research_model(op) == "claude-sonnet-5"


def test_sanitize_research_model_allowlists():
    """The intake filter: only curated research models pass; junk, planner
    smuggling attempts, and empties resolve to '' (Settings default)."""
    assert operator_service._sanitize_research_model("claude-opus-4-8") == "claude-opus-4-8"
    assert operator_service._sanitize_research_model("claude-fable-5") == "claude-fable-5"
    assert operator_service._sanitize_research_model("  claude-sonnet-5  ") == "claude-sonnet-5"
    assert operator_service._sanitize_research_model("gpt-999") == ""
    assert operator_service._sanitize_research_model("") == ""
    assert operator_service._sanitize_research_model("claude-sonnet-5; rm -rf /") == ""
