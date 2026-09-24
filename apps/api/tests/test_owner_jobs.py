"""Ridian Jobs v1, Operator side (v7.3) — contract: site docs/ridian-jobs-v1.md.

Pins, mutation-style like the other gates:
  1. CADENCE: claim every 15 s while connected; 403 (not allowed) slows to
     every 60 s and Settings says so; allowJobs from a snapshot push answer
     (or the claim 403 body) takes effect at once — a flip to true claims
     immediately; 429 honors Retry-After; network failures back off
     exponentially, capped; 401 drops the connection.
  2. ONE AT A TIME: nothing is claimed while a job is non-terminal here.
  3. The job runs through the REAL operator_service.run_operation (planner
     loop, record, persistence) with a mocked tool, stamped source
     "owner-workspace" + the job id.
  4. An approval parks the run -> awaiting_approval; resuming -> running;
     the inbox and Dismiss end a parked job too.
  5. The RESULT carries only the allowlisted fields; replyText goes through
     the snapshot exporter's scrub, so no email or phone ever reaches the
     site, and no '@' at all. A refused result is recorded on the operation
     and never resent; a transient failure resends the identical body.
  6. A stale (> 1 h) or over-long (> 2000 chars) job is refused, reported
     failed, and nothing runs.
  7. Disconnect stops polling; jobs never run disconnected.
  8. The phone companion cannot touch jobs.
  9. The first push after a pairing always sends (sync_service).

The site is an httpx.MockTransport (JobsSite); no test touches the network.
"""
import asyncio
import datetime as dt
import inspect
import json
import logging
import re
import secrets
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services import (approval_inbox_service, companion_service, jobs_service,
                          operator_service, owner_snapshot_service, settings_service,
                          state_store, sync_service)
from app.services.operator_context import current_operator

SITE = "https://ridiantechnologies.com"
PC = ("127.0.0.1", 50000)
HDR = {"X-Ridian-Companion": "1"}
_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
ALLOWED_RESULT_FIELDS = {"status", "replyText", "artifactNames", "toolsUsed", "spendUsd", "openQuestions", "errorCount"}


