"""Per-agent model selectors + sub-agent effort — planner protected.

The Script selector rides the same allowlisted-override pattern as Research;
script_model() falls back to the planner model (the script writer's
historical behavior). Effort is the GA output_config.effort request param —
levels taken verbatim by the API, no token budgets exist behind them —
applied to SUB-AGENT calls only and omitted for Haiku (which rejects it).
The planner's model and effort are deliberately absent from the run surface.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.agents import default_model, model_supports_effort, script_model
from app.services import anthropic_runtime, operator_service
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


# --------------------------------------------------------------------------
# script_model resolution
# --------------------------------------------------------------------------

def test_script_model_falls_back_to_planner_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_SCRIPT_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert script_model() == default_model() == "claude-opus-4-8"
    monkeypatch.setenv("ANTHROPIC_SCRIPT_MODEL", "claude-sonnet-5")
    assert script_model() == "claude-sonnet-5"


def test_script_override_reaches_the_real_tool(tmp_path, monkeypatch):
    seen = {}

    async def fake(system, prompt, **kw):
        seen.update(kw)
        # the script tool now asks for stats (return_stats=True) so its spend
        # can be folded into the run's dollar ledger
        return TextAgentResult(text="**Host A**: hello\n**Host B**: hi\n",
                               searches=0, restarts=0)

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, {"script_model_override": "claude-fable-5"})
    set_current_operator(op)
    # write_audiobook_script is not in PLANNER_TOOLS (legacy audiobook path);
    # exercise the decorated tool object directly.
    payload = json.loads(asyncio.run(t.write_audiobook_script.call({
        "sources_md": "### Source One\nhttps://e.com\n",
    })))
    assert payload["bytes"] > 0
    assert seen["model"] == "claude-fable-5"


def test_script_default_preserves_historical_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_SCRIPT_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    seen = {}

    async def fake(system, prompt, **kw):
        seen.update(kw)
        return TextAgentResult(text="**Host A**: hello\n", searches=0, restarts=0)

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, {})
    set_current_operator(op)
    asyncio.run(t.write_audiobook_script.call({"sources_md": "### S\n"}))
    assert seen["model"] == "claude-opus-4-8"   # rides the planner model


# --------------------------------------------------------------------------
# effort — real API param, sub-agents only, Haiku omitted
# --------------------------------------------------------------------------

def test_sanitize_effort_allowlists():
    assert operator_service._sanitize_effort("high") == "high"
    assert operator_service._sanitize_effort("  Medium ") == "medium"
    assert operator_service._sanitize_effort("xhigh") == ""    # not offered per-run
    assert operator_service._sanitize_effort("turbo") == ""
    assert operator_service._sanitize_effort("") == ""


def test_model_supports_effort():
    assert model_supports_effort("claude-sonnet-5")
    assert model_supports_effort("claude-opus-4-8")
    assert model_supports_effort("claude-fable-5")
    assert not model_supports_effort("claude-haiku-4-5")


def _fake_client(seen):
    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text="ok")])
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_run_text_agent_sends_effort_as_output_config(monkeypatch):
    seen = []
    monkeypatch.setattr(anthropic_runtime, "get_client", lambda: _fake_client(seen))
    asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", model="claude-sonnet-5", effort="medium"))
    assert seen[0]["output_config"] == {"effort": "medium"}


def test_run_text_agent_omits_effort_for_haiku(monkeypatch):
    seen = []
    monkeypatch.setattr(anthropic_runtime, "get_client", lambda: _fake_client(seen))
    asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", model="claude-haiku-4-5", effort="high"))
    assert "output_config" not in seen[0]


def test_run_text_agent_omits_effort_when_unset(monkeypatch):
    seen = []
    monkeypatch.setattr(anthropic_runtime, "get_client", lambda: _fake_client(seen))
    asyncio.run(anthropic_runtime.run_text_agent("sys", "hi", model="claude-sonnet-5"))
    assert "output_config" not in seen[0]


def test_research_tool_passes_effort_through(tmp_path, monkeypatch):
    seen = {}

    async def fake(system, prompt, **kw):
        seen.update(kw)
        return TextAgentResult(text="### S\n- URL: https://e.com\n", searches=2, restarts=0)

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, {"deliverable_intent": True, "source_locked_url": "",
                         "research_plan_asked": True, "research_approved": True,
                         "effort_override": "medium"})
    set_current_operator(op)
    asyncio.run(_tool("web_research").call({"topic": "x"}))
    assert seen["effort"] == "medium"


def test_plan_names_effective_effort(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)

    async def bomb(*a, **kw):
        raise AssertionError("no spend before approval")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"deliverable_intent": True, "effort_override": "medium"})
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "x"}))
    q = op.record["needs_input"][-1]["question"]
    assert "effort: medium" in q


def test_plan_notes_haiku_effort_omission(tmp_path, monkeypatch):
    async def bomb(*a, **kw):
        raise AssertionError("no spend before approval")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"deliverable_intent": True, "effort_override": "high",
                         "research_model_override": "claude-haiku-4-5"})
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "x"}))
    q = op.record["needs_input"][-1]["question"]
    assert "claude-haiku-4-5" in q
    assert "n/a on Haiku" in q
