"""In-flight runs survive closing the app (v7.8, 0.9.18).

Closing the app used to kill the backend at once (taskkill /T /F) with runs
in flight. Now Electron's before-quit asks the backend to drain first
(POST /app/drain): nothing new starts, and each run in flight finishes its
current step — the model turn and the tools it called — and is checkpointed
at that boundary. After the restart it resumes from exactly there: the same
conversation, record and context, no new message. A run still inside a step
when the grace runs out (or when the app is killed) was marked "running"
when it started, so the restart expires it honestly — on record, reported
to the site, notified — as parked runs that cannot continue do today.
"""
import asyncio
import inspect
import json
from pathlib import Path

import pytest

from anthropic.types.beta import BetaTextBlock, BetaToolUseBlock
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services import jobs_service, operator_service, parked_runs, push_service, state_store
from app.services.operator_context import current_operator

from test_owner_jobs import PC, Clock, JobsSite, _connect, _engine, _isolated, _job_op, fresh_notices  # noqa: F401
from test_parked_runs import _collector, planner

COMMAND = "Build a research packet on the Navigator pilot for Greg"
TOOL_USE = {"type": "tool_use", "id": "toolu_ck01", "name": "research_topic", "input": {"topic": "Navigator pilot"}}
TOOL_RESULT = {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "toolu_ck01", "content": "Found 3 sources."}]}


def _working_step(gate=None, started=None):
    """A step that is not the run's last: the model calls a tool. ``gate``
    holds the step open (the app closes while it runs)."""
    async def turn():
        op = current_operator()
        await op.emit_step(name="research", status="running", detail="Researching the Navigator pilot")
        if started is not None:
            started.set()
        if gate is not None:
            await gate.wait()
        op.record["test_progress"] = "step one done"
        await op.emit_step(name="research", status="completed", detail="3 sources")
        return ([BetaTextBlock(type="text", text="Researching first."),
                 BetaToolUseBlock(type="tool_use", id="toolu_ck01", name="research_topic",
                                  input={"topic": "Navigator pilot"})],
                TOOL_RESULT)
    return turn


def _last_step(captured: dict):
    async def turn():
        op = current_operator()
        captured["progress"] = op.record.get("test_progress")
        path = Path(op.folder) / "research_packet.md"
        path.write_text("# Navigator pilot", encoding="utf-8")
        await op.emit_step(name="research_packet", status="running", detail="Writing")
        await op.emit_artifact(name=path.name, path=str(path), kind="markdown")
        await op.emit_step(name="research_packet", status="completed", detail="Written")
        return ([BetaTextBlock(type="text", text="The research packet is ready.")], None)
    return turn


@pytest.fixture(autouse=True)
def _not_closing():
    yield
    operator_service.end_drain()
    operator_service._IN_FLIGHT.clear()


def _restart() -> None:
    """What the process loses when the app closes: every session in memory."""
    operator_service._SESSIONS.clear()
    operator_service._SESSION_LOCKS.clear()
    operator_service._IN_FLIGHT.clear()
    operator_service.end_drain()


def _op(operation_id: str):
    return next((o for o in state_store.load_list("operations") if o.get("id") == operation_id), None)


# ---------------------------------------------------------------------------
# 1. Closed between steps: checkpointed, resumed after the restart
# ---------------------------------------------------------------------------