def _offline_brief():
    raise RuntimeError("offline")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "local_settings.json")
    monkeypatch.setattr(sync_service, "SYNC_PATH", tmp_path / "owner_workspace.json")
    monkeypatch.setattr(jobs_service, "JOBS_PATH", tmp_path / "owner_jobs.json")
    monkeypatch.setattr(sync_service, "_transport", None)
    monkeypatch.setattr(sync_service, "_pairing", None)
    monkeypatch.setattr(owner_snapshot_service.brief_service, "build_brief", _offline_brief)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only-not-a-real-key")
    monkeypatch.setattr(operator_service, "apply_to_environment", lambda: None)
    previous_sync = sync_service.use_engine(None)
    previous_jobs = jobs_service.use_engine(None)
    listeners = list(state_store._save_listeners)
    run_listeners = list(operator_service._RUN_LISTENERS)
    sessions = dict(operator_service._SESSIONS)
    companion_service.reset_pairing_state()
    yield
    sync_service.use_engine(previous_sync)
    jobs_service.use_engine(previous_jobs)
    state_store._save_listeners[:] = listeners
    operator_service._RUN_LISTENERS[:] = run_listeners
    operator_service._SESSIONS.clear()
    operator_service._SESSIONS.update(sessions)
    companion_service.reset_pairing_state()


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class JobsSite:
    """The live jobs contract in-process: claim (200 job, 204 nothing, 403
    jobs_not_allowed, 401 empty for a bad token), status, result, and the
    snapshot push the sync engine uses."""

    def __init__(self) -> None:
        self.tokens: dict = {}
        self.revoked: set = set()
        self.allowed = True
        self.queue: list = []
        self.requests: list = []
        self.claim_answers: list = []
        self.result_answers: list = []
        self.statuses: dict = {}
        self.results: dict = {}
        self.fail_network = False
        self.legacy_statuses = False     # a site that predates awaiting_input

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        self.tokens[token] = _now() + dt.timedelta(days=90)
        return token

    def add_job(self, command: str, *, age: dt.timedelta = dt.timedelta(seconds=5)) -> str:
        job_id = str(uuid.uuid4())
        self.queue.append({"id": job_id, "command": command, "createdAt": _iso(_now() - age)})
        return job_id

    def _live(self, token: str) -> bool:
        return token in self.tokens and token not in self.revoked

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_network:
            raise httpx.ConnectError("offline", request=request)
        auth = request.headers.get("authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not self._live(token):
            return httpx.Response(401, headers={"www-authenticate": "Bearer"})
        path = request.url.path
        if path == sync_service.PUSH_PATH:
            # Site 47484f6+: every authenticated push answer carries allowJobs.
            return httpx.Response(200, json={"ok": True, "duplicate": False, "snapshotId": "snap",
                                             "allowJobs": self.allowed})
        if path.startswith("/api/jobs/") and not self.allowed:
            return httpx.Response(403, json={"error": "jobs_not_allowed", "allowJobs": False})
        if path == jobs_service.CLAIM_PATH:
            if self.claim_answers:
                status, body, headers = self.claim_answers.pop(0)
                return httpx.Response(status, json=body, headers=headers or {})
            if not self.queue:
                return httpx.Response(204)
            job = self.queue.pop(0)
            return httpx.Response(200, json={**job, "status": "claimed", "claimedAt": _iso(_now()),
                                             "expiresAt": _iso(_now() + dt.timedelta(hours=1))})
        m = re.fullmatch(r"/api/jobs/([0-9a-f-]{36})/(status|result)", path)
        if m:
            job_id, kind = m.groups()
            body = json.loads(request.content)
            if kind == "status":
                if self.legacy_statuses and body.get("status") not in ("running", "awaiting_approval"):
                    self.statuses.setdefault(job_id + ":refused", []).append(body)
                    return httpx.Response(400, json={"error": "invalid_status"})
                self.statuses.setdefault(job_id, []).append(body)
                return httpx.Response(200, json={"id": job_id, "status": body["status"],
                                                 "operationId": body.get("operationId"), "startedAt": _iso(_now())})
            if self.result_answers:
                status, answer = self.result_answers.pop(0)
                self.results.setdefault(job_id + ":refused", []).append(request.content)
                return httpx.Response(status, json=answer)
            self.results.setdefault(job_id, []).append(request.content)
            return httpx.Response(200, json={"id": job_id, "status": body["status"], "finishedAt": _iso(_now())})
        return httpx.Response(404)

    def install(self, monkeypatch) -> "JobsSite":
        monkeypatch.setattr(sync_service, "_transport", httpx.MockTransport(self.handler))
        return self

    def of(self, kind: str) -> list:
        if kind == "claim":
            return [r for r in self.requests if r.url.path == jobs_service.CLAIM_PATH]
        return [r for r in self.requests if r.url.path.endswith("/" + kind)]


def _connect(site: JobsSite) -> str:
    token = site.issue()
    sync_service._connect(SITE, "Test PC", token, site.tokens[token], via="browser")
    return token


def _engine(clock: Clock, **kw) -> jobs_service.JobsEngine:
    engine = jobs_service.JobsEngine(clock=clock, **kw)
    engine.attach()
    jobs_service.use_engine(engine)
    engine.poll_now()
    return engine


# ── The planner: the real run_operation, a scripted model, a mocked tool ──

class _Runner:
    def __init__(self, turn) -> None:
        self._turn = turn
        self._done = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._done:
            raise StopAsyncIteration
        self._done = True
        text = await self._turn()
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                               stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    async def generate_tool_call_response(self):
        return None


class MockTool:
    """A planner tool the scripted model calls, exactly as the SDK's tool
    runner would: it works through the live OperatorContext."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list = []

    async def call(self, kwargs: dict) -> str:
        self.calls.append(dict(kwargs))
        op = current_operator()
        op.record.setdefault("tools_used", []).append(self.name)
        if kwargs.get("park"):
            await op.emit_needs_input(question="Send the $250 invoice to Sandy Alvarez?",
                                      context_hint="QuickBooks invoice — approval needed",
                                      options=[{"label": "Approve", "action": "submit", "value": "approve"}])
            return json.dumps({"error": "BLOCKED: approval pending", "reason": "invoice_plan_pending"})
        path = Path(op.folder) / kwargs.get("filename", "recap.docx")
        path.write_text("recap", encoding="utf-8")
        await op.emit_artifact(name=path.name, path=str(path), kind="docx")
        return json.dumps({"ok": True})


def planner(monkeypatch, turns) -> list:
    """Each planner turn (the run, then each resume) is one async callable."""
    tool = MockTool("draft_document")
    monkeypatch.setattr(operator_service, "PLANNER_TOOLS", [tool])
    seen: list = []

    def tool_runner(**kw):
        turn = turns[len(seen)]
        seen.append(kw)
        return _Runner(lambda: turn(tool))

    monkeypatch.setattr(operator_service, "get_client", lambda: SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(tool_runner=tool_runner))))
    return seen


async def _draft(tool, receipt="Drafted the recap and saved recap.docx.", **extra):
    await tool.call({"filename": "recap.docx", **extra})
    return receipt


async def _park(tool):
    await tool.call({"park": True})
    return "I need your approval to send the invoice."


def _ops() -> list:
    return state_store.load_list("operations")


def _job_op(job_id: str) -> dict:
    return next(o for o in _ops() if o.get("job_id") == job_id)


# ---------------------------------------------------------------------------
# 1. Cadence, 403 backoff, 429, network, 401
# ---------------------------------------------------------------------------

def test_the_timings_are_the_contract():
    assert jobs_service.POLL_SECONDS == 15
    assert jobs_service.NOT_ALLOWED_POLL_SECONDS == 60
    assert jobs_service.STALE_AFTER == dt.timedelta(hours=1)
    assert jobs_service.COMMAND_MAX_CHARS == 2000
    assert jobs_service.SOURCE == "owner-workspace"
    assert jobs_service.NOT_ALLOWED_TEXT == "Owner Workspace has not allowed this PC to run commands"


def test_claims_every_15_seconds_and_every_60_seconds_when_not_allowed(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        assert await engine.tick() == "no_job"
        claim = site.of("claim")[0]
        assert claim.method == "POST" and claim.headers["authorization"].startswith("Bearer ")
        assert "cookie" not in claim.headers and "origin" not in claim.headers
        assert engine.view()["text"] == "Accepting commands from the Owner Workspace"
        clock.advance(14)
        assert await engine.tick() == "waiting" and len(site.of("claim")) == 1
        clock.advance(1)
        assert await engine.tick() == "no_job" and len(site.of("claim")) == 2
        # The owner has not allowed this PC: every 60 seconds, and Settings says so.
        site.allowed = False
        clock.advance(15)
        assert await engine.tick() == "not_allowed"
        assert engine.view()["state"] == "not_allowed"
        assert engine.view()["text"] == "Owner Workspace has not allowed this PC to run commands"
        assert sync_service.status_view()["jobs"]["text"] == jobs_service.NOT_ALLOWED_TEXT
        for _ in range(3):
            clock.advance(15)
            assert await engine.tick() == "waiting"
        assert len(site.of("claim")) == 3, "no claim inside the 60-second window"
        clock.advance(15)
        assert await engine.tick() == "not_allowed" and len(site.of("claim")) == 4
        site.allowed = True
        clock.advance(60)
        assert await engine.tick() == "no_job"
        assert engine.view()["state"] == "accepting"

    asyncio.run(scenario())


def test_allow_jobs_on_a_push_answer_takes_effect_at_once(monkeypatch):
    """The site reports allowJobs on every authenticated snapshot push: a
    flip to true claims immediately instead of after the 60-second wait; a
    flip to false stops claiming at once."""
    site = JobsSite().install(monkeypatch)
    _connect(site)
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        site.allowed = False
        assert await engine.tick() == "not_allowed"
        clock.advance(5)
        assert await engine.tick() == "waiting"
        # The owner flips the switch; the next snapshot push says so.
        site.allowed = True
        job_id = site.add_job("Draft the recap")
        assert sync_service.push_now(["timer"])[0] in ("accepted", "unchanged")
        assert engine.view()["state"] == "accepting"
        assert await engine.tick() == "claimed", "claimed at once, no 60-second wait"
        assert clock() == 1005
        engine._task.cancel()
        jobs_service._save({})             # the run itself is not this test's subject
        # Flipped off: the push says so and claiming stops right away.
        site.allowed = False
        sync_service._update(None, force_next_push=True)
        assert sync_service.push_now(["timer"])[0] == "accepted"
        assert engine.view()["state"] == "not_allowed"
        assert engine.view()["text"] == jobs_service.NOT_ALLOWED_TEXT
        claims = len(site.of("claim"))
        clock.advance(59)
        assert await engine.tick() == "waiting"
        assert len(site.of("claim")) == claims
        return job_id

    asyncio.run(scenario())


def test_a_403_is_not_allowed_whatever_its_body_says(monkeypatch):
    """The claim 403 body's allowJobs only confirms: a contradicting body can
    never turn a refusal into a claim loop."""
    site = JobsSite().install(monkeypatch)
    _connect(site)
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        site.claim_answers.append((403, {"error": "jobs_not_allowed", "allowJobs": True}, None))
        assert await engine.tick() == "not_allowed"
        assert engine.view()["state"] == "not_allowed"
        for _ in range(3):
            clock.advance(15)
            assert await engine.tick() == "waiting"
        assert len(site.of("claim")) == 1

    asyncio.run(scenario())


def test_429_honors_retry_after_and_network_failures_back_off_exponentially(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        site.claim_answers.append((429, {"error": "rate_limited", "retryAfterSeconds": 120}, {"retry-after": "120"}))
        assert await engine.tick() == "rate_limited"
        clock.advance(119)
        assert await engine.tick() == "waiting"
        clock.advance(1)
        assert await engine.tick() == "no_job"
        site.fail_network = True
        waits = []
        for _ in range(7):
            clock.advance(3600)
            engine.poll_now()
            before = clock()
            assert await engine.tick() == "network_error"
            waits.append(engine._next_poll - before)
        assert waits == [15, 30, 60, 120, 240, 300, 300], "exponential, capped at 5 minutes"
        site.fail_network = False
        clock.advance(300)
        assert await engine.tick() == "no_job"
        assert engine._next_poll - clock() == 15, "success resets the backoff"

    asyncio.run(scenario())


def test_a_401_drops_the_connection_like_a_push_401(monkeypatch):
    site = JobsSite().install(monkeypatch)
    token = _connect(site)
    site.revoked.add(token)
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        assert await engine.tick() == "unauthorized"
        view = sync_service.status_view()
        assert view["connected"] is False and view["disconnected_reason"] == "unauthorized"
        sent = len(site.requests)
        for _ in range(3):
            clock.advance(300)
            assert await engine.tick() == "disconnected"
        assert len(site.requests) == sent, "never retried with the dead token"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2-3. A job runs through the real planner path, one at a time
# ---------------------------------------------------------------------------

def test_a_job_runs_through_the_real_planner_with_a_mocked_tool_and_reports_its_result(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    seen = planner(monkeypatch, [_draft])
    job_id = site.add_job("Draft the Tuesday recap for the Gulf Shores workshop")
    clock = Clock()
    sync_engine = sync_service.SyncEngine(clock=clock)
    sync_service.use_engine(sync_engine)

    async def scenario():
        engine = _engine(clock)
        assert await engine.tick() == "claimed"
        await engine.wait_for_job()
        assert await engine.tick() == "result_accepted"

    asyncio.run(scenario())
    # The real run_operation drove the planner and the tool.
    assert len(seen) == 1 and "Draft the Tuesday recap" in seen[0]["messages"][0]["content"]
    op = _job_op(job_id)
    assert op["source"] == "owner-workspace" and op["job_id"] == job_id
    assert op["status"] == "completed" and op["command"].startswith("Draft the Tuesday recap")
    # Reports: running (with the operation id), then the result.
    assert site.statuses[job_id] == [{"status": "running", "operationId": op["id"]}]
    body = json.loads(site.results[job_id][0])
    assert body["status"] == "completed"
    assert set(body) == {"status", "result"} and set(body["result"]) <= ALLOWED_RESULT_FIELDS
    assert body["result"]["status"] == "completed"
    assert body["result"]["replyText"] == "Drafted the recap and saved recap.docx."
    assert body["result"]["artifactNames"] == ["recap.docx"]
    assert body["result"]["toolsUsed"] == ["draft_document"]
    assert body["result"]["spendUsd"] == op["spend_usd"] > 0
    assert body["result"]["openQuestions"] == 0 and body["result"]["errorCount"] == 0
    # Then a sync is triggered, and the job is done here.
    assert sync_engine.pending(), "a finished job triggers an Owner Workspace sync"
    assert jobs_service.current_job() is None
    assert jobs_service._load()["recent"][-1]["outcome"] == "accepted"


def test_a_job_runs_exactly_as_a_typed_command(monkeypatch):
    """Same entry point, same arguments except the stamp: no gate, ceiling or
    allowlist can differ, because the code path is the command bar's."""
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {}

    real_signature = inspect.signature(operator_service.run_operation)
    site = JobsSite().install(monkeypatch)
    _connect(site)
    monkeypatch.setattr(operator_service, "run_operation", fake_run)
    site.add_job("Invoice the retainer")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()

    asyncio.run(scenario())
    assert set(captured) == {"command", "emit", "origin"}, "no background, no model, no effort override"
    assert captured["command"] == "Invoice the retainer"
    assert captured["origin"]["source"] == "owner-workspace"
    assert real_signature.parameters["background"].default is False, "a typed command is foreground"
    assert real_signature.parameters["origin"].default is None
    source = inspect.getsource(jobs_service)
    assert source.count("run_operation(") == 1


def test_one_job_at_a_time(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_park, _draft])
    first = site.add_job("Send the Sandy Alvarez invoice")
    second = site.add_job("Draft the recap")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        assert await engine.tick() == "claimed"
        await engine.wait_for_job()
        assert await engine.tick() == "reported"            # running, awaiting_approval
        for _ in range(8):
            clock.advance(15)
            assert await engine.tick() == "busy"
        assert len(site.of("claim")) == 1, "never claim while a job is non-terminal here"
        assert [j["id"] for j in site.queue] == [second]
        operator_service.dismiss_operation(_job_op(first)["id"])  # stopped on the PC
        assert await engine.tick() == "result_accepted"
        clock.advance(15)
        assert await engine.tick() == "claimed"
        await engine.wait_for_job()
        assert await engine.tick() == "result_accepted"

    asyncio.run(scenario())
    assert json.loads(site.results[first][0])["status"] == "cancelled"
    assert json.loads(site.results[second][0])["status"] == "completed"


