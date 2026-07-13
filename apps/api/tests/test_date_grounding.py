"""Date grounding — the live clock is injected at RUNTIME, never hardcoded.

A live run on 2026-07-13 anchored "this week" to the model's training era
(the leaked narration read "the current date context appears to be around
mid-December 2025") because nothing in the operator path told the model the
date. These tests pin the fix at both injection points — the run_text_agent
system choke point (all sub-agents + legacy specialists) and the planner
input — and pin that the date is evaluated from datetime.now() per call, so
the line is correct on ANY day the suite runs, not just the day it was
written.
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

from app.services import anthropic_runtime, operator_service


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.kwargs_seen = []

    async def create(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        return self._responses.pop(0)


def test_date_line_is_runtime_evaluated():
    now = datetime.now()
    line = anthropic_runtime.date_line()
    assert line.startswith("Today's date:")
    assert now.date().isoformat() in line   # ISO date from the live clock
    assert now.strftime("%A") in line       # weekday, so "this week" resolves


def test_run_text_agent_injects_runtime_date_into_system(monkeypatch):
    fake = _FakeMessages([SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="ok")],
    )])
    monkeypatch.setattr(anthropic_runtime, "get_client",
                        lambda: SimpleNamespace(messages=fake))
    asyncio.run(anthropic_runtime.run_text_agent("SUB-AGENT PROMPT", "hi"))
    system = fake.kwargs_seen[0]["system"]
    assert system.startswith("Today's date:")
    assert datetime.now().date().isoformat() in system
    assert "SUB-AGENT PROMPT" in system   # original prompt intact after the line


def test_planner_input_carries_runtime_date(monkeypatch):
    monkeypatch.setattr(operator_service, "_memory_context_snippet",
                        lambda: "none")
    out = operator_service._build_planner_input(
        "Build a research packet on agentic AI this week.",
        "Drive auto-upload: off (user disabled). Skip auto_upload_drive.",
    )
    assert out.startswith("Today's date:")
    assert datetime.now().date().isoformat() in out
    assert "Operator command:\nBuild a research packet" in out