def test_a_run_closed_mid_step_is_checkpointed_after_the_step_and_resumes(monkeypatch):
    captured: dict = {}
    gate, started = asyncio.Event(), asyncio.Event()
    planner(monkeypatch, [_working_step(gate, started)])
    events, emit = _collector()

    async def scenario():
        run = asyncio.get_running_loop().create_task(
            operator_service.run_operation(command=COMMAND, emit=emit))
        await started.wait()
        oid = next(iter(operator_service._IN_FLIGHT))
        assert parked_runs.load(oid)["state"] == "running", "on disk from the start"
        # The app closes while the step runs: the drain waits for the step.
        drain = asyncio.get_running_loop().create_task(operator_service.drain(grace=5))
        await asyncio.sleep(0.1)
        assert not drain.done(), "the drain waits for the step in flight"
        gate.set()
        drained = await drain
        out = await run
        return oid, drained, out

    oid, drained, out = asyncio.run(scenario())
    assert drained == {"checkpointed": [oid], "mid_step": []}
    assert out == {"id": oid, "checkpointed": True}
    assert [e["event"] for e in events].count("checkpointed") == 1
    saved = parked_runs.load(oid)
    assert saved["state"] == "checkpoint"
    assert saved["input_list"][1:] == [
        {"role": "assistant", "content": [{"type": "text", "text": "Researching first."}, TOOL_USE]},
        TOOL_RESULT], "the step's model turn and its tool result are kept"
    assert _op(oid) is None, "not finalized: it is not over"

    # The restart: the sweep rebuilds it; the lifespan continues it.
    _restart()
    swept = operator_service.recover_parked_runs()
    assert swept == {"kept": [], "expired": [], "resumed": [oid]}
    assert operator_service.live_state(oid)["status"] == "running"
    history = list(saved["input_list"])
    seen2 = planner(monkeypatch, [_last_step(captured)])
    done = asyncio.run(operator_service.resume_checkpointed(oid))
    assert seen2[0]["messages"] == history, "resumed from exactly the boundary — no new message"
    assert captured["progress"] == "step one done", "the record came back with the step's work"
    assert done["status"] == "completed"
    assert "research_packet.md" in [a["name"] for a in done["artifacts"]]
    assert _op(oid)["status"] == "completed"
    assert not parked_runs.exists(oid)


def test_a_job_run_closed_between_steps_resumes_and_the_site_hears_it(monkeypatch, fresh_notices):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    captured: dict = {}

    async def closing_step():
        await operator_service.drain(grace=0)       # the app starts closing during this step
        return await _working_step()()

    planner(monkeypatch, [closing_step])
    job_id = site.add_job(COMMAND)
    clock = Clock()

    async def before():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        assert await engine.tick() == "reported"
        engine.detach()

    asyncio.run(before())
    oid = jobs_service.current_job()["operation_id"]
    assert parked_runs.load(oid)["state"] == "checkpoint"
    _restart()
    assert operator_service.recover_parked_runs()["resumed"] == [oid]
    planner(monkeypatch, [_last_step(captured)])

    async def after():
        engine = _engine(clock)
        engine.recover()
        assert await engine.tick() == "busy", "the job is kept, never reported interrupted"
        await operator_service.resume_checkpointed(oid)
        assert await engine.tick() == "result_accepted"

    asyncio.run(after())
    assert [s["status"] for s in site.statuses[job_id]] == ["running"]
    body = json.loads(site.results[job_id][0])
    assert body["status"] == "completed" and body["result"]["replyText"] == "The research packet is ready."
    resumed = [n for n in jobs_service.notices_after(0, "")["notices"] if n["kind"] == "resumed"]
    assert len(resumed) == 1 and resumed[0]["operation_id"] == oid and resumed[0]["command"] == COMMAND
    assert [e["event"] for e in jobs_service.job_events(oid, 0)["events"]][:1] == ["start"], "the window can follow it"


# ---------------------------------------------------------------------------
# 2. Closed in the middle of a step: expired honestly, as today
# ---------------------------------------------------------------------------