# ---------------------------------------------------------------------------
# 4. Approvals: park -> awaiting_approval; resume -> running
# ---------------------------------------------------------------------------

def test_a_question_parks_and_reports_awaiting_input_then_resume_reports_running(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_park, _draft])
    job_id = site.add_job("Invoice Sandy Alvarez $250 for the workshop")
    clock = Clock()

    async def null_emit(_event):
        return None

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        op = _job_op(job_id)
        assert op["status"] == "awaiting_input"
        assert await engine.tick() == "reported"
        assert [s["status"] for s in site.statuses[job_id]] == ["running", "awaiting_input"]
        assert jobs_service.current_job()["phase"] == "awaiting_input"
        # Answered on this PC exactly as today: the in-thread resume.
        await operator_service.continue_operation(operation_id=op["id"], answer="approve", emit=null_emit)
        assert await engine.tick() == "result_accepted"

    asyncio.run(scenario())
    assert [s["status"] for s in site.statuses[job_id]] == ["running", "awaiting_input", "running"]
    assert all(s["operationId"] == _job_op(job_id)["id"] for s in site.statuses[job_id])
    body = json.loads(site.results[job_id][0])
    assert body["status"] == "completed" and body["result"]["artifactNames"] == ["recap.docx"]
    assert body["result"]["openQuestions"] == 1, "the answered question stays in the count, as in the snapshot"


