"""A run's status is its live state, never whether its folder could be read
(v7.7, 0.9.17) — and the phone notifications a run raised are withdrawn once
it is answered, cancelled or expired.

Incident (2026-09-24): job e52e4f29 (op_5d6d24ad87a7, a research packet) was
claimed at 14:25:21.777 and first wrote operation_log.json when it parked
on its research-plan approval at 14:25:31.27. Opened in between, the pane
read the folder, found no log and painted the live run "Failed — Could not
rehydrate this operator run". The auto-open on claim had stopped following
at once: the pane still held the 14:23 job run (op_5d8e6e5c475d), which
the first live tick took for "the window moved on".

GET /operations/live answers from the live session first, then the
operations store; the window takes the pane's status from it. The
renderer half is the "Open right after claim (real DOM)" harness section.
"""
import asyncio
import inspect
import json
import time
from pathlib import Path

from anthropic.types.beta import BetaTextBlock
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services import (approval_inbox_service, companion_service, jobs_service,
                          operator_service, push_service, settings_service, state_store)
from app.services.operator_context import current_operator

# The jobs site, clock, engine and isolation fixtures, and the scripted
# planner with real SDK blocks — imported so they apply here too.
from test_owner_jobs import PC, Clock, JobsSite, _connect, _engine, _isolated, _job_op  # noqa: F401
from test_parked_runs import COMMAND, QUESTION, _ask, _collector, _finisher, planner


def _live(client, **params) -> dict:
    return client.get("/operations/live", params=params).json()


# ---------------------------------------------------------------------------
# 1. Opened within 100 ms of the claim: running, never failed
# ---------------------------------------------------------------------------

