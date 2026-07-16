"""Hard per-run cost ceiling + failed-run spend forensics + stream read timeout.

Born from the 2026-07-15 A/B experiment bill (~$1.50-2.00, including two
FAILED research runs that still billed for their pre-death searches). Three
guarantees pinned here:

1. FORENSICS — a failed run's spend never vanishes from our ledger: any
   exception leaving run_text_agent carries ``ridian_partial`` and logs an
   ``anthropic.run_failed`` line; the tools fold that money into
   record["spend_usd"] and STATE it in the failed step.
2. CEILING — the operator's dollar fence covers the WHOLE operation (planner
   turns included). Layer 1: billable tools refuse at/over the fence with
   zero new spend. Layer 2: the live mid-stream guard aborts a call in
   flight (RunBudgetExceeded) on the conservative live figure.
3. READ TIMEOUT — streamed research requests carry a 660s per-read timeout
   (dynamic filtering goes quiet >300s; two of three baseline runs died as
   false ReadTimeout failures) while non-streamed calls keep the client-wide
   default. The 720s wall clock remains the single binding duration bound.
"""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import anthropic_runtime, operator_service
from app.services import operator_tools as t
from app.services.anthropic_runtime import (
    RunBudgetExceeded,
    TextAgentResult,
    estimate_cost_usd,
)
from app.services.operator_context import OperatorContext, set_current_operator
from app.services.operator_service import _absorb_planner_spend, resolve_cost_ceiling


# --------------------------------------------------------------------------
# Harness (same shapes as test_honest_search_accounting)
# --------------------------------------------------------------------------

def _text(txt):
    return SimpleNamespace(type="text", text=txt)


def _search_block():
    return SimpleNamespace(type="server_tool_use", name="web_search")


def _start(block):
    return SimpleNamespace(type="content_block_start", content_block=block)


def _msg_start(tokens_in):
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(usage=SimpleNamespace(
            input_tokens=tokens_in, output_tokens=0)),
    )


def _msg_delta(tokens_out, tokens_in=0):
    return SimpleNamespace(
        type="message_delta",
        usage=SimpleNamespace(output_tokens=tokens_out, input_tokens=tokens_in),
    )


def _final(stop_reason, blocks, usage=None):
    resp = SimpleNamespace(stop_reason=stop_reason, content=list(blocks))
    if usage is not None:
        resp.usage = usage
    return resp


class _FakeStream:
    def __init__(self, events, final):
        self._events = list(events)
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        self._it = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_message(self):
        return self._final


class _DyingStream(_FakeStream):
    """Emits its events, then dies mid-stream — the ReadTimeout shape."""

    async def __anext__(self):
        try:
            return await super().__anext__()
        except StopAsyncIteration:
            raise RuntimeError("connection reset mid-stream")


class _FakeMessages:
    def __init__(self, streams):
        self._streams = list(streams)
        self.kwargs_seen = []

    def stream(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        return self._streams.pop(0)

    async def create(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        return _final("end_turn", [_text("F")])


def _patch(monkeypatch, fake):
    monkeypatch.setattr(anthropic_runtime, "get_client",
                        lambda: SimpleNamespace(messages=fake))


async def _noop_progress(phase, n):
    return None


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


# --------------------------------------------------------------------------
# Shared cost math — one function behind the fence, the plan, and forensics
# --------------------------------------------------------------------------

def test_estimate_cost_usd_rates():
    # Sonnet 5 intro: 8 searches + 150k in + 8k out = .08 + .30 + .08
    assert estimate_cost_usd("claude-sonnet-5", 150_000, 8_000,
                             searches=8) == pytest.approx(0.46)
    # Opus 4.8: $5/$25
    assert estimate_cost_usd("claude-opus-4-8", 10_000, 1_000) == pytest.approx(0.075)
    # Haiku 4.5: $1/$5
    assert estimate_cost_usd("claude-haiku-4-5-20251001", 100_000, 10_000
                             ) == pytest.approx(0.15)
    # Fable 5: $10/$50
    assert estimate_cost_usd("claude-fable-5", 10_000, 1_000) == pytest.approx(0.15)
    # Unknown model prices at the TOP tier — the fence never under-counts.
    assert estimate_cost_usd("claude-mystery-9", 10_000, 1_000) == pytest.approx(0.15)


def test_resolve_cost_ceiling_parsing(monkeypatch):
    def _with(value):
        monkeypatch.setattr(operator_service, "load_settings",
                            lambda: {"operator_run_cost_ceiling_usd": value})
        return resolve_cost_ceiling()

    monkeypatch.setattr(operator_service, "load_settings", lambda: {})
    assert resolve_cost_ceiling() == 1.00      # absent → the default fence is ON
    assert _with("") == 1.00                   # blank → default (untouched field)
    assert _with("  ") == 1.00
    assert _with("off") is None                # the deliberate no-ceiling switch
    assert _with("OFF") is None
    assert _with("none") is None
    assert _with("0") is None                  # explicit zero reads as no fence
    assert _with("2.50") == 2.50
    assert _with("$1.75") == 1.75
    assert _with("not-a-number") == 1.00       # junk fails CLOSED, not open


# --------------------------------------------------------------------------
# The plan names the fence — before any spend
# --------------------------------------------------------------------------

def test_plan_names_the_ceiling(tmp_path, monkeypatch):
    async def bomb(*a, **kw):
        raise AssertionError("no spend before approval")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"deliverable_intent": True,
                         "cost_ceiling_usd": 1.00, "spend_usd": 0.05})
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "x"}))
    q = op.record["needs_input"][-1]["question"]
    assert "Run ceiling: $1.00" in q
    assert "$0.05 already spent" in q
    assert "Heads-up" not in q      # 0.05 + 0.80 est high band stays under 1.00