def test_the_inbox_answer_and_dismiss_end_a_parked_job(monkeypatch):
    """The phone and the PC inbox answer approvals without resuming the
    planner; the persisted status change is what ends the job."""
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_park, _park])
    approved = site.add_job("Invoice the retainer")
    dismissed = site.add_job("Invoice the other retainer")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()
        approval_inbox_service._update_operation(_job_op(approved)["id"], "Approved from the approval inbox: send it?")
        assert await engine.tick() == "result_accepted"
        clock.advance(15)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()
        operator_service.dismiss_operation(_job_op(dismissed)["id"])
        assert await engine.tick() == "result_accepted"

    asyncio.run(scenario())
    assert json.loads(site.results[approved][0])["status"] == "completed"
    assert json.loads(site.results[dismissed][0])["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 4b. Visible on the PC (v7.5): notices, live events, waiting questions
# ---------------------------------------------------------------------------

async def _stage_then_park(tool):
    """A real gate: stage the approval in the inbox, then park on it."""
    op = current_operator()
    approval_inbox_service.stage_from_tool(
        "create_quickbooks_invoice", {"customer": "Sandy Alvarez"}, {"reason": "invoice_plan_pending"})
    op.record.setdefault("tools_used", []).append("create_quickbooks_invoice")
    await op.emit_needs_input(question="Create this $250 invoice for Sandy Alvarez?",
                              context_hint="QuickBooks invoice — approval needed", buttons_only=True,
                              options=[{"label": "Approve", "action": "submit", "value": "approve"}])
    return "Waiting for your approval of the invoice."


@pytest.fixture
def fresh_notices(monkeypatch):
    monkeypatch.setattr(jobs_service, "_notices", jobs_service.deque(maxlen=jobs_service.NOTICE_KEEP))
    monkeypatch.setattr(jobs_service, "_noticed", set())
    monkeypatch.setattr(jobs_service, "_notice_seq", 0)
    monkeypatch.setattr(jobs_service, "_events", {})
    monkeypatch.setattr(jobs_service, "_site_awaiting_input", None)


def test_a_job_parked_on_a_question_is_visible_on_the_pc_once(monkeypatch, fresh_notices):
    """Today's incident: request_missing_info, nothing staged. The window gets
    one "working on" and one "needs you" notice however often it polls, the
    Approvals page lists the question, the site hears awaiting_input, and the
    existing phone push fires once."""
    pushes = []
    from app.services import push_service
    monkeypatch.setattr(push_service, "notify_run_parked", lambda snap: pushes.append(snap.get("id")))
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_park])
    job_id = site.add_job("Draft a follow-up to Greg about the Navigator pilot")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        for _ in range(4):
            await engine.tick()
            clock.advance(15)
        # Re-saves of the same parked record never make a second notice.
        for _ in range(3):
            state_store.save("operations", state_store.load_list("operations"))

    asyncio.run(scenario())
    op = _job_op(job_id)
    assert [s["status"] for s in site.statuses[job_id]] == ["running", "awaiting_input"]
    feed = jobs_service.notices_after(0, "")
    assert [(n["kind"], n.get("park")) for n in feed["notices"]] == [("claimed", None), ("parked", "question")]
    claimed, parked = feed["notices"]
    assert claimed["operation_id"] == parked["operation_id"] == op["id"]
    assert claimed["command"] == "Draft a follow-up to Greg about the Navigator pilot"
    assert claimed["artifact_folder"] == op["artifact_folder"]
    assert parked["question"] == "Send the $250 invoice to Sandy Alvarez?"
    again = jobs_service.notices_after(feed["latest"], feed["epoch"])
    assert again["notices"] == [], "a window that has seen them gets nothing new"
    assert jobs_service.notices_after(feed["latest"], "another-epoch")["notices"] == feed["notices"]
    assert pushes == [op["id"]], "the existing phone push, once"

    # A run typed on this PC and parked on a question is not a job: not listed here.
    ops = state_store.load_list("operations")
    ops.append({"id": "op_typed000001", "command": "Typed here", "status": "awaiting_input", "source": "",
                "needs_input": [{"question": "Which Greg?"}], "artifact_folder": "C:/typed"})
    state_store.save("operations", ops)
    pc = TestClient(app, client=PC)
    questions = pc.get("/approvals/questions").json()
    assert questions["count"] == 1
    q = questions["questions"][0]
    assert q["operation_id"] == op["id"] and q["job_id"] == job_id
    assert q["question"] == "Send the $250 invoice to Sandy Alvarez?"
    assert q["command"].startswith("Draft a follow-up to Greg") and q["artifact_folder"] == op["artifact_folder"]
    assert pc.get("/approvals").json()["count"] == 0, "nothing staged: the inbox itself is empty"
    notices = pc.get("/owner-workspace/jobs/notices", params={"after": 0}).json()
    assert [n["kind"] for n in notices["notices"]] == ["claimed", "parked"]