def test_a_run_opened_right_after_claim_is_running_never_failed(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    claimed_at: dict = {}
    seen_while_alive: list = []
    real_started = jobs_service.JobsEngine._job_started

    def stamp(self, job_id, operation_id, command="", folder=""):
        claimed_at["t"] = time.monotonic()
        return real_started(self, job_id, operation_id, command, folder)

    monkeypatch.setattr(jobs_service.JobsEngine, "_job_started", stamp)
    pc = TestClient(app, client=PC)

    async def first_turn():
        # The run is alive and has not parked: open it the way the window does.
        op = current_operator()
        folder = str(op.folder)
        opened = time.monotonic() - claimed_at["t"]
        load = pc.get("/operations/load", params={"artifact_folder": folder}).json()
        by_id = _live(pc, operation_id=op.record["id"], artifact_folder=folder)
        by_folder = _live(pc, artifact_folder=folder)
        seen_while_alive.append({"opened": opened, "log": load["operation_log"], "by_id": by_id, "by_folder": by_folder})
        return await _ask()

    planner(monkeypatch, [first_turn, _finisher({})])
    job_id = site.add_job(COMMAND)
    events, emit = _collector()

    async def scenario():
        engine = _engine(Clock())
        await engine.tick()
        await engine.wait_for_job()
        oid = _job_op(job_id)["id"]
        parked = _live(pc, operation_id=oid)
        await operator_service.continue_operation(operation_id=oid, answer="Greg Ortiz", emit=emit)
        return oid, parked

    oid, parked = asyncio.run(scenario())
    (alive,) = seen_while_alive
    assert alive["opened"] < 0.1, f"opened {alive['opened'] * 1000:.0f} ms after the claim"
    assert alive["log"] is None, "the run folder has no operation_log.json before the first park"
    for live in (alive["by_id"], alive["by_folder"]):
        assert live["known"] is True and live["live"] is True, live
        assert live["status"] == "running", live
        assert live["id"] == oid and live["source"] == "owner-workspace" and live["command"] == COMMAND
    assert parked["status"] == "awaiting_input" and parked["pending"]["question"] == QUESTION
    assert _live(pc, operation_id=oid)["status"] == "completed"
    assert _live(pc, operation_id="op_nosuchrun01") == {
        "id": "op_nosuchrun01", "known": False, "status": "unknown", "live": False, "command": "",
        "source": "", "artifact_folder": "", "pending": None}


def test_the_live_state_wins_over_the_folder(monkeypatch):
    """A run resumed since its last park is running while its folder still
    says awaiting_input; a dismissed run is cancelled while its folder log
    still says awaiting_input (dismiss never rewrote it)."""
    seen: list = []
    pc = TestClient(app, client=PC)

    async def resumed_turn():
        op = current_operator()
        log = json.loads((Path(op.folder) / "operation_log.json").read_text(encoding="utf-8"))
        seen.append((log["status"], _live(pc, operation_id=op.record["id"])["status"]))
        return ([BetaTextBlock(type="text", text="Done.")], None)

    planner(monkeypatch, [_ask, resumed_turn, _ask])
    events, emit = _collector()

    async def scenario():
        first = await operator_service.run_operation(command=COMMAND, emit=emit)
        await operator_service.continue_operation(operation_id=first["id"], answer="Greg Ortiz", emit=emit)
        second = await operator_service.run_operation(command=COMMAND, emit=emit)
        return second

    second = asyncio.run(scenario())
    assert seen == [("awaiting_input", "running")]
    operator_service.dismiss_operation(second["id"])
    folder_log = json.loads((Path(second["artifact_folder"]) / "operation_log.json").read_text(encoding="utf-8"))
    assert folder_log["status"] == "awaiting_input"
    assert _live(pc, artifact_folder=second["artifact_folder"])["status"] == "cancelled"


def test_the_live_state_is_pc_only():
    assert not companion_service.device_request_allowed("GET", "/operations/live")
    assert companion_service.device_request_allowed("GET", "/operations/op_abc123"), "one record stays readable"
    assert "_require_loopback(request)" in inspect.getsource(main_module.operations_live)
    settings_service.save_settings({"companion_enabled": "true"})
    pc = TestClient(app, client=PC)
    code = pc.post("/companion/pairing-code").json()["code"]
    lan = TestClient(app, base_url="http://192.168.1.7:8000", client=("192.168.1.50", 40001))
    assert lan.post("/companion/pair", headers={"X-Ridian-Companion": "1"},
                    json={"code": code, "device_name": "Pixel 7"}).status_code == 200
    assert lan.get("/operations/live", params={"operation_id": "op_x"}).status_code == 403


# ---------------------------------------------------------------------------
# 2. Phone notifications are withdrawn when their run is answered,
#    cancelled or expired
# ---------------------------------------------------------------------------

def _push_on(monkeypatch) -> list:
    settings_service.save_settings({"companion_push_enabled": "true"})
    monkeypatch.setattr(push_service, "VAPID_PATH", state_store.STATE_DIR.parent / "vapid.bin")
    push_service.ensure_vapid()
    state_store.save("companion_devices", [{
        "id": "cd_test000001", "name": "Pixel 7", "token_sha256": "irrelevant", "created_iso": "2026-09-24T09:00:00",
        "push_subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                              "keys": {"p256dh": "pk", "auth": "ak"}}}])
    sent: list = []
    monkeypatch.setattr(push_service, "webpush", lambda **kw: sent.append(json.loads(kw["data"])) or "ok")
    monkeypatch.setattr(push_service, "_spawn", lambda fn, *args: fn(*args))   # synchronous
    push_service._inflight.clear()
    return sent


def _ledger() -> dict:
    return state_store.load_dict("companion_push_ledger")


def test_answering_a_parked_run_withdraws_its_phone_notification_once(monkeypatch):
    sent = _push_on(monkeypatch)
    planner(monkeypatch, [_ask, _ask, _finisher({})])
    events, emit = _collector()

    async def scenario():
        oid = (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]
        assert [p.get("tag") for p in sent] == [f"op:{oid}:q1"], "the park notified"
        await operator_service.continue_operation(operation_id=oid, answer="Greg", emit=emit)
        assert sent[1] == {"withdraw": [f"op:{oid}:q1"], "tag": f"wd:{oid}"}, "withdrawn, with nothing to show"
        assert sent[2]["tag"] == f"op:{oid}:q2", "asked again: a new notification"
        await operator_service.continue_operation(operation_id=oid, answer="Greg Ortiz", emit=emit)
        return oid

    oid = asyncio.run(scenario())
    assert sent[3] == {"withdraw": [f"op:{oid}:q2"], "tag": f"wd:{oid}"}, "only what is still showing"
    assert len(sent) == 4
    assert {f"wd:op:{oid}:q1", f"wd:op:{oid}:q2"} <= set(_ledger())
    push_service._withdraw(oid, "answered")
    assert len(sent) == 4, "once"


def test_cancelling_or_answering_from_the_inbox_withdraws_too(monkeypatch):
    sent = _push_on(monkeypatch)
    planner(monkeypatch, [_ask])
    events, emit = _collector()

    async def scenario():
        return (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]

    oid = asyncio.run(scenario())
    operator_service.dismiss_operation(oid)
    assert sent[-1] == {"withdraw": [f"op:{oid}:q1"], "tag": f"wd:{oid}"}

    # An approval answered from the inbox (the PC's or the phone's):
    # its approval notification and its park notification are withdrawn.
    from app.services.operator_tools import RESTORE_CANCEL
    state_store.save("operations", [{"id": "op_inbox000001", "command": "Restore Tuesday's backup",
                                     "status": "awaiting_input", "needs_input": [{"question": "Restore?"}]}])
    state_store.save("approvals", [{"id": "appr_inbox0001", "operation_id": "op_inbox000001", "status": "pending",
                                    "command": "Restore Tuesday's backup", "tool": "restore_backup", "kwargs": {},
                                    "reason": "restore_pending", "question": "Restore?",
                                    "options": [{"label": "Cancel", "value": RESTORE_CANCEL}],
                                    "gate_flags": {"restore_asked": True}, "folder": str(state_store.STATE_DIR),
                                    "staged_at": "2026-09-24T14:25:27"}])
    ledger = _ledger()
    ledger.update({"appr:appr_inbox0001": "2026-09-24T14:25:28", "op:op_inbox000001:q1": "2026-09-24T14:25:31"})
    state_store.save("companion_push_ledger", ledger)
    out = asyncio.run(approval_inbox_service.answer_approval("appr_inbox0001", RESTORE_CANCEL))
    assert out.get("declined") is True, out
    assert sent[-1] == {"withdraw": ["appr:appr_inbox0001", "op:op_inbox000001:q1"], "tag": "wd:op_inbox000001"}


def test_an_expired_run_withdraws_as_it_says_so(monkeypatch):
    sent = _push_on(monkeypatch)
    planner(monkeypatch, [_ask])
    events, emit = _collector()

    async def scenario():
        return (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]

    oid = asyncio.run(scenario())
    operator_service._SESSIONS.clear()
    operator_service._SESSION_LOCKS.clear()
    from app.services import parked_runs
    parked_runs.delete(oid)
    assert operator_service.recover_parked_runs()["expired"] == [oid]
    expired = sent[-1]
    assert expired["tag"] == f"op:{oid}:expired" and expired["title"] == "Ridian couldn't continue"
    assert expired["withdraw"] == [f"op:{oid}:q1"], "the stale 'waiting on you' closes as this shows"
    assert f"wd:op:{oid}:q1" in _ledger()
    push_service._withdraw(oid, "expired")
    assert sent[-1] is expired, "nothing left to withdraw"


def test_nothing_is_withdrawn_that_was_never_delivered(monkeypatch):
    """The phone is subscribed, but the park's push was refused: nothing was
    shown there, so cancelling the run sends nothing to withdraw."""
    _push_on(monkeypatch)
    attempts: list = []

    def refuse_then_accept(**kw):
        attempts.append(json.loads(kw["data"]))
        if len(attempts) == 1:
            raise push_service.WebPushException("refused")
        return "ok"

    monkeypatch.setattr(push_service, "webpush", refuse_then_accept)
    planner(monkeypatch, [_ask])
    events, emit = _collector()

    async def scenario():
        return (await operator_service.run_operation(command=COMMAND, emit=emit))["id"]

    oid = asyncio.run(scenario())
    assert [a.get("tag") for a in attempts] == [f"op:{oid}:q1"] and f"op:{oid}:q1" not in _ledger()
    operator_service.dismiss_operation(oid)
    assert len(attempts) == 1, "no withdrawal of a notification that was never delivered"
    assert not any(k.startswith("wd:") for k in _ledger())


# ---------------------------------------------------------------------------
# 3. The service worker closes withdrawn notifications
# ---------------------------------------------------------------------------

def test_the_service_worker_closes_withdrawn_notifications():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node is not available")
    desktop = Path(__file__).resolve().parents[3] / "desktop"
    proc = subprocess.run([node, str(desktop / "scripts" / "check_companion_sw.js")], cwd=str(desktop),
                          capture_output=True, text=True, timeout=120)
    output = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, output[-3000:]
    assert "COMPANION SW OK" in output, output[-3000:]
