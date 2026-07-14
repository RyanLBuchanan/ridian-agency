"""Live per-search progress — research no longer runs silent.

With on_progress set, run_text_agent streams and fires ("search", n) as each
server-side search starts and ("writing", n) when text follows a search,
counting across pause_turn restarts. The research tools surface those as
in-place step-detail updates through the same SSE events every tool uses.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.services import anthropic_runtime
from app.services import operator_tools as t
from app.services.anthropic_runtime import TextAgentResult
from app.services.operator_context import OperatorContext, set_current_operator


def _text(txt):
    return SimpleNamespace(type="text", text=txt)


def _search():
    return SimpleNamespace(type="server_tool_use")


def _search_result():
    return SimpleNamespace(type="web_search_tool_result")


def _final(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def _start_event(block):
    return SimpleNamespace(type="content_block_start", content_block=block)


class _FakeStream:
    """Mimics client.messages.stream(): async context manager that yields
    events, then hands back the final message."""

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


class _FakeStreamingMessages:
    def __init__(self, streams):
        self._streams = list(streams)
        self.stream_calls = 0
        self.create_calls = 0

    def stream(self, **kwargs):
        self.stream_calls += 1
        return self._streams.pop(0)

    async def create(self, **kwargs):
        self.create_calls += 1
        raise AssertionError("create() used despite on_progress — no streaming, no progress")


def _patch(monkeypatch, fake):
    monkeypatch.setattr(anthropic_runtime, "get_client",
                        lambda: SimpleNamespace(messages=fake))


def test_progress_fires_per_search_and_on_writing(monkeypatch):
    fake = _FakeStreamingMessages([_FakeStream(
        events=[
            _start_event(_text("planning")),        # text before any search: silent
            _start_event(_search()),                # → ("search", 1)
            _start_event(_search()),                # → ("search", 2)
            _start_event(_text("writing it up")),   # → ("writing", 2)
        ],
        final=_final("end_turn", _text("n"), _search(), _search_result(),
                     _search(), _search_result(), _text("FINAL")),
    )])
    _patch(monkeypatch, fake)
    seen = []

    async def on_progress(phase, n):
        seen.append((phase, n))

    res = asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, return_stats=True,
        on_progress=on_progress,
    ))
    assert seen == [("search", 1), ("search", 2), ("writing", 2)]
    assert res.text == "FINAL"      # extraction semantics unchanged
    assert res.searches == 2        # stats still counted from final blocks
    assert fake.stream_calls == 1 and fake.create_calls == 0


def test_progress_counter_survives_pause_restart(monkeypatch):
    fake = _FakeStreamingMessages([
        _FakeStream(
            events=[_start_event(_search())],
            final=_final("pause_turn", _text("n1"), _search()),
        ),
        _FakeStream(
            events=[_start_event(_search()), _start_event(_text("w"))],
            final=_final("end_turn", _search_result(), _search(),
                         _search_result(), _text("done")),
        ),
    ])
    _patch(monkeypatch, fake)
    seen = []

    async def on_progress(phase, n):
        seen.append((phase, n))

    out = asyncio.run(anthropic_runtime.run_text_agent(
        "sys", "hi", use_web_search=True, on_progress=on_progress,
    ))
    assert out == "done"
    assert seen == [("search", 1), ("search", 2), ("writing", 2)]
    assert fake.stream_calls == 2


def test_tool_surfaces_progress_as_step_updates(tmp_path, monkeypatch):
    """Through the REAL registered tool: the on_progress it passes updates the
    run's own step detail in place — the renderer's existing step row."""
    op_holder = {}
    observed = []

    async def fake(system, prompt, **kw):
        await kw["on_progress"]("search", 3)
        step = next(s for s in op_holder["op"].record["steps"]
                    if s["name"] == "research_packet")
        observed.append(step["detail"])
        await kw["on_progress"]("writing", 3)
        step = next(s for s in op_holder["op"].record["steps"]
                    if s["name"] == "research_packet")
        observed.append(step["detail"])
        return TextAgentResult(text="## S\nhttps://e.com\n\nx.\n", searches=3, restarts=0)

    monkeypatch.setattr(t, "run_text_agent", fake)

    async def _emit(_ev):
        return None

    record = {"deliverable_intent": True, "research_plan_asked": True,
              "research_approved": True,
              "steps": [], "tools_used": [], "artifacts": [], "errors": []}
    op = OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)
    op_holder["op"] = op
    set_current_operator(op)
    tool = next(x for x in t.PLANNER_TOOLS if x.name == "build_research_packet")
    asyncio.run(tool.call({"topic": "x"}))
    assert observed == [
        "Searching 3 of up to 8…",
        "Reading results and writing… (3 searches so far)",
    ]
    # completion overwrites the detail afterwards, same step row throughout
    step = next(s for s in record["steps"] if s["name"] == "research_packet")
    assert step["status"] == "completed"