def test_a_job_parked_on_a_gate_approval_says_so_and_stays_in_the_inbox(monkeypatch, fresh_notices):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_stage_then_park])
    job_id = site.add_job("Invoice Sandy Alvarez $250 for the workshop")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()

    asyncio.run(scenario())
    op = _job_op(job_id)
    assert [s["status"] for s in site.statuses[job_id]] == ["running", "awaiting_approval"]
    assert jobs_service.park_kind(op["id"]) == "approval"
    parked = jobs_service.notices_after(0, "")["notices"][-1]
    assert parked["kind"] == "parked" and parked["park"] == "approval" and parked["question"] == ""
    pc = TestClient(app, client=PC)
    assert pc.get("/approvals/questions").json()["count"] == 0, "gate approvals are not questions"
    inbox = pc.get("/approvals").json()
    assert inbox["count"] == 1 and inbox["approvals"][0]["operation_id"] == op["id"]


def test_a_site_without_awaiting_input_hears_awaiting_approval(monkeypatch, fresh_notices):
    """Until the site adds awaiting_input it answers 400 invalid_status: the
    report is resent as awaiting_approval, and later parks skip the 400."""
    site = JobsSite().install(monkeypatch)
    site.legacy_statuses = True
    _connect(site)
    planner(monkeypatch, [_park, _park])
    first = site.add_job("Draft a follow-up to Greg")
    second = site.add_job("Draft a follow-up to Sandy")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        assert await engine.tick() == "reported"
        operator_service.dismiss_operation(_job_op(first)["id"])
        await engine.tick()
        clock.advance(15)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()

    asyncio.run(scenario())
    assert [s["status"] for s in site.statuses[first]] == ["running", "awaiting_approval"]
    assert [s["status"] for s in site.statuses[first + ":refused"]] == ["awaiting_input"]
    assert [s["status"] for s in site.statuses[second]] == ["running", "awaiting_approval"]
    assert second + ":refused" not in site.statuses, "no second 400 once the site is known"