def test_a_run_still_mid_step_when_the_app_closes_expires_honestly(monkeypatch, fresh_notices):
    pushes: list = []
    monkeypatch.setattr(push_service, "notify_run_expired", lambda op: pushes.append(op.get("id")))
    site = JobsSite().install(monkeypatch)
    _connect(site)
    never, started = asyncio.Event(), asyncio.Event()
    planner(monkeypatch, [_working_step(never, started)])
    job_id = site.add_job(COMMAND)
    clock = Clock()

    async def before():
        engine = _engine(clock)
        await engine.tick()
        await started.wait()
        drained = await operator_service.drain(grace=0.1)
        engine._task.cancel()                       # the app is killed mid-step
        try:
            await engine._task
        except asyncio.CancelledError:
            pass
        engine.detach()
        return drained

    drained = asyncio.run(before())
    oid = jobs_service.current_job()["operation_id"]
    assert drained == {"checkpointed": [], "mid_step": [oid]}
    assert parked_runs.load(oid)["state"] == "running" and _op(oid) is None
    _restart()
    swept = operator_service.recover_parked_runs()
    assert swept == {"kept": [], "expired": [oid], "resumed": []}
    op = _op(oid)
    message = operator_service.expired_message("mid_step")
    assert op["status"] == "failed" and op["expired"]["reason"] == "mid_step"
    assert op["expired"]["message"] == message and op["command"] == COMMAND and op["job_id"] == job_id
    assert "in the middle of one of this run's steps" in message
    folder_log = json.loads((Path(op["artifact_folder"]) / "operation_log.json").read_text(encoding="utf-8"))
    assert folder_log["status"] == "failed", "a reopened thread shows the ending"
    assert not parked_runs.exists(oid)

    async def after():
        engine = _engine(clock)
        engine.recover()
        assert await engine.tick() == "result_accepted"

    asyncio.run(after())
    body = json.loads(site.results[job_id][0])
    assert body["status"] == "failed" and body["result"]["status"] == "expired"
    assert body["result"]["replyText"] == message
    assert pushes == [oid]
    assert [n["kind"] for n in jobs_service.notices_after(0, "")["notices"]].count("expired") == 1
    assert operator_service.recover_parked_runs() == {"kept": [], "expired": [], "resumed": []}, "once"


# ---------------------------------------------------------------------------
# 3. While closing, nothing new starts; the desktop app drains before it kills
# ---------------------------------------------------------------------------

def test_while_closing_nothing_new_starts(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    site.add_job(COMMAND)
    planner(monkeypatch, [_working_step()])
    events, emit = _collector()

    async def scenario():
        await operator_service.drain(grace=0)
        out = await operator_service.run_operation(command=COMMAND, emit=emit)
        engine = _engine(Clock())
        tick = await engine.tick()
        return out, tick

    out, tick = asyncio.run(scenario())
    assert out == {} and events[-1]["event"] == "error" and "closing" in events[-1]["data"]["message"]
    assert tick == "draining" and site.of("claim") == [], "no job is claimed while closing"
    assert parked_runs.list_ids() == []


def test_the_desktop_app_drains_the_backend_before_killing_it():
    main_js = (Path(__file__).resolve().parents[3] / "desktop" / "main.js").read_text(encoding="utf-8")
    before = main_js.split("app.on('before-quit'", 1)[1].split("app.on('will-quit'", 1)[0]
    assert "e.preventDefault()" in before and "drainBackend().finally(() => app.quit())" in before
    drain = main_js.split("async function drainBackend()", 1)[1].split("\n}", 1)[0]
    assert "/app/drain" in drain and "method: 'POST'" in drain
    timeout_ms = int(main_js.split("const DRAIN_TIMEOUT_MS = ", 1)[1].split(";", 1)[0])
    assert timeout_ms > operator_service.DRAIN_GRACE_SECONDS * 1000, "the backend gets its whole grace"
    will_quit = main_js.split("app.on('will-quit'", 1)[1].split("});", 1)[0]
    assert "stopBackend()" in will_quit
    assert "_require_loopback(request)" in inspect.getsource(main_module.app_drain)
    lifespan = inspect.getsource(main_module._lifespan)
    assert lifespan.index("recover_parked_runs()") < lifespan.index("resume_checkpointed(")
    lan = TestClient(app, base_url="http://192.168.1.7:8000", client=("192.168.1.50", 40001))
    assert lan.post("/app/drain").status_code in (401, 403), "never from the phone or the LAN"
