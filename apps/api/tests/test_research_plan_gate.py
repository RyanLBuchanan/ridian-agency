"""Approve-before-spend for live web research — enforced in code.

The feature's whole guarantee: ZERO search spend before the operator
approves. These tests pin the spend itself, not just the question — on the
unapproved and declined paths run_text_agent is replaced with a bomb that
fails the test if the tool ever reaches it. Approval flags are set only by
operator_service._apply_research_answer from the operator's own answer, so
the planner can never talk itself past the gate.
"""
import asyncio
import json
from pathlib import Path

from app.services import operator_service as svc
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


def _spend_bomb(monkeypatch):
    """run_text_agent stand-in that MUST NOT be reached: any call = leaked
    search spend before approval, and the test fails loudly."""
    calls = {"n": 0}

    async def bomb(*a, **kw):
        calls["n"] += 1
        raise AssertionError("run_text_agent called before approval — search spend leaked")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    return calls


# --------------------------------------------------------------------------
# Unapproved path: plan presented, ZERO spend
# --------------------------------------------------------------------------

def test_unapproved_packet_run_never_reaches_the_api(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)
    calls = _spend_bomb(monkeypatch)
    op = _ctx(tmp_path, {"deliverable_intent": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("build_research_packet").call(
        {"topic": "agentic AI", "time_window": "this week"}
    )))
    assert payload["reason"] == "research_plan_pending"
    assert calls["n"] == 0                      # THE guarantee: zero spend
    assert op.record["awaiting_input"] is True  # run paused for the answer
    need = op.record["needs_input"][-1]
    # the plan is deterministic: topic, window, search cap, model, cost
    assert "agentic AI" in need["question"]
    assert "this week" in need["question"]
    assert "8" in need["question"]
    assert "claude-sonnet-5" in need["question"]
    assert "$" in need["question"]
    assert [o["label"] for o in need["options"]] == ["Proceed", "Cancel"]
    assert all(o["action"] == "submit" for o in need["options"])
    step = next(s for s in op.record["steps"] if s["name"] == "research_plan")
    assert step["status"] == "running"


def test_unapproved_web_research_never_reaches_the_api(tmp_path, monkeypatch):
    calls = _spend_bomb(monkeypatch)
    op = _ctx(tmp_path, {"deliverable_intent": True, "source_locked_url": ""})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("web_research").call({"topic": "x"})))
    assert payload["reason"] == "research_plan_pending"
    assert calls["n"] == 0


def test_plan_asked_once_while_pending(tmp_path, monkeypatch):
    """A planner retry while the question is open must not spam the operator
    with duplicate questions — and must still spend nothing."""
    calls = _spend_bomb(monkeypatch)
    op = _ctx(tmp_path, {"deliverable_intent": True})
    set_current_operator(op)
    for _ in range(2):
        payload = json.loads(asyncio.run(
            _tool("build_research_packet").call({"topic": "x"})
        ))
        assert payload["reason"] == "research_plan_pending"
    assert len(op.record["needs_input"]) == 1
    assert calls["n"] == 0


# --------------------------------------------------------------------------
# Declined path: honest refusal, ZERO spend
# --------------------------------------------------------------------------

def test_declined_run_never_reaches_the_api(tmp_path, monkeypatch):
    calls = _spend_bomb(monkeypatch)
    op = _ctx(tmp_path, {"deliverable_intent": True,
                         "research_plan_asked": True, "research_declined": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(
        _tool("build_research_packet").call({"topic": "x"})
    ))
    assert payload["reason"] == "research_declined"
    assert calls["n"] == 0
    step = next(s for s in op.record["steps"] if s["name"] == "research_plan")
    assert step["status"] == "skipped"


# --------------------------------------------------------------------------
# Approved path: proceeds exactly once approved
# --------------------------------------------------------------------------

def test_approved_run_proceeds(tmp_path, monkeypatch):
    seen = []

    async def fake(system, prompt, **kw):
        seen.append(kw)
        return TextAgentResult(
            text="**Audio Overview focus:** x.\n\n## Source One\nhttps://e.com\n\nS.\n",
            searches=3, restarts=0,
        )

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, {"deliverable_intent": True,
                         "research_plan_asked": True, "research_approved": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(
        _tool("build_research_packet").call({"topic": "x"})
    ))
    assert payload["sources_count"] == 1
    assert len(seen) == 1
    step = next(s for s in op.record["steps"] if s["name"] == "research_plan")
    assert step["status"] == "completed"


def test_search_lock_wins_over_plan_gate(tmp_path, monkeypatch):
    """A source-locked run refuses BEFORE any plan is presented — the lock is
    the stronger gate and the operator never sees a plan for a forbidden run."""
    calls = _spend_bomb(monkeypatch)
    op = _ctx(tmp_path, {"deliverable_intent": True,
                         "source_locked_url": "https://x/y"})
    set_current_operator(op)
    payload = json.loads(asyncio.run(
        _tool("build_research_packet").call({"topic": "x"})
    ))
    assert payload["reason"] == "search_locked"
    assert "research_plan_asked" not in op.record
    assert op.record.get("needs_input", []) == []   # no plan question was raised
    assert calls["n"] == 0


# --------------------------------------------------------------------------
# Answer resolution — the ONLY writer of the approval flags
# --------------------------------------------------------------------------

def test_apply_answer_proceed_button(tmp_path):
    op = _ctx(tmp_path, {"research_plan_asked": True})
    note = svc._apply_research_answer(op, t.RESEARCH_PLAN_PROCEED)
    assert op.record["research_approved"] is True
    assert "APPROVED" in note


def test_apply_answer_cancel_button(tmp_path):
    op = _ctx(tmp_path, {"research_plan_asked": True})
    note = svc._apply_research_answer(op, t.RESEARCH_PLAN_CANCEL)
    assert op.record["research_declined"] is True
    assert "DECLINED" in note


def test_apply_answer_keywords(tmp_path):
    op = _ctx(tmp_path, {"research_plan_asked": True})
    svc._apply_research_answer(op, "yes, go ahead but keep it tight")
    assert op.record["research_approved"] is True

    op2 = _ctx(tmp_path, {"research_plan_asked": True})
    svc._apply_research_answer(op2, "no, cancel that")
    assert op2.record["research_declined"] is True


def test_apply_answer_unmatched_reasks(tmp_path):
    op = _ctx(tmp_path, {"research_plan_asked": True})
    note = svc._apply_research_answer(op, "how many searches would that be?")
    assert note == ""
    assert "research_approved" not in op.record
    assert "research_declined" not in op.record
    assert op.record["research_plan_asked"] is False   # gate will re-present


def test_apply_answer_noop_when_no_plan_pending(tmp_path):
    op = _ctx(tmp_path, {})
    assert svc._apply_research_answer(op, "proceed") == ""
    assert "research_approved" not in op.record