def test_the_window_can_follow_a_job_run_live(monkeypatch, fresh_notices):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_park])
    job_id = site.add_job("Draft a follow-up to Greg")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()

    asyncio.run(scenario())
    op = _job_op(job_id)
    pc = TestClient(app, client=PC)
    feed = pc.get("/owner-workspace/jobs/events", params={"operation_id": op["id"], "after": 0}).json()
    kinds = [e["event"] for e in feed["events"]]
    assert kinds[0] == "start" and "needs_input" in kinds and kinds[-1] == "complete"
    start = feed["events"][0]["data"]
    assert start["id"] == op["id"] and start["artifact_folder"] == op["artifact_folder"]
    assert feed["events"][-1]["data"]["awaiting_input"] is True
    assert feed["known"] is True and feed["live"] is False, "parked: nothing more will stream"
    rest = pc.get("/owner-workspace/jobs/events", params={"operation_id": op["id"], "after": feed["next"]}).json()
    assert rest["events"] == [] and rest["next"] == feed["next"]
    unknown = pc.get("/owner-workspace/jobs/events", params={"operation_id": "op_nope", "after": 0}).json()
    assert unknown["known"] is False and unknown["events"] == []


# ---------------------------------------------------------------------------
# 5. The result: allowlist, scrub, rejection, identical resend
# ---------------------------------------------------------------------------

LEAKY_RECEIPT = ("Sent the recap to sandy.alvarez@example.com and texted (251) 555-0147. "
                 "Saved C:\\Users\\ryan\\Documents\\recap.docx. Ping @jane at the chamber.")


def test_the_result_is_allowlisted_and_scrubbed_so_no_email_or_phone_reaches_the_site(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)

    async def leaky(tool):
        await tool.call({"filename": "recap.docx"})
        return LEAKY_RECEIPT

    planner(monkeypatch, [leaky])
    job_id = site.add_job("Draft and send the recap")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        assert await engine.tick() == "result_accepted"

    asyncio.run(scenario())
    body = json.loads(site.results[job_id][0])
    reply = body["result"]["replyText"]
    assert "[email]" in reply and "[phone]" in reply and "[local path removed]" in reply
    assert "(at)jane" in reply
    wire = b"".join(r.content for r in site.requests)
    for leaked in (b"sandy.alvarez", b"555-0147", b"C:\\\\Users", b"@"):
        assert leaked not in wire, leaked
    assert set(body["result"]) <= ALLOWED_RESULT_FIELDS
    # The op record keeps the receipt as it was (the PC's own history).
    assert "sandy.alvarez@example.com" in _job_op(job_id)["receipt"]


def test_build_result_keeps_only_what_the_site_accepts():
    op = {
        "status": "partial", "receipt": "word " * 1000, "spend_usd": 5000.0,
        "tools_used": ["draft_document", "draft_document", "bad/tool", "a@b", ""],
        "artifacts": [{"name": "C:\\run\\report.pdf", "kind": "pdf"}, {"name": "operation_log.json", "kind": "json"},
                      {"name": "https://example.com/x", "kind": "browser"}, {"name": "me@x.pdf", "kind": "pdf"}],
        "needs_input": [{}] * 3, "errors": ["boom"] * 2000, "urls_opened": ["x"], "sms_messages": [{"to": "+1"}],
    }
    body = jobs_service.build_result(op)
    assert body["status"] == "completed", "partial is a finished run with errors"
    result = body["result"]
    assert set(result) == ALLOWED_RESULT_FIELDS
    assert result["status"] == "partial"
    assert len(result["replyText"]) <= 2000 and result["replyText"].endswith("word…")
    # Cut at a word boundary, before the scrub: an address is never split into
    # something the email pattern would miss.
    cut = jobs_service.reply_text("a " * 995 + "sandy.alvarez@example.com and more text after it " * 3)
    assert "sandy" not in cut and "@" not in cut
    # Addresses the email pattern misses (no TLD) still never leave as text.
    odd = jobs_service.reply_text("Mail sandy@localhost or root@server, then ping @jane.")
    assert odd == "Mail [email] or [email] then ping (at)jane."
    assert result["artifactNames"] == ["report.pdf"]
    assert result["toolsUsed"] == ["draft_document"]
    assert result["spendUsd"] == 1000.0 and result["openQuestions"] == 3 and result["errorCount"] == 1000
    failed = jobs_service.build_result({"status": "failed", "errors": ["Planner failed: TimeoutError"]})
    assert failed["status"] == "failed" and failed["result"]["replyText"] == "Planner failed: TimeoutError"
    assert jobs_service.build_result({"status": "cancelled"})["status"] == "cancelled"
    assert jobs_service.build_result({"status": "weird!"})["result"]["status"] == "unknown"


def test_a_refused_result_is_recorded_on_the_operation_and_never_resent(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_draft, _draft])
    job_id = site.add_job("Draft the recap")
    site.result_answers.append((422, {"error": "result_rejected", "reason": "at_sign_in_value", "detail": "$.result.replyText"}))
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        assert await engine.tick() == "result_rejected"
        for _ in range(5):
            clock.advance(60)
            await engine.tick()

    asyncio.run(scenario())
    assert len(site.results[job_id + ":refused"]) == 1 and job_id not in site.results, "never resent"
    op = _job_op(job_id)
    assert op["owner_workspace_result"] == {"status": "rejected", "reason": "at_sign_in_value", "detail": "$.result.replyText"}
    assert "refused this job's result (at_sign_in_value at $.result.replyText)" in op["steps"][-1]["detail"]
    assert jobs_service.current_job() is None, "the queue moves on"


