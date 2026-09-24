"""Durable parked runs (v7.6, 0.9.16).

A run parked on a question or a gate approval stays resumable until the
owner answers, dismisses or cancels it — across the loss of its in-memory
session (a parked-session timeout) and across an app restart. A run that
truly cannot continue expires: failed with the reason, reported to the
site (awaiting_input / awaiting_approval -> failed), one notification, the
waiting badges cleared. Answering it says so and offers "Send again" —
never a bare error.

Incident (2026-09-24): op_3bb0dfb95e13, an Owner Workspace job, parked on a
question at 10:34:51. The app was quit and relaunched at 11:49:00 (same
0.9.14 install, no crash or sleep logged); the parked session lived only in
memory, so the 11:53 answer found none: "That operation is no longer
active".

The planner is scripted with REAL SDK blocks (thinking with its signature,
text, tool_use), so the restored conversation is checked exactly as the
model would receive it again.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock, BetaToolUseBlock
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services import (approval_inbox_service, jobs_service, operator_service,
                          parked_runs, push_service, state_store)
from app.services.operator_context import current_operator

# The Owner Workspace test site, clock and engine — and the jobs tests'
# isolation fixtures (autouse _isolated; fresh_notices), imported so they
# apply here exactly as there.
from test_owner_jobs import (PC, Clock, JobsSite, _connect, _engine, _isolated,  # noqa: F401
                             _job_op, fresh_notices)

COMMAND = "Draft a follow-up to Greg about the Navigator pilot"
QUESTION = "Which Greg: Greg Ortiz or Greg Lane?"


# ── The planner: the real run/continue loop, scripted turns, real SDK blocks ──

class _Runner:
    def __init__(self, turn) -> None:
        self._turn = turn
        self._done = False
        self._tool_result = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._done:
            raise StopAsyncIteration
        self._done = True
        content, self._tool_result = await self._turn()
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                               stop_reason="end_turn", content=content)

    async def generate_tool_call_response(self):
        return self._tool_result


def planner(monkeypatch, turns) -> list:
    """Each turn (the run, then each resume) is an async callable returning
    (assistant content blocks, the tool-result message or None). Returns
    what each turn was given: the system prompt and the messages."""
    monkeypatch.setattr(operator_service, "PLANNER_TOOLS", [])
    seen: list = []

    def tool_runner(**kw):
        turn = turns[len(seen)]
        seen.append({"system": kw["system"], "messages": list(kw["messages"])})
        return _Runner(turn)

    monkeypatch.setattr(operator_service, "get_client", lambda: SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(tool_runner=tool_runner))))
    return seen


ASSISTANT_BLOCKS = [
    {"type": "thinking", "thinking": "Two Gregs in contacts; ask which.", "signature": "sig-parked-01"},
    {"type": "text", "text": "Which Greg do you mean?"},
    {"type": "tool_use", "id": "toolu_parked01", "name": "request_missing_info",
     "input": {"question": QUESTION}},
]
TOOL_RESULT = {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "toolu_parked01", "content": "Asked the operator."}]}


async def _ask():
    """Park on a question — request_missing_info, nothing staged."""
    op = current_operator()
    op.record["test_gate_flag"] = {"set": "before the park", "ids": [3, 1]}
    op.sources_packet_text = "Navigator pilot notes"
    await op.emit_needs_input(question=QUESTION, context_hint="Follow-up to Greg about the Navigator pilot")
    return ([BetaThinkingBlock(type="thinking", thinking="Two Gregs in contacts; ask which.",
                               signature="sig-parked-01"),
             BetaTextBlock(type="text", text="Which Greg do you mean?"),
             BetaToolUseBlock(type="tool_use", id="toolu_parked01", name="request_missing_info",
                              input={"question": QUESTION})],
            TOOL_RESULT)


async def _gate():
    """Park on a gate approval: the approval is staged in the inbox."""
    op = current_operator()
    await op.emit_needs_input(question="Create this $250 invoice for Sandy Alvarez?",
                              context_hint="QuickBooks invoice — approval needed", buttons_only=True,
                              options=[{"label": "Approve", "action": "submit", "value": "approve"}])
    approval_inbox_service.stage_from_tool(
        "create_quickbooks_invoice", {"customer": "Sandy Alvarez"}, {"reason": "invoice_plan_pending"})
    return ([BetaTextBlock(type="text", text="Waiting for your approval of the invoice.")], None)


def _finisher(captured: dict):
    async def _finish():
        op = current_operator()
        captured["flag"] = op.record.get("test_gate_flag")
        captured["sources"] = op.sources_packet_text
        captured["state_during"] = (parked_runs.load(op.record["id"]) or {}).get("state")
        path = Path(op.folder) / "followup.docx"
        path.write_text("follow-up", encoding="utf-8")
        await op.emit_artifact(name=path.name, path=str(path), kind="docx")
        return ([BetaTextBlock(type="text", text="Drafted the follow-up to Greg Ortiz.")], None)
    return _finish


def _collector():
    events: list = []

    async def emit(event):
        events.append(event)
    return events, emit


def _restart() -> None:
    """What an app restart loses: every in-memory session and lock."""
    operator_service._SESSIONS.clear()
    operator_service._SESSION_LOCKS.clear()


def _op(operation_id: str) -> dict:
    return next(o for o in state_store.load_list("operations") if o.get("id") == operation_id)


# ---------------------------------------------------------------------------
# 1. Resumable across a parked-session timeout and across a restart
# ---------------------------------------------------------------------------

def test_park_then_timeout_then_answer_continues(monkeypatch):
    captured: dict = {}
    seen = planner(monkeypatch, [_ask, _finisher(captured)])
    events, emit = _collector()

    async def scenario():
        parked = await operator_service.run_operation(command=COMMAND, emit=emit)
        oid = parked["id"]
        assert parked["status"] == "awaiting_input"
        saved = parked_runs.load(oid)
        assert saved["state"] == "parked" and saved["folder"] == parked["artifact_folder"]
        # The parked-session timeout: the in-memory session is gone, the process lives on.
        operator_service._SESSIONS.pop(oid)
        operator_service._SESSION_LOCKS.pop(oid, None)
        events.clear()
        done = await operator_service.continue_operation(operation_id=oid, answer="Greg Ortiz", emit=emit)
        return oid, saved, done

    oid, saved, done = asyncio.run(scenario())
    assert done["status"] == "completed", "the run continued and finished"
    assert [e["event"] for e in events if e["event"] in ("error", "expired")] == []
    assert events[0] == {"event": "start", "data": {
        "id": oid, "command": "Greg Ortiz", "resumed": True,
        "artifact_folder": saved["folder"], "started_at": saved["record"]["started_at"]}}
    # The SAME conversation: the model's thinking (signature kept), text and
    # tool call, its tool result, then the answer — and the same system prompt.
    resumed = seen[1]["messages"]
    assert resumed[:-1] == saved["input_list"]
    assert resumed[1] == {"role": "assistant", "content": ASSISTANT_BLOCKS}
    assert resumed[2] == TOOL_RESULT
    assert resumed[-1]["role"] == "user" and resumed[-1]["content"].endswith(
        "The operator's answer to your question: Greg Ortiz")
    assert seen[1]["system"] == seen[0]["system"]
    # The record's gate state and the context caches came back.
    assert captured["flag"] == {"set": "before the park", "ids": [3, 1]}
    assert captured["sources"] == "Navigator pilot notes"
    assert captured["state_during"] == "resuming", "marked while the resumed turn runs"
    assert "followup.docx" in [a["name"] for a in done["artifacts"]]
    assert not parked_runs.exists(oid), "a finished run leaves no parked file"


def test_park_then_restart_then_answer_continues_and_the_site_hears_it(monkeypatch, fresh_notices):
    captured: dict = {}
    site = JobsSite().install(monkeypatch)
    _connect(site)
    seen = planner(monkeypatch, [_ask, _finisher(captured)])
    job_id = site.add_job(COMMAND)
    clock = Clock()
    events, emit = _collector()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        assert await engine.tick() == "reported"
        oid = _job_op(job_id)["id"]
        # The restart: sessions, locks and the engine are gone; the disk stays.
        engine.detach()
        _restart()
        assert operator_service.recover_parked_runs() == {"kept": [oid], "expired": [], "resumed": []}
        after = _engine(clock)
        after.recover()
        await after.tick()
        await operator_service.continue_operation(operation_id=oid, answer="Greg Ortiz", emit=emit)
        assert await after.tick() == "result_accepted"
        return oid

    oid = asyncio.run(scenario())
    assert [s["status"] for s in site.statuses[job_id]] == ["running", "awaiting_input", "running"]
    body = json.loads(site.results[job_id][0])
    assert body["status"] == "completed" and body["result"]["artifactNames"] == ["followup.docx"]
    assert body["result"]["replyText"] == "Drafted the follow-up to Greg Ortiz."
    assert seen[1]["messages"][1] == {"role": "assistant", "content": ASSISTANT_BLOCKS}
    assert captured["flag"] == {"set": "before the park", "ids": [3, 1]}
    assert captured["sources"] == "Navigator pilot notes"
    assert [e["event"] for e in events if e["event"] in ("error", "expired")] == []
    op = _op(oid)
    assert op["status"] == "completed" and op["source"] == "owner-workspace" and op["job_id"] == job_id
    assert not parked_runs.exists(oid)
    assert [n["kind"] for n in jobs_service.notices_after(0, "")["notices"]] == ["claimed", "parked"]


def test_a_run_that_parks_again_after_a_restart_stays_resumable(monkeypatch):
    """Answer after a restart, get asked again: the new park is saved too."""
    captured: dict = {}
    planner(monkeypatch, [_ask, _ask, _finisher(captured)])
    events, emit = _collector()

    async def scenario():
        oid = (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]
        _restart()
        again = await operator_service.continue_operation(operation_id=oid, answer="Greg", emit=emit)
        assert again["status"] == "awaiting_input"
        assert parked_runs.load(oid)["state"] == "parked"
        assert len(parked_runs.load(oid)["input_list"]) == 6
        _restart()
        return await operator_service.continue_operation(operation_id=oid, answer="Greg Ortiz", emit=emit)

    assert asyncio.run(scenario())["status"] == "completed"
    assert [e["event"] for e in events if e["event"] in ("error", "expired")] == []


# ---------------------------------------------------------------------------
# 2. A run that truly cannot continue expires — once
# ---------------------------------------------------------------------------

def test_an_unresumable_parked_job_fails_is_reported_and_notifies_once(monkeypatch, fresh_notices):
    """Today's incident, after the fix: the parked state is gone (the run
    parked before 0.9.16 saved it). The startup sweep marks it failed, the
    site hears failed, one notification, and the badge clears."""
    pushes: list = []
    monkeypatch.setattr(push_service, "notify_run_expired", lambda op: pushes.append(op.get("id")))
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_ask])
    job_id = site.add_job(COMMAND)
    clock = Clock()
    events, emit = _collector()
    pc = TestClient(app, client=PC)

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()
        oid = _job_op(job_id)["id"]
        assert pc.get("/approvals/questions").json()["count"] == 1
        parked_runs.delete(oid)
        engine.detach()
        _restart()
        assert operator_service.recover_parked_runs() == {"kept": [], "expired": [oid], "resumed": []}
        assert operator_service.recover_parked_runs() == {"kept": [], "expired": [], "resumed": []}
        after = _engine(clock)
        after.recover()
        assert await after.tick() == "result_accepted"
        # Answering it afterwards: the window is told, never a bare error.
        await operator_service.continue_operation(operation_id=oid, answer="Greg Ortiz", emit=emit)
        assert await after.tick() != "result_accepted"
        return oid

    oid = asyncio.run(scenario())
    assert [s["status"] for s in site.statuses[job_id]] == ["running", "awaiting_input"]
    assert len(site.results[job_id]) == 1
    body = json.loads(site.results[job_id][0])
    message = operator_service.expired_message("state_missing")
    assert body["status"] == "failed"
    assert body["result"]["status"] == "expired"
    assert body["result"]["replyText"] == message, "the reason, not the question it parked on"
    op = _op(oid)
    assert op["status"] == "failed" and op["awaiting_input"] is False
    assert op["expired"]["reason"] == "state_missing" and op["expired"]["message"] == message
    assert op["errors"][-1] == message
    assert [s["detail"] for s in op["steps"] if s["name"] == "expired"] == [message]
    folder_log = json.loads((Path(op["artifact_folder"]) / "operation_log.json").read_text(encoding="utf-8"))
    assert folder_log["status"] == "failed" and folder_log["awaiting_input"] is False
    assert folder_log["expired"]["message"] == message, "a reopened thread shows the ending"
    expired = [n for n in jobs_service.notices_after(0, "")["notices"] if n["kind"] == "expired"]
    assert len(expired) == 1, "one notification"
    assert expired[0]["operation_id"] == oid and expired[0]["job_id"] == job_id
    assert expired[0]["command"] == COMMAND and expired[0]["message"] == message
    assert pushes == [oid], "one phone push"
    assert pc.get("/approvals/questions").json()["count"] == 0, "the waiting badge clears"
    assert [e["event"] for e in events] == ["expired"]
    data = events[0]["data"]
    assert data["id"] == oid and data["command"] == COMMAND and data["expired"] is False
    assert data["status"] == "failed" and "already ended (failed)" in data["message"]
    assert data["message"].endswith("Send the command again if it is still needed.")


def test_an_answer_to_a_run_that_cannot_continue_expires_it_and_says_so(monkeypatch, fresh_notices):
    """A gate-approval park whose saved state is unreadable, answered in the
    thread: expired there and then, the approval voided, the site hears
    failed (from awaiting_approval), the window gets 'expired'."""
    pushes: list = []
    monkeypatch.setattr(push_service, "notify_run_expired", lambda op: pushes.append(op.get("id")))
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_gate])
    job_id = site.add_job("Invoice Sandy Alvarez $250 for the workshop")
    clock = Clock()
    events, emit = _collector()
    pc = TestClient(app, client=PC)

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()
        oid = _job_op(job_id)["id"]
        assert pc.get("/approvals").json()["count"] == 1
        (state_store.STATE_DIR / "parked" / f"{oid}.json").write_text("{not json", encoding="utf-8")
        operator_service._SESSIONS.pop(oid)
        await operator_service.continue_operation(operation_id=oid, answer="approve", emit=emit)
        assert await engine.tick() == "result_accepted"
        return oid

    oid = asyncio.run(scenario())
    message = operator_service.expired_message("state_unreadable")
    assert [e["event"] for e in events] == ["expired"]
    assert events[0]["data"] == {"id": oid, "command": "Invoice Sandy Alvarez $250 for the workshop",
                                 "status": "failed", "reason": "state_unreadable", "expired": True,
                                 "message": message}
    assert [s["status"] for s in site.statuses[job_id]] == ["running", "awaiting_approval"]
    body = json.loads(site.results[job_id][0])
    assert body["status"] == "failed" and body["result"]["replyText"] == message
    assert pc.get("/approvals").json()["count"] == 0, "the Approvals badge clears"
    voided = [a for a in state_store.load_list("approvals") if a.get("operation_id") == oid]
    assert [a["status"] for a in voided] == ["declined"] and "expired" in voided[0]["outcome"]
    assert not parked_runs.exists(oid)
    assert pushes == [oid]
    assert [n["kind"] for n in jobs_service.notices_after(0, "")["notices"]] == ["claimed", "parked", "expired"]


def test_a_restart_during_a_resumed_turn_expires_instead_of_replaying_the_question(monkeypatch):
    """The answer was accepted and the run was continuing when the app
    closed: it cannot pick up from the middle, and replaying from the
    question could repeat what it already did."""
    planner(monkeypatch, [_ask])
    events, emit = _collector()

    async def scenario():
        oid = (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]
        parked_runs.save(operator_service._SESSIONS[oid], parked_runs.RESUMING)
        _restart()
        assert operator_service.recover_parked_runs() == {"kept": [], "expired": [oid], "resumed": []}
        return oid

    oid = asyncio.run(scenario())
    op = _op(oid)
    assert op["status"] == "failed" and op["expired"]["reason"] == "interrupted"
    assert op["expired"]["message"] == operator_service.expired_message("interrupted")
    assert "cannot pick up from the middle" in op["expired"]["message"]


def test_answering_an_unknown_or_ended_run_never_gives_a_bare_error(monkeypatch):
    planner(monkeypatch, [_ask])
    events, emit = _collector()

    async def scenario():
        await operator_service.continue_operation(operation_id="op_nosuchrun01", answer="Greg", emit=emit)
        oid = (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]
        operator_service.dismiss_operation(oid)
        events.clear()
        await operator_service.continue_operation(operation_id=oid, answer="Greg", emit=emit)
        return oid

    oid = asyncio.run(scenario())
    assert [e["event"] for e in events] == ["expired"]
    assert events[0]["data"]["expired"] is False and events[0]["data"]["command"] == COMMAND
    assert "already ended (cancelled)" in events[0]["data"]["message"]
    assert _op(oid)["status"] == "cancelled", "an ended run is left as it ended"


# ---------------------------------------------------------------------------
# 3. The parked file's lifecycle, the startup sweep, the format
# ---------------------------------------------------------------------------

def test_dismiss_removes_the_parked_file_and_the_sweep_removes_strays(monkeypatch):
    planner(monkeypatch, [_ask])
    events, emit = _collector()

    async def scenario():
        return (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]

    oid = asyncio.run(scenario())
    assert parked_runs.exists(oid)
    operator_service.dismiss_operation(oid)
    assert not parked_runs.exists(oid) and _op(oid)["status"] == "cancelled"
    # A parked file left behind for a run that is no longer waiting.
    stray = SimpleNamespace(operator=SimpleNamespace(record={"id": oid}, sources_packet_text="", script_text=""),
                            folder=Path("C:/runs/x"), system="s", input_list=[], upload_state_line="")
    assert parked_runs.save(stray)
    assert operator_service.recover_parked_runs() == {"kept": [], "expired": [], "resumed": []}
    assert not parked_runs.exists(oid)


def test_the_app_startup_sweeps_parked_runs_before_the_jobs_engine(monkeypatch):
    order: list = []
    real_sweep = operator_service.recover_parked_runs
    monkeypatch.setattr(operator_service, "recover_parked_runs", lambda: order.append("sweep") or real_sweep())
    monkeypatch.setattr(jobs_service, "start_engine", lambda: order.append("jobs"))
    state_store.save("operations", [{"id": "op_legacypark1", "command": COMMAND, "status": "awaiting_input",
                                     "awaiting_input": True, "needs_input": [{"question": QUESTION}],
                                     "steps": [], "errors": [], "artifact_folder": ""}])
    with TestClient(main_module.app, client=PC):
        pass
    assert order == ["sweep", "jobs"]
    assert _op("op_legacypark1")["status"] == "failed"


def test_the_parked_file_is_plain_json_and_its_path_is_confined():
    record = {"id": "op_format00001", "tags": {"b", "a"}, "where": Path("C:/runs/y"),
              "nested": ({"n": 1},)}
    blocks = [BetaThinkingBlock(type="thinking", thinking="t", signature="s1"),
              BetaTextBlock(type="text", text="hi")]
    assert parked_runs.json_safe(record) == {"id": "op_format00001", "tags": ["a", "b"],
                                             "where": str(Path("C:/runs/y")), "nested": [{"n": 1}]}
    assert parked_runs.json_safe(blocks) == [{"type": "thinking", "thinking": "t", "signature": "s1"},
                                             {"type": "text", "text": "hi"}]
    for bad in ("../escape", "a/b", "", ".hidden", "x" * 65):
        assert parked_runs._path(bad) is None
    assert parked_runs.load("../escape") is None