def test_plan_warns_when_estimate_straddles_ceiling(tmp_path, monkeypatch):
    async def bomb(*a, **kw):
        raise AssertionError("no spend before approval")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"deliverable_intent": True,
                         "cost_ceiling_usd": 1.00, "spend_usd": 0.50})
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "x"}))
    q = op.record["needs_input"][-1]["question"]
    assert "Heads-up" in q
    assert "cancelling now costs nothing" in q.lower() or "cancelling now" in q


def test_plan_says_ceiling_off(tmp_path, monkeypatch):
    async def bomb(*a, **kw):
        raise AssertionError("no spend before approval")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"deliverable_intent": True})   # no fence on the record
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "x"}))
    q = op.record["needs_input"][-1]["question"]
    assert "Run ceiling: off." in q


# --------------------------------------------------------------------------
# Layer 1 — billable tools refuse at the fence, with ZERO new spend
# --------------------------------------------------------------------------

def test_research_refuses_at_ceiling_before_any_spend(tmp_path, monkeypatch):
    async def bomb(*a, **kw):
        raise AssertionError("spend past the ceiling")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"deliverable_intent": True, "research_plan_asked": True,
                         "research_approved": True,
                         "cost_ceiling_usd": 1.00, "spend_usd": 1.20})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("build_research_packet").call({"topic": "x"})))
    assert payload["reason"] == "cost_ceiling"
    assert "$1.20" in payload["error"] and "$1.00" in payload["error"]


def test_web_research_also_fenced(tmp_path, monkeypatch):
    async def bomb(*a, **kw):
        raise AssertionError("spend past the ceiling")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"deliverable_intent": True, "research_plan_asked": True,
                         "research_approved": True,
                         "cost_ceiling_usd": 0.50, "spend_usd": 0.50})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("web_research").call({"topic": "x"})))
    assert payload["reason"] == "cost_ceiling"


def test_script_tool_also_fenced(tmp_path, monkeypatch):
    async def bomb(*a, **kw):
        raise AssertionError("spend past the ceiling")

    monkeypatch.setattr(t, "run_text_agent", bomb)
    op = _ctx(tmp_path, {"cost_ceiling_usd": 1.00, "spend_usd": 1.00})
    set_current_operator(op)
    payload = json.loads(asyncio.run(t.write_audiobook_script.call({
        "sources_md": "### S\n"})))
    assert payload["reason"] == "cost_ceiling"


# --------------------------------------------------------------------------
# The ledger — every completed call's cost lands on the record
# --------------------------------------------------------------------------

def test_success_adds_spend_to_ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)

    async def fake(system, prompt, **kw):
        return TextAgentResult(
            text="**Audio Overview focus:** x.\n\n## S\nhttps://e.com\n\nS.\n",
            searches=8, restarts=0, tokens_in=150_000, tokens_out=8_000,
        )

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, {"deliverable_intent": True, "research_plan_asked": True,
                         "research_approved": True,
                         "cost_ceiling_usd": 1.00, "spend_usd": 0.10})
    set_current_operator(op)
    asyncio.run(_tool("build_research_packet").call({"topic": "x"}))
    # 0.10 prior + (8 searches + 150k in + 8k out at Sonnet intro) = 0.10 + 0.46
    assert op.record["spend_usd"] == pytest.approx(0.56)


