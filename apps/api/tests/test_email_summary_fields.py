"""Review-email snapshot fields — original command, cost, packet coverage.

The "Email package to me" summary is assembled renderer-side from the
operation snapshot. A resumed run's summary used to show the operator's
approval click ("Proceed with the research plan") as "what I asked" because
the renderer read the LAST start event's command — the resume answer. These
tests pin the backend half of the fix: the ORIGINAL command survives on the
record untouched by resume answers, and the research tools persist
source_titles + the reconciliation line so the email can show what a run
covered and cost without re-opening artifacts.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.services import operation_log_service, operator_service
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


_APPROVED = {"deliverable_intent": True, "research_plan_asked": True,
             "research_approved": True}


# --------------------------------------------------------------------------
# The tools persist coverage + the self-audit on the record
# --------------------------------------------------------------------------

def test_packet_tool_persists_titles_and_reconciliation(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)

    async def fake(system, prompt, **kw):
        return TextAgentResult(
            text=("**Audio Overview focus:** frameworks.\n\n"
                  "## LangGraph 0.6 ships durable execution\nhttps://a.com\n\nS.\n\n"
                  "## Claude Agent SDK adds managed sandboxes\nhttps://b.com\n\nS.\n"),
            searches=8, restarts=0, elapsed_seconds=417.0,
            tokens_in=150_000, tokens_out=8_000,
        )

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, dict(_APPROVED))
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "agentic AI"}))
    assert op.record["source_titles"] == [
        "LangGraph 0.6 ships durable execution",
        "Claude Agent SDK adds managed sandboxes",
    ]
    recon = op.record["reconciliation"]
    assert recon.startswith("Plan: up to 8 searches")
    assert "8 searches" in recon and "6m57s" in recon and "$0.46" in recon


def test_web_research_persists_titles_from_h3(tmp_path, monkeypatch):
    async def fake(system, prompt, **kw):
        return TextAgentResult(
            text="### Source One\nhttps://a.com\n\n### Source Two\nhttps://b.com\n",
            searches=2, restarts=0,
        )

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, dict(_APPROVED))
    set_current_operator(op)
    asyncio.run(_tool("web_research").call({"topic": "x"}))
    assert op.record["source_titles"] == ["Source One", "Source Two"]
    assert op.record["reconciliation"].startswith("Plan: up to 8 searches")


def test_titles_capped_at_20(tmp_path, monkeypatch):
    body = "\n\n".join(f"## Source {i}\nhttps://e.com/{i}\n\nS." for i in range(25))

    async def fake(system, prompt, **kw):
        return TextAgentResult(text=f"**Audio Overview focus:** x.\n\n{body}\n",
                               searches=1, restarts=0)

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, dict(_APPROVED))
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "x"}))
    assert len(op.record["source_titles"]) == 20
    assert op.record["source_titles"][0] == "Source 0"


# --------------------------------------------------------------------------
# The snapshot carries the email's fields — and the ORIGINAL command
# --------------------------------------------------------------------------

def test_finalized_view_carries_email_fields():
    record = operation_log_service.build_record(
        command="Build a research packet on the newest agentic AI frameworks this week",
        intent="planner", artifact_folder="f",
    )
    record["research_approved"] = True
    record["reconciliation"] = "Plan: up to 8 searches — actual: 8, 6m57s, ≈$0.62"
    record["source_titles"] = ["A", "B"]
    record["spend_usd"] = 0.9731
    record["cost_ceiling_usd"] = 1.0
    snap = operator_service._finalized_view(record)
    assert snap["command"].startswith("Build a research packet")   # the ASK
    assert snap["research_approved"] is True
    assert snap["research_declined"] is False
    assert snap["reconciliation"].endswith("≈$0.62")
    assert snap["source_titles"] == ["A", "B"]
    assert snap["spend_usd"] == 0.9731
    assert json.dumps(snap)   # snapshot stays JSON-serializable for disk/SSE


def test_finalized_view_defaults_for_old_records():
    """Runs that predate these fields still snapshot cleanly (renderer falls
    back to the Sources-gathered count and omits the cost lines)."""
    record = operation_log_service.build_record(
        command="old run", intent="planner", artifact_folder="f",
    )
    snap = operator_service._finalized_view(record)
    assert snap["reconciliation"] == ""
    assert snap["source_titles"] == []
    assert snap["research_approved"] is False


def test_resume_answer_never_touches_the_command(tmp_path):
    """The root cause, pinned at the record level: applying the operator's
    plan-approval answer sets the flag but leaves record["command"] — and
    therefore the snapshot's command — as the ORIGINAL initiating request."""
    record = operation_log_service.build_record(
        command="Build a research packet on agentic AI frameworks",
        intent="planner", artifact_folder="f",
    )
    record["research_plan_asked"] = True
    op = _ctx(tmp_path, record)
    note = operator_service._apply_research_answer(
        op, "Proceed with the research plan")
    assert "APPROVED" in note
    assert record["research_approved"] is True
    assert record["command"] == "Build a research packet on agentic AI frameworks"
    snap = operator_service._finalized_view(record)
    assert snap["command"] == "Build a research packet on agentic AI frameworks"
    assert snap["research_approved"] is True
