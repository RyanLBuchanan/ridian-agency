"""Operation SSE stream robustness — heartbeats, crash logging, clean close.

A live run failed in the renderer at ~3.5 minutes while the backend was still
mid-research: the stream sends zero bytes during a long tool call, so any
idle-socket kill between Electron and uvicorn reports a healthy run as
Failed, and a detached runner task's traceback died invisibly on stderr.
These tests pin the fixes: comment-line heartbeats while the queue is quiet,
runner exceptions logged (never lost), and the stream always closing with an
``end`` event.
"""
import asyncio
import logging

from app import main as m
from app.services import anthropic_runtime


def _collect(run):
    async def go():
        resp = m._operation_sse(run)
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return "".join(chunks)
    return asyncio.run(go())


def test_sse_heartbeats_during_silent_tool_call(monkeypatch):
    monkeypatch.setattr(m, "_SSE_HEARTBEAT_SECONDS", 0.02)

    async def slow_run(emit):
        await emit({"event": "step", "data": {"name": "research"}})
        await asyncio.sleep(0.15)   # a long, silent tool call

    out = _collect(slow_run)
    assert out.startswith(": connected\n\n")
    assert ": ping\n\n" in out              # bytes flowed during the silence
    assert "event: step" in out
    assert out.rstrip().endswith("event: end\ndata: {}")


def test_sse_no_heartbeat_needed_on_fast_run():
    async def fast_run(emit):
        await emit({"event": "complete", "data": {"status": "completed"}})

    out = _collect(fast_run)
    assert "event: complete" in out
    assert out.rstrip().endswith("event: end\ndata: {}")


def test_sse_runner_crash_is_logged_and_stream_closes(caplog):
    async def bad_run(emit):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        out = _collect(bad_run)
    # the stream still closes with end (renderer shows Failed, not a hang) …
    assert out.rstrip().endswith("event: end\ndata: {}")
    # … and the traceback is persisted through logging, not lost on stderr
    assert any("operation runner failed" in r.message for r in caplog.records)
    assert any(r.exc_info for r in caplog.records)


def test_anthropic_client_is_timeout_bounded(monkeypatch):
    """A wedged API request must fail within a bounded window instead of
    grinding through SDK-default long timeouts x retries."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic_runtime, "_client", None)
    monkeypatch.setattr(anthropic_runtime, "_client_key", None)
    client = anthropic_runtime.get_client()
    assert client.max_retries == anthropic_runtime._MAX_REQUEST_RETRIES == 1
    assert client.timeout == anthropic_runtime._REQUEST_TIMEOUT_SECONDS