def test_failed_call_partial_spend_lands_in_ledger_and_step(tmp_path, monkeypatch):
    async def fake(system, prompt, **kw):
        exc = RuntimeError("ReadTimeout")
        exc.ridian_partial = {"searches": 5, "tokens_in": 90_000,
                              "tokens_out": 0, "cost_usd": 0.23}
        raise exc

    monkeypatch.setattr(t, "run_text_agent", fake)
    op = _ctx(tmp_path, {"deliverable_intent": True, "research_plan_asked": True,
                         "research_approved": True,
                         "cost_ceiling_usd": 1.00, "spend_usd": 0.0})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("build_research_packet").call({"topic": "x"})))
    assert "error" in payload
    assert op.record["spend_usd"] == pytest.approx(0.23)   # the money is ON the ledger
    step = next(s for s in op.record["steps"] if s["name"] == "research_packet")
    assert step["status"] == "failed"
    assert "Partial spend before the failure ≈$0.23" in step["detail"]
    assert "5 searches" in step["detail"]
    assert "billed" in step["detail"]


# --------------------------------------------------------------------------
# Forensics — the runtime records what a dying run spent (it billed anyway)
# --------------------------------------------------------------------------

def test_runtime_attaches_partial_and_logs_on_failure(monkeypatch, caplog):
    fake = _FakeMessages([_DyingStream(
        events=[_start(_search_block()) for _ in range(3)],
        final=_final("end_turn", [_text("never")]),
    )])
    _patch(monkeypatch, fake)
    with caplog.at_level(logging.WARNING, logger="ridian.anthropic"):
        with pytest.raises(RuntimeError) as ei:
            asyncio.run(anthropic_runtime.run_text_agent(
                "sys", "hi", use_web_search=True, on_progress=_noop_progress,
            ))
    partial = ei.value.ridian_partial
    assert partial["searches"] == 3
    assert partial["cost_usd"] == pytest.approx(0.03)   # 3 × $0.01, no usage seen
    assert any("anthropic.run_failed" in r.message and "searches_attempted=3" in r.message
               for r in caplog.records)


# --------------------------------------------------------------------------
# Layer 2 — the live mid-stream guard (RunBudgetExceeded)
# --------------------------------------------------------------------------

def test_live_guard_aborts_on_input_tokens(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)
    fake = _FakeMessages([_FakeStream(
        events=[_msg_start(200_000)],   # 200k in at Sonnet intro = $0.40
        final=_final("end_turn", [_text("never")]),
    )])
    _patch(monkeypatch, fake)
    with pytest.raises(RunBudgetExceeded):
        asyncio.run(anthropic_runtime.run_text_agent(
            "sys", "hi", use_web_search=True, on_progress=_noop_progress,
            model="claude-sonnet-5", cost_ceiling=0.30,
        ))


def test_live_guard_counts_prior_run_spend(monkeypatch):
    """The fence is the RUN's, not the call's: prior spend + this call."""
    fake = _FakeMessages([_FakeStream(
        events=[_msg_start(50_000)],    # $0.10 alone — under a $0.50 fence
        final=_final("end_turn", [_text("never")]),
    )])
    _patch(monkeypatch, fake)
    with pytest.raises(RunBudgetExceeded):
        asyncio.run(anthropic_runtime.run_text_agent(
            "sys", "hi", use_web_search=True, on_progress=_noop_progress,
            model="claude-sonnet-5", cost_ceiling=0.50, spent_usd=0.45,
        ))


def test_live_guard_reads_output_token_deltas(monkeypatch):
    fake = _FakeMessages([_FakeStream(
        events=[_msg_start(10_000), _msg_delta(50_000)],   # $0.02 then +$0.50
        final=_final("end_turn", [_text("never")]),
    )])
    _patch(monkeypatch, fake)
    with pytest.raises(RunBudgetExceeded):
        asyncio.run(anthropic_runtime.run_text_agent(
            "sys", "hi", use_web_search=True, on_progress=_noop_progress,
            model="claude-sonnet-5", cost_ceiling=0.30,
        ))


def test_under_ceiling_run_completes_untouched(monkeypatch):
    fake = _FakeMessages([_FakeStream(
        events=[_msg_start(10_000), _start(_search_block()), _msg_delta(2_000)],
        final=_final("end_turn", [_text("FINAL")],
                     usage=SimpleNamespace(server_tool_use=SimpleNamespace(
                         web_search_requests=1), input_tokens=10_000,
                         output_tokens=2_000)),
    )])
    _patch(monkeypatch, fake)
    res = asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, return_stats=True,
        on_progress=_noop_progress, model="claude-sonnet-5", cost_ceiling=1.00,
    ))
    assert res.text == "FINAL"
    assert res.searches == 1