def test_a_transient_failure_resends_the_identical_result(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_draft])
    job_id = site.add_job("Draft the recap")
    site.result_answers.append((503, {"error": "unavailable"}))
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        assert await engine.tick() == "report_retry"
        clock.advance(1)
        assert await engine.tick() == "waiting"
        clock.advance(15)
        assert await engine.tick() == "result_accepted"

    asyncio.run(scenario())
    assert site.results[job_id + ":refused"][0] == site.results[job_id][0], "byte-identical"


# ---------------------------------------------------------------------------
# 6. Stale and over-long jobs are refused
# ---------------------------------------------------------------------------

def test_a_stale_or_over_long_job_is_refused_and_nothing_runs(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)

    async def must_not_run(**_kw):
        raise AssertionError("a refused job must never run")

    monkeypatch.setattr(operator_service, "run_operation", must_not_run)
    stale = site.add_job("Old news", age=dt.timedelta(hours=1, minutes=1))
    long = site.add_job("x" * 2001)
    fresh_enough = site.add_job("y" * 2000, age=dt.timedelta(minutes=59))
    clock = Clock()
    ran = []

    async def scenario():
        engine = _engine(clock)
        assert await engine.tick() == "result_accepted"
        clock.advance(15)
        assert await engine.tick() == "result_accepted"
        monkeypatch.setattr(operator_service, "run_operation", lambda **kw: _record_run(kw, ran))
        clock.advance(15)
        assert await engine.tick() == "claimed"
        await engine.wait_for_job()

    asyncio.run(scenario())
    stale_body = json.loads(site.results[stale][0])
    assert stale_body["status"] == "failed" and stale_body["result"]["status"] == "stale"
    assert "more than an hour old" in stale_body["result"]["replyText"]
    long_body = json.loads(site.results[long][0])
    assert long_body["status"] == "failed" and long_body["result"]["status"] == "too_long"
    assert stale not in site.statuses and long not in site.statuses, "refused jobs never report running"
    assert len(ran) == 1 and ran[0]["command"] == "y" * 2000, "59 minutes and 2000 characters still run"
    assert ran[0]["origin"]["job_id"] == fresh_enough


async def _record_run(kw, ran):
    ran.append(kw)
    return {}


# ---------------------------------------------------------------------------
# 7. Disconnected: nothing is polled, nothing runs
# ---------------------------------------------------------------------------

def test_disconnect_stops_polling_and_jobs_never_run_disconnected(monkeypatch):
    site = JobsSite().install(monkeypatch)
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        # Never connected: nothing is sent.
        for _ in range(3):
            clock.advance(15)
            assert await engine.tick() == "disconnected"
        assert site.requests == []
        assert jobs_service.status_view()["state"] == "off"
        _connect(site)
        assert await engine.tick() == "no_job"
        sync_service.disconnect()
        site.add_job("Should never run")
        for _ in range(4):
            clock.advance(300)
            assert await engine.tick() == "disconnected"
        assert len(site.of("claim")) == 1
        assert site.queue, "the job stays queued on the site"

    asyncio.run(scenario())


def test_a_job_in_flight_is_abandoned_when_the_connection_ends(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_park])
    site.add_job("Invoice the retainer")
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()
        sync_service.disconnect()
        assert await engine.tick() == "disconnected"
        assert jobs_service.current_job() is None
        assert jobs_service._load()["recent"][-1]["outcome"] == "abandoned"

    asyncio.run(scenario())


