"""Honest search accounting + hard spend bounds.

The 2026-07-15 live run showed "Searching 27 of up to 8": every
server_tool_use block — including the dynamic-filtering code execution that
web_search_20260209 runs under the hood — was counted as a search. These
tests pin the fixes: only name=="web_search" blocks count, the authoritative
billed number comes from usage.server_tool_use.web_search_requests, resumed
segments only get the REMAINING search budget, a hostile stream can never
push billed searches past the approved cap (adversarial guarantee test), and
a wall-clock ceiling bounds every turn independent of byte flow.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import anthropic_runtime
from app.services import operator_tools as t
from app.services.anthropic_runtime import (
    ResearchBudgetExceeded,
    RunDeadlineExceeded,
    TextAgentResult,
)
from app.services.operator_context import OperatorContext, set_current_operator


def _text(txt):
    return SimpleNamespace(type="text", text=txt)


def _search_block():
    return SimpleNamespace(type="server_tool_use", name="web_search")


def _filter_block():
    return SimpleNamespace(type="server_tool_use", name="code_execution")


def _usage(searches=None, tokens_in=0, tokens_out=0):
    stu = SimpleNamespace(web_search_requests=searches) if searches is not None else None
    return SimpleNamespace(server_tool_use=stu, input_tokens=tokens_in,
                           output_tokens=tokens_out)


def _final(stop_reason, blocks, usage=None):
    resp = SimpleNamespace(stop_reason=stop_reason, content=list(blocks))
    if usage is not None:
        resp.usage = usage
    return resp


def _start(block):
    return SimpleNamespace(type="content_block_start", content_block=block)


class _FakeStream:
    def __init__(self, events, final, delay=0.0):
        self._events = list(events)
        self._final = final
        self._delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        self._it = iter(self._events)
        return self

    async def __anext__(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, streams):
        self._streams = list(streams)
        self.kwargs_seen = []

    def stream(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        return self._streams.pop(0)

    async def create(self, **kwargs):
        raise AssertionError("create() used on a progress run")


def _patch(monkeypatch, fake):
    monkeypatch.setattr(anthropic_runtime, "get_client",
                        lambda: SimpleNamespace(messages=fake))


async def _noop_progress(phase, n):
    return None


# --------------------------------------------------------------------------
# Honest counting
# --------------------------------------------------------------------------

def test_filter_rounds_are_not_searches(monkeypatch):
    """Dynamic-filtering code-execution rounds show as their own phase and
    never increment the search counter — the '27 of 8' lie is dead."""
    fake = _FakeMessages([_FakeStream(
        events=[
            _start(_search_block()),    # ("search", 1)
            _start(_filter_block()),    # ("filter", 1)
            _start(_filter_block()),    # ("filter", 1)
            _start(_search_block()),    # ("search", 2)
            _start(_text("w")),         # ("writing", 2)
        ],
        final=_final("end_turn",
                     [_search_block(), _filter_block(), _filter_block(),
                      _search_block(), _text("FINAL")]),
    )])
    _patch(monkeypatch, fake)
    seen = []

    async def on_progress(phase, n):
        seen.append((phase, n))

    res = asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, return_stats=True,
        on_progress=on_progress,
    ))
    assert seen == [("search", 1), ("filter", 1), ("filter", 1),
                    ("search", 2), ("writing", 2)]
    assert res.searches == 2       # named-block fallback excludes filter rounds
    assert res.tool_rounds == 4    # but rounds count everything
    assert res.text == "FINAL"


def test_billed_count_from_usage_is_authoritative(monkeypatch):
    """usage.server_tool_use.web_search_requests wins over block counting."""
    fake = _FakeMessages([_FakeStream(
        events=[_start(_search_block())],
        final=_final("end_turn",
                     [_search_block(), _search_block(), _search_block(), _text("F")],
                     usage=_usage(searches=8, tokens_in=150_000, tokens_out=8_000)),
    )])
    _patch(monkeypatch, fake)
    res = asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, return_stats=True,
        on_progress=_noop_progress,
    ))
    assert res.searches == 8          # from usage, not the 3 blocks
    assert res.tokens_in == 150_000
    assert res.tokens_out == 8_000
    assert res.elapsed_seconds >= 0


def test_missing_usage_falls_back_to_named_blocks(monkeypatch):
    fake = _FakeMessages([_FakeStream(
        events=[],
        final=_final("end_turn", [_search_block(), _filter_block(), _text("F")]),
    )])
    _patch(monkeypatch, fake)
    res = asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, return_stats=True,
        on_progress=_noop_progress,
    ))
    assert res.searches == 1


# --------------------------------------------------------------------------
# Cap enforcement — ours, not just the server's
# --------------------------------------------------------------------------

def test_resume_shrinks_max_uses_to_remaining_budget(monkeypatch):
    fake = _FakeMessages([
        _FakeStream(events=[], final=_final(
            "pause_turn", [_search_block()], usage=_usage(searches=5))),
        _FakeStream(events=[], final=_final(
            "end_turn", [_text("done")], usage=_usage(searches=0))),
    ])
    _patch(monkeypatch, fake)
    asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, on_progress=_noop_progress,
    ))
    first, resumed = fake.kwargs_seen
    assert first["tools"][0]["max_uses"] == 8
    assert resumed["tools"][0]["max_uses"] == 3    # 8 cap - 5 billed


def test_resume_with_exhausted_budget_floors_at_one(monkeypatch):
    """max_uses:0 isn't a valid request — the floor is 1, and the mid-stream
    guard covers that last gap (see the adversarial test below)."""
    fake = _FakeMessages([
        _FakeStream(events=[], final=_final(
            "pause_turn", [_search_block()], usage=_usage(searches=8))),
        _FakeStream(events=[], final=_final(
            "end_turn", [_text("done")], usage=_usage(searches=0))),
    ])
    _patch(monkeypatch, fake)
    asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, on_progress=_noop_progress,
    ))
    assert fake.kwargs_seen[1]["tools"][0]["max_uses"] == 1


def test_hostile_stream_cannot_exceed_cap(monkeypatch):
    """THE guarantee, stated adversarially: a stream that attempts 12 searches
    against the approved cap of 8 is aborted AT the cap — billed can never
    exceed what the operator approved, no matter what the server allows."""
    fake = _FakeMessages([_FakeStream(
        events=[_start(_search_block()) for _ in range(12)],   # hostile: 12 attempts
        final=_final("end_turn", [_text("never reached")]),
    )])
    _patch(monkeypatch, fake)
    seen = []

    async def on_progress(phase, n):
        seen.append((phase, n))

    with pytest.raises(ResearchBudgetExceeded):
        asyncio.run(anthropic_runtime.run_text_agent(
            "sys", "hi", use_web_search=True, on_progress=on_progress,
        ))
    search_ns = [n for p, n in seen if p == "search"]
    assert max(search_ns) == 8          # progressed to the cap...
    assert len(search_ns) == 8          # ...and never past it: attempt 9 aborted


def test_hostile_stream_fails_honestly_through_real_tool(tmp_path, monkeypatch):
    """Same hostile stream through the REAL registered tool and the REAL
    run_text_agent: the run fails visibly (failed step + error payload),
    never a silent success, and no artifact is written."""
    fake = _FakeMessages([_FakeStream(
        events=[_start(_search_block()) for _ in range(12)],
        final=_final("end_turn", [_text("never reached")]),
    )])
    _patch(monkeypatch, fake)

    async def _emit(_ev):
        return None

    record = {"deliverable_intent": True, "research_plan_asked": True,
              "research_approved": True,
              "steps": [], "tools_used": [], "artifacts": [], "errors": []}
    op = OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)
    set_current_operator(op)
    tool = next(x for x in t.PLANNER_TOOLS if x.name == "build_research_packet")
    import json
    payload = json.loads(asyncio.run(tool.call({"topic": "x"})))
    assert "ResearchBudgetExceeded" in payload["error"] or "approved cap" in payload["error"]
    step = next(s for s in record["steps"] if s["name"] == "research_packet")
    assert step["status"] == "failed"
    assert not (Path(tmp_path) / "research_packet.md").exists()
    assert record["errors"]                       # surfaced as an error event too


# --------------------------------------------------------------------------
# Wall-clock ceiling — independent of byte flow
# --------------------------------------------------------------------------

def test_wall_clock_aborts_streaming_turn(monkeypatch):
    monkeypatch.setattr(anthropic_runtime, "_MAX_TURN_SECONDS", 0.05)
    fake = _FakeMessages([_FakeStream(
        events=[_start(_text("t")) for _ in range(50)],
        final=_final("end_turn", [_text("F")]),
        delay=0.02,      # bytes keep flowing — idle timeouts would never fire
    )])
    _patch(monkeypatch, fake)
    with pytest.raises(RunDeadlineExceeded):
        asyncio.run(anthropic_runtime.run_text_agent(
            "sys", "hi", use_web_search=True, on_progress=_noop_progress,
        ))


def test_wall_clock_aborts_nonstreaming_turn(monkeypatch):
    monkeypatch.setattr(anthropic_runtime, "_MAX_TURN_SECONDS", 0.05)

    class _SlowMessages:
        async def create(self, **kwargs):
            await asyncio.sleep(0.3)
            return _final("end_turn", [_text("F")])

    _patch(monkeypatch, _SlowMessages())
    with pytest.raises(RunDeadlineExceeded):
        asyncio.run(anthropic_runtime.run_text_agent("sys", "hi"))


# --------------------------------------------------------------------------
# Reconciliation — every run self-audits
# --------------------------------------------------------------------------

def test_reconciliation_in_step_and_payload(tmp_path, monkeypatch):
    async def fake(system, prompt, **kw):
        return TextAgentResult(
            text="**Audio Overview focus:** x.\n\n## Source One\nhttps://e.com\n\nS.\n",
            searches=8, restarts=0, tool_rounds=27, elapsed_seconds=544.0,
            tokens_in=150_000, tokens_out=8_000,
        )

    monkeypatch.setattr(t, "run_text_agent", fake)

    async def _emit(_ev):
        return None

    record = {"deliverable_intent": True, "research_plan_asked": True,
              "research_approved": True,
              "steps": [], "tools_used": [], "artifacts": [], "errors": []}
    op = OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)
    set_current_operator(op)
    tool = next(x for x in t.PLANNER_TOOLS if x.name == "build_research_packet")
    import json
    payload = json.loads(asyncio.run(tool.call({"topic": "x"})))
    # 8 * $0.01 + 150k in * $2/M + 8k out * $10/M = 0.08 + 0.30 + 0.08 = $0.46
    assert payload["searches_billed"] == 8
    recon = payload["reconciliation"]
    assert "up to 8 searches" in recon
    assert "8 searches" in recon
    assert "9m04s" in recon
    assert "$0.46" in recon
    step = next(s for s in record["steps"] if s["name"] == "research_packet")
    assert recon in step["detail"]


def test_fmt_elapsed():
    assert t._fmt_elapsed(544) == "9m04s"
    assert t._fmt_elapsed(42) == "42s"
    assert t._fmt_elapsed(60) == "1m00s"


def test_plan_question_promises_the_real_band(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)

    async def bomb(*a, **kw):
        raise AssertionError("no spend before approval")

    monkeypatch.setattr(t, "run_text_agent", bomb)

    async def _emit(_ev):
        return None

    record = {"deliverable_intent": True,
              "steps": [], "tools_used": [], "artifacts": [], "errors": []}
    op = OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)
    set_current_operator(op)
    tool = next(x for x in t.PLANNER_TOOLS if x.name == "build_research_packet")
    asyncio.run(tool.call({"topic": "x"}))
    q = record["needs_input"][-1]["question"]
    assert "4–9 minutes" in q
    assert "$0.40–$0.80" in q
    assert "1–2 minutes" not in q