def test_ceiling_abort_through_real_tool_states_the_money(tmp_path, monkeypatch):
    """The whole chain: real registered tool → real run_text_agent → live
    guard abort → honest failed step with the partial spend stated, the money
    on the ledger, and a no-retry reason for the planner."""
    monkeypatch.delenv("ANTHROPIC_RESEARCH_MODEL", raising=False)
    fake = _FakeMessages([_FakeStream(
        events=[_msg_start(200_000)],
        final=_final("end_turn", [_text("never")]),
    )])
    _patch(monkeypatch, fake)
    op = _ctx(tmp_path, {"deliverable_intent": True, "research_plan_asked": True,
                         "research_approved": True,
                         "cost_ceiling_usd": 0.30, "spend_usd": 0.0})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("build_research_packet").call({"topic": "x"})))
    assert payload["reason"] == "cost_ceiling"
    assert "ceiling" in payload["error"]
    step = next(s for s in op.record["steps"] if s["name"] == "research_packet")
    assert step["status"] == "failed"
    assert "Partial spend before the failure" in step["detail"]
    assert op.record["spend_usd"] == pytest.approx(0.40)   # 200k in at $2/MTok
    assert op.record["errors"]
    assert not (Path(tmp_path) / "research_packet.md").exists()


# --------------------------------------------------------------------------
# Planner turns are inside the fence too
# --------------------------------------------------------------------------

def _planner_msg(tokens_in, tokens_out, stop_reason="tool_use"):
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text="hi")],
    )


def test_absorb_planner_spend_accumulates(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    op = _ctx(tmp_path, {"cost_ceiling_usd": 1.00, "spend_usd": 0.0})
    stop = asyncio.run(_absorb_planner_spend(op, _planner_msg(13_000, 500)))
    assert stop is False
    # Opus 4.8: 13k × $5/M + 500 × $25/M = 0.065 + 0.0125
    assert op.record["spend_usd"] == pytest.approx(0.0775)


def test_absorb_planner_spend_stops_runaway(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    op = _ctx(tmp_path, {"cost_ceiling_usd": 0.05, "spend_usd": 0.0})
    stop = asyncio.run(_absorb_planner_spend(op, _planner_msg(13_000, 500)))
    assert stop is True
    assert op.record["errors"]
    step = next(s for s in op.record["steps"] if s["name"] == "cost_ceiling")
    assert step["status"] == "failed"
    assert "$0.05" in step["detail"]


def test_absorb_planner_spend_lets_a_finished_run_close(tmp_path, monkeypatch):
    """A turn that just ENDED is let through even over the fence — stopping
    then would burn the receipt the money already paid for."""
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    op = _ctx(tmp_path, {"cost_ceiling_usd": 0.05, "spend_usd": 0.0})
    stop = asyncio.run(_absorb_planner_spend(
        op, _planner_msg(13_000, 500, stop_reason="end_turn")))
    assert stop is False
    assert not op.record["errors"]


class _FakeRunner:
    def __init__(self, messages):
        self._messages = list(messages)
        self.consumed = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        self.consumed += 1
        return self._messages.pop(0)

    async def generate_tool_call_response(self):
        return None


def test_run_turn_stops_planner_runaway_at_ceiling(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    runner = _FakeRunner([_planner_msg(13_000, 500), _planner_msg(13_000, 500)])
    monkeypatch.setattr(
        operator_service, "get_client",
        lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
            tool_runner=lambda **kw: runner))))
    op = _ctx(tmp_path, {"cost_ceiling_usd": 0.05, "spend_usd": 0.0})
    session = operator_service._OperationSession(
        operator=op, folder=Path(tmp_path), system="s", input_list=[],
        upload_state_line="")
    asyncio.run(operator_service._run_turn(
        session, [{"role": "user", "content": "go"}]))
    assert runner.consumed == 1          # stopped after the FIRST over-fence turn
    assert op.record["errors"]
    assert session.input_list             # history mirrored before the stop


# --------------------------------------------------------------------------
# Read timeout — streamed research only; the wall clock stays the bound
# --------------------------------------------------------------------------

def test_stream_requests_carry_the_long_read_timeout(monkeypatch):
    fake = _FakeMessages([_FakeStream(
        events=[], final=_final("end_turn", [_text("F")]),
    )])
    _patch(monkeypatch, fake)
    asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, on_progress=_noop_progress,
    ))
    timeout = fake.kwargs_seen[0]["timeout"]
    assert timeout.read == 660.0        # under the 720s wall clock
    assert timeout.connect == 5.0


def test_nonstream_requests_keep_default_timeout(monkeypatch):
    fake = _FakeMessages([])
    _patch(monkeypatch, fake)
    asyncio.run(anthropic_runtime.run_text_agent("sys", "hi"))
    assert "timeout" not in fake.kwargs_seen[0]