def test_a_restart_recovers_the_current_job(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    connection_id = sync_service._load()["connection_id"]
    finished = str(uuid.uuid4())
    vanished = str(uuid.uuid4())
    state_store.save("operations", [{"id": "op_aaaaaaaaaaaa", "job_id": finished, "status": "completed",
                                     "receipt": "Done.", "artifacts": [], "errors": [], "needs_input": []}])
    clock = Clock()

    async def scenario():
        jobs_service._save({"current": {"job_id": finished, "connection_id": connection_id, "operation_id": "op_aaaaaaaaaaaa",
                                        "phase": "running", "last_status": "running", "outbox": []}})
        engine = _engine(clock)
        engine.recover()
        assert await engine.tick() == "result_accepted"
        jobs_service._save({"current": {"job_id": vanished, "connection_id": connection_id, "operation_id": "",
                                        "phase": "starting", "last_status": "claimed", "outbox": []}})
        engine.recover()
        assert await engine.tick() == "result_accepted"

    asyncio.run(scenario())
    assert json.loads(site.results[finished][0])["result"]["replyText"] == "Done."
    interrupted = json.loads(site.results[vanished][0])
    assert interrupted["status"] == "failed" and interrupted["result"]["status"] == "interrupted"


# ---------------------------------------------------------------------------
# 8. The phone companion cannot touch jobs
# ---------------------------------------------------------------------------

JOB_ROUTES = ("/owner-workspace/status", "/owner-workspace/jobs/notices",
              "/owner-workspace/jobs/events", "/approvals/questions")


def test_nothing_about_jobs_is_reachable_from_the_phone():
    # Every jobs surface is loopback-only and off the companion allowlist.
    paths = [getattr(r, "path", "") for r in app.routes]
    job_paths = [p for p in paths if "job" in p.lower() or p.startswith("/owner-workspace") or p == "/approvals/questions"]
    assert set(JOB_ROUTES) <= set(job_paths)
    for method, path in companion_service._DEVICE_ALLOWED_EXACT | companion_service._PREAUTH_ALLOWED:
        assert "job" not in path and "owner-workspace" not in path and path != "/approvals/questions", path
    for path in job_paths:
        for method in ("GET", "POST"):
            assert not companion_service.device_request_allowed(method, path), path
    for handler in (main_module.owner_workspace_job_notices, main_module.owner_workspace_job_events,
                    main_module.approvals_questions):
        assert "_require_loopback(request)" in inspect.getsource(handler)
    # A paired phone is refused every one of them.
    settings_service.save_settings({"companion_enabled": "true"})
    pc = TestClient(app, client=PC)
    code = pc.post("/companion/pairing-code").json()["code"]
    lan = TestClient(app, base_url="http://192.168.1.7:8000", client=("192.168.1.50", 40001))
    paired = lan.post("/companion/pair", headers=HDR, json={"code": code, "device_name": "Pixel 7"})
    assert paired.status_code == 200, paired.text
    for path in JOB_ROUTES:
        refused = lan.get(path)
        assert refused.status_code == 403, path
    # The phone's own /operations/run can never stamp a job: the request
    # model has no such field, and only jobs_service passes origin.
    fields = set(main_module.OperationRunRequest.model_fields)
    assert not fields & {"origin", "source", "job_id"}
    run_route = inspect.getsource(main_module.operations_run)
    assert "origin" not in run_route
    app_dir = Path(main_module.__file__).parent
    stampers = [p.name for p in app_dir.rglob("*.py") if "origin={" in p.read_text(encoding="utf-8")]
    assert stampers == ["jobs_service.py"]


def test_a_forged_origin_cannot_stamp_a_run():
    record_src = inspect.getsource(operator_service.run_operation)
    assert 'origin.get("source") == JOB_SOURCE and origin.get("job_id")' in record_src
    assert operator_service.JOB_SOURCE == "owner-workspace"


# ---------------------------------------------------------------------------
# 9. First push after pairing always sends
# ---------------------------------------------------------------------------

def test_the_first_push_after_pairing_always_sends(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    # Whatever the record carries, a fresh pairing's first push goes out.
    _body, content_hash = sync_service._snapshot_body()
    sync_service._update(None, last_pushed_content_hash=content_hash)
    assert sync_service._load()["force_next_push"] is True
    assert sync_service.push_now(["paired"]) == ("accepted", None)
    pushes = [r for r in site.requests if r.url.path == sync_service.PUSH_PATH]
    assert len(pushes) == 1, "the new token was exercised"
    assert sync_service._load()["force_next_push"] is False
    assert sync_service.push_now(["timer"]) == ("unchanged", None)
    assert len([r for r in site.requests if r.url.path == sync_service.PUSH_PATH]) == 1


def test_a_failed_first_push_keeps_the_force_until_one_is_accepted(monkeypatch):
    site = JobsSite().install(monkeypatch)
    _connect(site)
    _body, content_hash = sync_service._snapshot_body()
    sync_service._update(None, last_pushed_content_hash=content_hash)
    site.fail_network = True
    assert sync_service.push_now(["paired"])[0] == "network_error"
    assert sync_service._load()["force_next_push"] is True
    site.fail_network = False
    assert sync_service.push_now(["retry"]) == ("accepted", None)


# ---------------------------------------------------------------------------
# Privacy, status, renderer
# ---------------------------------------------------------------------------

def test_the_command_text_is_never_logged_or_kept_in_the_jobs_store(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    site = JobsSite().install(monkeypatch)
    _connect(site)
    planner(monkeypatch, [_draft])
    command = "Draft the confidential Baldwin County retainer renewal"
    site.add_job(command)
    clock = Clock()

    async def scenario():
        engine = _engine(clock)
        await engine.tick()
        await engine.wait_for_job()
        await engine.tick()

    asyncio.run(scenario())
    assert "confidential Baldwin" not in caplog.text
    assert "confidential Baldwin" not in jobs_service.JOBS_PATH.read_text(encoding="utf-8")
    pc = TestClient(app, client=PC)
    assert "confidential Baldwin" not in pc.get("/owner-workspace/status").text


def test_settings_and_the_sidebar_show_jobs():
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    ows_block = html.split("v7.1 Owner Workspace sync", 1)[1].split('id="settings-advanced"', 1)[0]
    assert 'id="settings-ows-jobs"' in ows_block
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    render = app_js.split("function _owsRender(", 1)[1].split("async function _owsRefresh(", 1)[0]
    assert "settings-ows-jobs" in render and "s.connected && s.jobs" in render
    assert "jobs.state === 'not_allowed'" in render
    rail = app_js.split("function _railRenderThreads(", 1)[1][:6000]
    assert "op.source === 'owner-workspace'" in rail and "'From Owner Workspace'" in rail
    assert "log.source === 'owner-workspace' ? 'From Owner Workspace' : 'You'" in app_js
    harness = (_DESKTOP / "scripts" / "check_settings_layout.js").read_text(encoding="utf-8")
    assert "'settings-ows-jobs'" in harness
