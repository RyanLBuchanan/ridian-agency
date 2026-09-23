"""Owner Workspace sync (v7.1) — outbound only, debounced, never leaky.

Pins, mutation-style like the other gates:
  1. PAIRING saves a token only after the site accepted it: the pairing
     exchange, or (today's site, which has no exchange) one real push with
     the pasted device token. Refused, malformed or unreachable: nothing is
     saved. The token is DPAPI-wrapped at rest.
  2. TRIGGERS: startup (after 60 s), an operation reaching a terminal state,
     an approval staged/answered/voided, an obligation write, a deal write,
     and the 30-minute timer each fire exactly ONE push after the debounce;
     a burst coalesces; a trickle cannot hold a push back past 120 s.
  3. 401 discards the token, marks the connection disconnected, and nothing
     is ever sent again until the owner reconnects.
  4. 429 / network failure back off (Retry-After honored, exponential) and
     retry only on a later trigger — never in a loop.
  5. REFRESH rotates a token within 7 days of expiry; a site without the
     endpoint is asked again after a day; a token past expiry is never sent.
  6. The token only goes to the site it was paired with, never through a
     redirect, and never appears in logs, records, status, settings, the
     pushed documents, or the export.
  7. Loopback only: nothing about sync is on the companion allowlist.

The site is an httpx.MockTransport (FakeSite); no test touches the network.
"""
import asyncio
import datetime as dt
import inspect
import json
import logging
import secrets
from pathlib import Path

import httpx
import jsonschema
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services import (approval_inbox_service, companion_service, obligations_service,
                          operation_log_service, owner_snapshot_service, pipeline_service,
                          settings_service, state_store, sync_service)
from app.services.operator_context import OperatorContext, set_current_operator

SITE = "https://ridiantechnologies.com"
CODE = "PAIRCODE-7Q2X"
PC = ("127.0.0.1", 50000)
HDR = {"X-Ridian-Companion": "1"}
_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"


def _offline_brief():
    raise RuntimeError("offline")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "local_settings.json")
    monkeypatch.setattr(sync_service, "SYNC_PATH", tmp_path / "owner_workspace.json")
    monkeypatch.setattr(sync_service, "_transport", None)
    # The real brief can reach Google; the exporter degrades honestly without it.
    monkeypatch.setattr(owner_snapshot_service.brief_service, "build_brief", _offline_brief)
    previous = sync_service.use_engine(None)
    listeners = list(state_store._save_listeners)
    companion_service.reset_pairing_state()
    yield
    sync_service.use_engine(previous)
    state_store._save_listeners[:] = listeners
    companion_service.reset_pairing_state()
    set_current_operator(None)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSite:
    """The Owner Workspace in-process. Mirrors the live push contract (401
    with an empty body for a bad token, 429 + Retry-After, 422 reason and
    path) and, when enabled, the pair / refresh / revoke exchange. A
    disabled endpoint answers the way the live site's Origin gate answers
    any unknown non-GET path: 403 with a plain-text body."""

    def __init__(self, *, pairing=True, refresh=True, revoke=True, days=90):
        self.pairing = pairing
        self.refresh_supported = refresh
        self.revoke_supported = revoke
        self.days = days
        self.codes = {CODE}
        self.tokens: dict = {}
        self.revoked: set = set()
        self.requests: list = []
        self.push_answers: list = []
        self.fail_network = False

    def issue(self, days=None) -> str:
        token = secrets.token_urlsafe(32)
        self.tokens[token] = _now() + dt.timedelta(days=self.days if days is None else days)
        return token

    def _live(self, token: str) -> bool:
        expires = self.tokens.get(token)
        return expires is not None and token not in self.revoked and expires > _now()

    @staticmethod
    def _gate() -> httpx.Response:
        return httpx.Response(403, text="Cross-site form submissions are forbidden.")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_network:
            raise httpx.ConnectError("offline", request=request)
        auth = request.headers.get("authorization", "")
        bearer = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        path = request.url.path
        if path == sync_service.PAIR_PATH:
            if not self.pairing:
                return self._gate()
            if bearer not in self.codes:
                return httpx.Response(401)
            self.codes.discard(bearer)
            token = self.issue()
            return httpx.Response(200, json={"token": token, "expiresAt": _iso(self.tokens[token])})
        if path == sync_service.REFRESH_PATH:
            if not self.refresh_supported:
                return self._gate()
            if not self._live(bearer):
                return httpx.Response(401)
            self.revoked.add(bearer)
            token = self.issue()
            return httpx.Response(200, json={"token": token, "expiresAt": _iso(self.tokens[token])})
        if path == sync_service.REVOKE_PATH:
            if not self.revoke_supported:
                return self._gate()
            if not self._live(bearer):
                return httpx.Response(401)
            self.revoked.add(bearer)
            return httpx.Response(204)
        if path == sync_service.PUSH_PATH:
            if not self._live(bearer):
                return httpx.Response(401, headers={"www-authenticate": "Bearer"})
            if self.push_answers:
                status, body, headers = self.push_answers.pop(0)
                return httpx.Response(status, json=body, headers=headers or {})
            document = json.loads(request.content)
            return httpx.Response(200, json={
                "ok": True, "duplicate": False, "snapshotId": f"snap-{len(self.pushes)}",
                "sha256": "ab" * 32, "bytes": len(request.content),
                "generatedAt": document["generatedAt"]})
        return httpx.Response(404)

    @property
    def pushes(self) -> list:
        return [r for r in self.requests if r.url.path == sync_service.PUSH_PATH]

    def install(self, monkeypatch) -> "FakeSite":
        monkeypatch.setattr(sync_service, "_transport", httpx.MockTransport(self.handler))
        return self


def _connected(site: FakeSite, days=90) -> str:
    """A connection as a successful pairing leaves it, without the network."""
    token = site.issue(days=days)
    sync_service._connect(SITE, "Test PC", token, site.tokens[token], via="pairing")
    return token


def _engine(clock: Clock) -> sync_service.SyncEngine:
    engine = sync_service.SyncEngine(clock=clock)
    sync_service.use_engine(engine)
    engine.attach()
    return engine


async def _emit(_event: dict) -> None:
    return None


def _stage_approval(tmp_path) -> dict:
    record = {"id": "op_sync", "command": "Invoice Sandy for the workshop",
              "needs_input": [{"question": "Create this invoice?",
                               "options": [{"label": "Approve", "value": "approve"},
                                           {"label": "Cancel", "value": "cancel"}]}]}
    set_current_operator(OperatorContext(folder=tmp_path / "run", record=record, emit=_emit))
    try:
        return approval_inbox_service.stage_from_tool(
            "create_quickbooks_invoice", {"customer": "Sandy Alvarez"},
            {"reason": "invoice_plan_pending"})
    finally:
        set_current_operator(None)


def _deal(n: int = 0) -> dict:
    return {"contact_id": "c_1", "contact_name": "Sandy Alvarez",
            "title": f"AI workshop {n}", "stage": "lead"}


OBLIGATION = {"name": "Monthly support retainer", "task": "Invoice the retainer",
              "cadence": {"kind": "monthly_first_business_day"}}


# ---------------------------------------------------------------------------
# 1. Pairing
# ---------------------------------------------------------------------------

def test_pairing_exchange_stores_a_dpapi_wrapped_token_and_schedules_one_sync(monkeypatch):
    site = FakeSite().install(monkeypatch)
    clock = Clock()
    engine = _engine(clock)
    view = sync_service.pair(CODE, "  Ryan   desktop ")
    assert view["connected"] is True and view["status"] == "connected"
    assert view["label"] == "Ryan desktop" and view["paired_via"] == "pairing"
    assert view["site"] == SITE and view["token_expires_iso"].endswith("Z")
    pair_request = site.requests[0]
    assert pair_request.url.path == "/api/devices/pair"
    assert pair_request.headers["authorization"] == f"Bearer {CODE}"
    assert json.loads(pair_request.content) == {"label": "Ryan desktop"}
    token = next(iter(site.tokens))
    disk = sync_service.SYNC_PATH.read_text(encoding="utf-8")
    assert token not in disk and CODE not in disk
    stored = json.loads(disk)["device_token"]
    assert stored.startswith("dpapi1:") and sync_service._unseal(stored) == token
    # Pairing schedules exactly one push, after the debounce.
    assert not site.pushes
    clock.advance(sync_service.DEBOUNCE_SECONDS - 1)
    assert engine.tick() is None and not site.pushes
    clock.advance(1)
    assert engine.tick() == "accepted"
    assert len(site.pushes) == 1
    assert site.pushes[0].headers["authorization"] == f"Bearer {token}"


def test_a_refused_or_malformed_pairing_code_saves_nothing(monkeypatch):
    site = FakeSite().install(monkeypatch)
    with pytest.raises(sync_service.SyncError) as exc:
        sync_service.pair("WRONG-CODE-0000", "Test PC")
    assert exc.value.status == 400 and "did not accept" in exc.value.detail
    assert "WRONG-CODE-0000" not in exc.value.detail
    assert not sync_service.SYNC_PATH.exists()
    assert sync_service.status_view()["status"] == "not_connected"
    sent = len(site.requests)
    for bad in ("", "   ", "has space", "semi;colon", "abc", "x" * 300):
        with pytest.raises(sync_service.SyncError):
            sync_service.pair(bad, "Test PC")
    with pytest.raises(sync_service.SyncError):
        sync_service.pair(CODE, "bell" + chr(7) + "label")
    assert len(site.requests) == sent, "a malformed code or label never reaches the network"
    assert not sync_service.SYNC_PATH.exists()


def test_a_site_without_the_exchange_takes_the_device_token_after_one_real_push(monkeypatch):
    """Today's site: the token is created on /owner/devices and pasted here."""
    site = FakeSite(pairing=False).install(monkeypatch)
    token = site.issue()
    view = sync_service.pair(token, "Ryan desktop")
    assert view["connected"] and view["paired_via"] == "device_token"
    assert view["token_expires_iso"] == "", "the site did not state an expiry"
    assert [r.url.path for r in site.requests] == [sync_service.PAIR_PATH, sync_service.PUSH_PATH]
    push = site.requests[1]
    assert push.headers["authorization"] == f"Bearer {token}"
    assert push.headers["content-type"] == "application/json"
    document = json.loads(push.content)
    assert document["schema"] == "ridian-operator-snapshot" and document["version"] == 1
    # The verifying push was a real sync.
    assert view["last_result"] == "accepted" and view["last_success_iso"]
    assert token not in sync_service.SYNC_PATH.read_text(encoding="utf-8")


def test_a_device_token_the_site_refuses_or_cannot_verify_saves_nothing(monkeypatch):
    site = FakeSite(pairing=False).install(monkeypatch)
    with pytest.raises(sync_service.SyncError) as exc:
        sync_service.pair(secrets.token_urlsafe(32), "Test PC")
    assert exc.value.status == 400 and "did not accept" in exc.value.detail
    token = site.issue()
    site.push_answers.append((503, {"ok": False, "error": "storage_unavailable"}, None))
    with pytest.raises(sync_service.SyncError) as exc:
        sync_service.pair(token, "Test PC")
    assert exc.value.status == 502 and "Nothing was saved" in exc.value.detail
    assert not sync_service.SYNC_PATH.exists()


def test_an_unreachable_site_saves_nothing(monkeypatch):
    site = FakeSite().install(monkeypatch)
    site.fail_network = True
    with pytest.raises(sync_service.SyncError) as exc:
        sync_service.pair(CODE, "Test PC")
    assert exc.value.status == 502 and "Could not reach" in exc.value.detail
    assert not sync_service.SYNC_PATH.exists()


def test_the_routes_connect_report_and_disconnect_without_ever_returning_the_token(monkeypatch):
    site = FakeSite().install(monkeypatch)
    pc = TestClient(app, client=PC)
    first = pc.get("/owner-workspace/status")
    assert first.status_code == 200
    assert first.json()["status"] == "not_connected" and first.json()["default_label"]
    refused = pc.post("/owner-workspace/connect", json={"code": "WRONG-CODE-0000", "label": "PC"})
    assert refused.status_code == 400 and "WRONG-CODE-0000" not in refused.text
    ok = pc.post("/owner-workspace/connect", json={"code": CODE, "label": "Ryan desktop"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["connected"] is True and ok.json()["label"] == "Ryan desktop"
    token = next(iter(site.tokens))
    again = pc.post("/owner-workspace/connect", json={"code": CODE, "label": "Other"})
    assert again.status_code == 409 and "Disconnect first" in again.json()["detail"]
    status = pc.get("/owner-workspace/status")
    assert status.json()["connected"] is True
    gone = pc.post("/owner-workspace/disconnect")
    assert gone.status_code == 200 and gone.json()["connected"] is False
    assert gone.json()["remote_revoked"] is True
    for response in (first, refused, ok, again, status, gone):
        assert token not in response.text and CODE not in response.text


# ---------------------------------------------------------------------------
# 2. Triggers: exactly one push per trigger, after the debounce
# ---------------------------------------------------------------------------

def test_the_timings_and_the_site_are_the_specified_contract():
    """The tests below are written against these constants; this pins the
    constants themselves to the specification."""
    assert sync_service.DEBOUNCE_SECONDS == 20
    assert sync_service.STARTUP_DELAY_SECONDS == 60
    assert sync_service.HEARTBEAT_SECONDS == 30 * 60
    assert sync_service.REFRESH_WINDOW == dt.timedelta(days=7)
    assert sync_service.MAX_COALESCE_SECONDS == 120
    assert sync_service.DEFAULT_SITE == "https://ridiantechnologies.com"
    assert sync_service.PUSH_PATH == "/api/operator-snapshot/push"

ACTIONS = {
    "startup": lambda tmp_path: None,
    "operation_finished": lambda tmp_path: operation_log_service.upsert_operation(
        {"id": "op_1", "status": "completed", "command": "Draft the recap"}),
    "approval_staged": _stage_approval,
    "approval_answered": lambda tmp_path: approval_inbox_service.sync_from_record(
        {"id": "op_sync", "invoice_approved": True}),
    "approval_voided": lambda tmp_path: approval_inbox_service.void_for_operation(
        "op_sync", "test"),
    "obligation_write": lambda tmp_path: obligations_service.add_obligation(
        dict(OBLIGATION), written_by="manual"),
    "deal_write": lambda tmp_path: pipeline_service.add_deal(_deal(), written_by="pipeline"),
}


@pytest.mark.parametrize("trigger", [
    "startup", "operation_finished", "approval_staged", "approval_answered",
    "approval_voided", "obligation_write", "deal_write", "timer"])
def test_each_trigger_fires_exactly_one_push_after_the_debounce(monkeypatch, tmp_path, trigger):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    # Setup writes happen BEFORE the engine listens: they are not the trigger.
    if trigger == "operation_finished":
        operation_log_service.upsert_operation(
            {"id": "op_1", "status": "running", "command": "Draft the recap"})
    if trigger in ("approval_answered", "approval_voided"):
        _stage_approval(tmp_path)
    clock = Clock()
    engine = _engine(clock)
    if trigger in ("startup", "timer"):
        engine.arm()
    if trigger == "timer":
        clock.advance(sync_service.STARTUP_DELAY_SECONDS)
        assert engine.tick() == "accepted"            # the startup push
        site.requests.clear()
        clock.advance(sync_service.HEARTBEAT_SECONDS - 1)
        assert engine.tick() is None and not site.pushes
        clock.advance(1)
        assert engine.tick() is None                  # the timer fired; now debouncing
        delay = sync_service.DEBOUNCE_SECONDS
    else:
        ACTIONS[trigger](tmp_path)
        delay = (sync_service.STARTUP_DELAY_SECONDS if trigger == "startup"
                 else sync_service.DEBOUNCE_SECONDS)
    assert engine.pending(), f"{trigger} did not schedule a push"
    assert not site.pushes, "nothing is sent before the debounce elapses"
    clock.advance(delay - 1)
    assert engine.tick() is None and not site.pushes
    clock.advance(1)
    assert engine.tick() == "accepted"
    assert len(site.pushes) == 1
    clock.advance(sync_service.DEBOUNCE_SECONDS * 3)
    assert engine.tick() is None and len(site.pushes) == 1, "exactly one push per trigger"


def test_a_burst_of_writes_coalesces_into_one_push(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    clock = Clock()
    engine = _engine(clock)
    for n in range(5):
        pipeline_service.add_deal(_deal(n), written_by="pipeline")
        clock.advance(5)
        assert engine.tick() is None
    obligations_service.add_obligation(dict(OBLIGATION), written_by="manual")
    clock.advance(sync_service.DEBOUNCE_SECONDS - 1)
    assert engine.tick() is None and not site.pushes
    clock.advance(1)
    assert engine.tick() == "accepted"
    assert len(site.pushes) == 1


def test_a_steady_trickle_cannot_hold_a_push_back_past_two_minutes(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    clock = Clock()
    engine = _engine(clock)
    start = clock()
    pushed_at = None
    for n in range(12):
        pipeline_service.add_deal(_deal(n), written_by="pipeline")
        clock.advance(15)
        if engine.tick() == "accepted" and pushed_at is None:
            pushed_at = clock()
    assert pushed_at is not None and pushed_at - start <= sync_service.MAX_COALESCE_SECONDS
    assert len(site.pushes) >= 1


def test_progress_writes_and_runs_that_ended_before_startup_are_not_triggers(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    operation_log_service.upsert_operation({"id": "op_old", "status": "completed", "command": "old"})
    clock = Clock()
    engine = _engine(clock)
    operation_log_service.upsert_operation({"id": "op_2", "status": "running", "command": "x"})
    operation_log_service.upsert_operation({"id": "op_2", "status": "awaiting_input", "command": "x"})
    assert not engine.pending(), "only a run REACHING a terminal state is a trigger"
    operation_log_service.upsert_operation({"id": "op_2", "status": "partial", "command": "x"})
    assert engine.pending(), "a finished run with errors is terminal too"


# ---------------------------------------------------------------------------
# 3. 401: disconnect, discard, stop
# ---------------------------------------------------------------------------

def test_a_401_discards_the_token_and_nothing_is_sent_again(monkeypatch):
    site = FakeSite().install(monkeypatch)
    token = _connected(site)
    clock = Clock()
    engine = _engine(clock)
    site.revoked.add(token)                        # revoked on /owner/devices
    pipeline_service.add_deal(_deal(), written_by="pipeline")
    clock.advance(sync_service.DEBOUNCE_SECONDS)
    assert engine.tick() == "unauthorized"
    view = sync_service.status_view()
    assert view["connected"] is False and view["status"] == "disconnected"
    assert view["disconnected_reason"] == "unauthorized"
    assert json.loads(sync_service.SYNC_PATH.read_text(encoding="utf-8"))["device_token"] == ""
    sent = len(site.requests)
    engine.arm()
    for n in range(3):
        pipeline_service.add_deal(_deal(n + 1), written_by="pipeline")
        clock.advance(sync_service.HEARTBEAT_SECONDS)
        engine.tick()
        clock.advance(sync_service.DEBOUNCE_SECONDS)
        engine.tick()
    assert sync_service.push_now(["manual"]) == ("not_connected", None)
    assert len(site.requests) == sent, "the dead token is never retried"


# ---------------------------------------------------------------------------
# 4. Backoff: 429 and network failures
# ---------------------------------------------------------------------------

def test_a_429_backs_off_honors_retry_after_and_retries_only_on_a_later_trigger(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    clock = Clock()
    engine = _engine(clock)
    site.push_answers.append((429, {"ok": False, "error": "rate_limited", "limit": 10,
                                    "retryAfterSeconds": 900}, {"retry-after": "900"}))
    pipeline_service.add_deal(_deal(), written_by="pipeline")
    clock.advance(sync_service.DEBOUNCE_SECONDS)
    assert engine.tick() == "rate_limited"
    limited_at = clock()
    view = sync_service.status_view()
    assert view["connected"] is True and view["last_result"] == "rate_limited"
    assert "rate limiting" in view["last_error"]
    # No trigger, no retry: waiting alone sends nothing.
    clock.advance(100)
    assert engine.tick() is None and len(site.pushes) == 1
    # A trigger inside the Retry-After window waits for the window to close.
    pipeline_service.add_deal(_deal(1), written_by="pipeline")
    assert engine.next_wake() == limited_at + 900
    for _ in range(5):
        clock.advance(100)
        assert engine.tick() is None
    clock.t = limited_at + 900 - 1
    assert engine.tick() is None and len(site.pushes) == 1
    clock.t = limited_at + 900
    assert engine.tick() == "accepted"
    assert len(site.pushes) == 2
    assert sync_service.status_view()["last_error"] == ""


def test_network_failures_back_off_exponentially_and_never_loop(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    clock = Clock()
    engine = _engine(clock)
    site.fail_network = True
    pipeline_service.add_deal(_deal(), written_by="pipeline")
    clock.advance(sync_service.DEBOUNCE_SECONDS)
    assert engine.tick() == "network_error"
    for n, expected in enumerate((60, 120, 240, 480), start=1):
        failed_at = clock()
        clock.advance(1)
        assert engine.tick() is None, "a failure alone never schedules a retry"
        pipeline_service.add_deal(_deal(n), written_by="pipeline")
        assert engine.next_wake() == failed_at + expected
        clock.t = failed_at + expected
        assert engine.tick() == "network_error"
    assert len(site.pushes) == 5
    view = sync_service.status_view()
    assert view["connected"] is True and view["last_result"] == "network_error"
    site.fail_network = False
    failed_at = clock()
    pipeline_service.add_deal(_deal(9), written_by="pipeline")
    clock.t = engine.next_wake()
    assert clock() == failed_at + 960
    assert engine.tick() == "accepted"
    # Success clears the backoff: the next trigger is back to the plain debounce.
    pipeline_service.add_deal(_deal(10), written_by="pipeline")
    assert engine.next_wake() == clock() + sync_service.DEBOUNCE_SECONDS


# ---------------------------------------------------------------------------
# 5. Refresh and expiry
# ---------------------------------------------------------------------------

def test_refresh_rotates_the_token_within_seven_days_of_expiry(monkeypatch):
    site = FakeSite().install(monkeypatch)
    old = _connected(site, days=6)
    assert sync_service.push_now(["manual"]) == ("accepted", None)
    assert [r.url.path for r in site.requests] == [sync_service.REFRESH_PATH, sync_service.PUSH_PATH]
    assert site.requests[0].headers["authorization"] == f"Bearer {old}"
    new = site.requests[1].headers["authorization"][len("Bearer "):]
    assert new != old and old in site.revoked and new in site.tokens
    disk = json.loads(sync_service.SYNC_PATH.read_text(encoding="utf-8"))
    assert sync_service._unseal(disk["device_token"]) == new
    assert disk["token_expires_iso"] == _iso(site.tokens[new])
    assert old not in json.dumps(disk) and new not in json.dumps(disk)
    # The fresh token is 90 days out: the next push does not refresh again.
    site.requests.clear()
    assert sync_service.push_now(["manual"]) == ("accepted", None)
    assert [r.url.path for r in site.requests] == [sync_service.PUSH_PATH]


def test_a_site_without_refresh_is_asked_again_after_a_day_not_on_every_push(monkeypatch):
    site = FakeSite(refresh=False).install(monkeypatch)
    token = _connected(site, days=3)
    assert sync_service.push_now(["manual"]) == ("accepted", None)
    assert [r.url.path for r in site.requests] == [sync_service.REFRESH_PATH, sync_service.PUSH_PATH]
    site.requests.clear()
    assert sync_service.push_now(["manual"]) == ("accepted", None)
    assert [r.url.path for r in site.requests] == [sync_service.PUSH_PATH]
    # A day later it asks again.
    sync_service._update(None, refresh_unsupported_until_iso=_iso(_now() - dt.timedelta(seconds=1)))
    site.requests.clear()
    sync_service.push_now(["manual"])
    assert [r.url.path for r in site.requests] == [sync_service.REFRESH_PATH, sync_service.PUSH_PATH]
    assert site.pushes[-1].headers["authorization"] == f"Bearer {token}"


def test_a_token_past_its_known_expiry_is_never_sent(monkeypatch):
    site = FakeSite().install(monkeypatch)
    token = site.issue()
    sync_service._connect(SITE, "Test PC", token, _now() - dt.timedelta(minutes=1), via="pairing")
    assert sync_service.push_now(["manual"]) == ("disconnected", None)
    assert site.requests == []
    view = sync_service.status_view()
    assert view["connected"] is False and view["disconnected_reason"] == "expired"


def test_an_unreadable_token_disconnects_instead_of_sending_anything(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    sync_service._update(None, device_token="dpapi1:bm90LWEtcmVhbC1ibG9i")
    assert sync_service.push_now(["manual"]) == ("disconnected", None)
    assert site.requests == []
    assert sync_service.status_view()["disconnected_reason"] == "unreadable"


# ---------------------------------------------------------------------------
# 6. No push when disconnected; disconnect; where the token may go
# ---------------------------------------------------------------------------

def test_nothing_is_sent_or_written_when_never_connected(monkeypatch, tmp_path):
    site = FakeSite().install(monkeypatch)
    clock = Clock()
    engine = _engine(clock)
    engine.arm()
    clock.advance(sync_service.STARTUP_DELAY_SECONDS)
    assert engine.tick() == "not_connected"          # the startup attempt
    for action in ("deal_write", "obligation_write", "approval_staged", "operation_finished"):
        ACTIONS[action](tmp_path)
        clock.advance(sync_service.DEBOUNCE_SECONDS)
        assert engine.tick() == "not_connected"
    clock.advance(sync_service.HEARTBEAT_SECONDS * 2)
    engine.tick()
    clock.advance(sync_service.DEBOUNCE_SECONDS)
    engine.tick()
    assert site.requests == []
    assert not sync_service.SYNC_PATH.exists(), "an unconnected engine never writes its store"


def test_disconnect_revokes_locally_first_and_remotely_best_effort(monkeypatch):
    site = FakeSite().install(monkeypatch)
    sync_service.pair(CODE, "Ryan desktop")
    token = next(iter(site.tokens))
    out = sync_service.disconnect()
    assert out["connected"] is False and out["status"] == "disconnected"
    assert out["remote_revoked"] is True and "revoked this device" in out["detail"]
    revoke = [r for r in site.requests if r.url.path == sync_service.REVOKE_PATH]
    assert len(revoke) == 1 and revoke[0].headers["authorization"] == f"Bearer {token}"
    assert token in site.revoked
    assert json.loads(sync_service.SYNC_PATH.read_text(encoding="utf-8"))["device_token"] == ""
    # A site without the revoke endpoint (today's): the local revoke still
    # holds, and the owner is told to revoke on the site.
    site = FakeSite(pairing=False, revoke=False).install(monkeypatch)
    token = site.issue()
    sync_service.pair(token, "Ryan desktop")
    out = sync_service.disconnect()
    assert out["connected"] is False and out["remote_revoked"] is False
    assert "Device tokens" in out["detail"] and "Ryan desktop" in out["detail"]
    assert token not in site.revoked
    sent = len(site.requests)
    assert sync_service.push_now(["manual"]) == ("not_connected", None)
    assert len(site.requests) == sent


def test_a_push_in_flight_never_resurrects_a_disconnected_connection(monkeypatch):
    site = FakeSite(revoke=False)
    token = _connected(site)
    real = site.handler

    def disconnect_mid_push(request):
        if request.url.path == sync_service.PUSH_PATH and not site.pushes:
            sync_service.disconnect()          # the owner clicks Disconnect meanwhile
        return real(request)

    monkeypatch.setattr(sync_service, "_transport", httpx.MockTransport(disconnect_mid_push))
    result, _ = sync_service.push_now(["manual"])
    assert result == "accepted"                # the site accepted the in-flight push...
    view = sync_service.status_view()
    assert view["connected"] is False and view["disconnected_reason"] == "operator"
    assert view["last_success_iso"] == "", "...but its answer never rewrote the disconnected state"
    assert json.loads(sync_service.SYNC_PATH.read_text(encoding="utf-8"))["device_token"] == ""
    assert token in site.tokens


def test_the_token_only_travels_to_the_site_it_was_paired_with(monkeypatch):
    site = FakeSite().install(monkeypatch)
    sync_service.pair(CODE, "Test PC")
    settings_service.save_settings({"owner_workspace_url": "https://elsewhere.example"})
    assert sync_service.configured_site() == "https://elsewhere.example"
    assert sync_service.push_now(["manual"])[0] == "accepted"
    assert {r.url.host for r in site.requests} == {"ridiantechnologies.com"}


def test_a_redirect_is_never_followed_with_the_token(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    site.push_answers.append((308, {}, {"location": "https://elsewhere.example/api/operator-snapshot/push"}))
    assert sync_service.push_now(["manual"]) == ("redirected", 0.0)
    assert {r.url.host for r in site.requests} == {"ridiantechnologies.com"}
    assert "never sent through a redirect" in sync_service.status_view()["last_error"]


def test_a_refused_document_is_recorded_with_reason_and_path_and_backs_off(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    site.push_answers.append((422, {"ok": False, "error": "rejected", "reason": "at_sign_in_value",
                                    "detail": "$.contacts[0].company"}, None))
    assert sync_service.push_now(["manual"]) == ("rejected", 0.0)
    view = sync_service.status_view()
    assert view["connected"] is True
    assert view["last_error"] == ("The Owner Workspace refused the snapshot "
                                  "(at_sign_in_value at $.contacts[0].company).")


def test_the_site_override_accepts_only_an_https_origin():
    pc = TestClient(app, client=PC)
    for bad in ("http://ridiantechnologies.com", "https://ridiantechnologies.com/owner",
                "ftp://files.example", "ridiantechnologies.com", "https://user:pw@x.example",
                "https://x.example/?q=1", "https://x.example:99999"):
        r = pc.post("/settings", json={"owner_workspace_url": bad})
        assert r.status_code == 400, bad
        assert "https://" in r.json()["detail"]
    r = pc.post("/settings", json={"owner_workspace_url": "https://Staging.RidianTechnologies.com/"})
    assert r.status_code == 200 and r.json()["owner_workspace_url"] == "https://staging.ridiantechnologies.com"
    r = pc.post("/settings", json={"owner_workspace_url": "http://127.0.0.1:5173"})
    assert r.status_code == 200 and r.json()["owner_workspace_url"] == "http://127.0.0.1:5173"
    r = pc.post("/settings", json={"owner_workspace_url": ""})
    assert r.status_code == 200 and r.json()["owner_workspace_url"] == ""
    assert sync_service.configured_site() == sync_service.DEFAULT_SITE == SITE


def test_the_pushed_body_is_the_owner_snapshot_v1_document(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    obligations_service.add_obligation(dict(OBLIGATION), written_by="manual")
    assert sync_service.push_now(["manual"])[0] == "accepted"
    push = site.pushes[0]
    document = json.loads(push.content)
    jsonschema.Draft202012Validator(owner_snapshot_service.schema_document()).validate(document)
    assert document["summary"]["obligations"] == 1
    assert len(push.content) <= sync_service.MAX_BODY_BYTES
    assert push.headers["content-type"] == "application/json"
    assert push.headers["user-agent"].startswith("RidianOperator/")


def test_a_policy_refused_snapshot_is_never_sent(monkeypatch):
    site = FakeSite().install(monkeypatch)
    _connected(site)
    assert {"device_token", "pairing_code"} <= owner_snapshot_service.DENY_SOURCE_FIELDS
    monkeypatch.setattr(owner_snapshot_service, "CONTACT_FIELDS",
                        owner_snapshot_service.CONTACT_FIELDS + (("device_token", "deviceLabel", "str"),))
    assert sync_service.push_now(["manual"]) == ("policy_refused", 0.0)
    assert site.pushes == []
    assert "policy check" in sync_service.status_view()["last_error"]


# ---------------------------------------------------------------------------
# 7. Never leaks; loopback only; the engine lives with the app
# ---------------------------------------------------------------------------

def test_the_token_never_appears_in_logs_records_status_settings_pushes_or_the_export(
        monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    site = FakeSite().install(monkeypatch)
    clock = Clock()
    engine = _engine(clock)
    sync_service.pair(CODE, "Ryan desktop")
    first = next(iter(site.tokens))
    # A full life: pushes, a refused document, a rate limit, a refresh, a
    # dead token, a reconnect through today's device-token path, a disconnect.
    pipeline_service.add_deal(_deal(), written_by="pipeline")
    clock.advance(sync_service.DEBOUNCE_SECONDS)
    assert engine.tick() == "accepted"
    site.push_answers.append((422, {"ok": False, "error": "rejected", "reason": "schema_invalid",
                                    "detail": "$.deals[0]"}, None))
    assert sync_service.push_now(["manual"])[0] == "rejected"
    site.push_answers.append((429, {"ok": False, "error": "rate_limited"}, {"retry-after": "60"}))
    assert sync_service.push_now(["manual"])[0] == "rate_limited"
    sync_service._update(None, token_expires_iso=_iso(_now() + dt.timedelta(days=2)))
    assert sync_service.push_now(["manual"])[0] == "accepted"      # refreshed first
    current = site.pushes[-1].headers["authorization"][len("Bearer "):]
    site.revoked.add(current)
    assert sync_service.push_now(["manual"])[0] == "unauthorized"
    site.pairing = False
    third = site.issue()
    sync_service.pair(third, "Ryan desktop")
    _stage_approval(tmp_path)
    operation_log_service.upsert_operation({"id": "op_9", "status": "completed", "command": "x"})
    sync_service.disconnect()
    secrets_seen = [CODE] + list(site.tokens)
    assert first in secrets_seen and current in secrets_seen and third in secrets_seen

    pc = TestClient(app, client=PC)
    state_text = "".join(p.read_text(encoding="utf-8")
                         for p in state_store.STATE_DIR.rglob("*.json"))
    haystacks = {
        "logs": caplog.text,
        "status route": pc.get("/owner-workspace/status").text,
        "settings route": pc.get("/settings").text,
        "settings file": settings_service.SETTINGS_PATH.read_text(encoding="utf-8")
        if settings_service.SETTINGS_PATH.exists() else "",
        "sync store": sync_service.SYNC_PATH.read_text(encoding="utf-8"),
        "state store": state_text,
        "pushed documents": "".join(r.content.decode("utf-8") for r in site.pushes),
        "export": json.dumps(owner_snapshot_service.build_snapshot()),
    }
    for secret in secrets_seen:
        for name, text in haystacks.items():
            assert secret not in text, f"a credential leaked into {name}"
            assert secret[:16] not in text, f"a credential prefix leaked into {name}"


def test_sync_routes_are_pc_only_and_off_the_companion_allowlist():
    routes = [("GET", "/owner-workspace/status"), ("POST", "/owner-workspace/connect"),
              ("POST", "/owner-workspace/disconnect")]
    for method, path in routes:
        assert not companion_service.device_request_allowed(method, path), path
        assert not companion_service.preauth_request_allowed(method, path), path
    # A paired phone is refused by the gate, before any handler runs.
    settings_service.save_settings({"companion_enabled": "true"})
    pc = TestClient(app, client=PC)
    code = pc.post("/companion/pairing-code").json()["code"]
    lan = TestClient(app, base_url="http://192.168.1.7:8000", client=("192.168.1.50", 40001))
    paired = lan.post("/companion/pair", headers=HDR, json={"code": code, "device_name": "Pixel 7"})
    assert paired.status_code == 200, paired.text
    for method, path in routes:
        r = lan.request(method, path, headers=HDR if method != "GET" else {},
                        **({"json": {"code": CODE}} if path.endswith("/connect") else {}))
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
        assert "not available from a companion device" in r.json()["detail"]
    # And every handler re-checks loopback itself.
    class _Req:
        class client:
            host = "192.168.1.50"

    for handler in (main_module.owner_workspace_status, main_module.owner_workspace_disconnect):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(handler(_Req()))
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        asyncio.run(main_module.owner_workspace_connect(
            main_module.OwnerWorkspaceConnectRequest(code=CODE), _Req()))
    for handler in (main_module.owner_workspace_status, main_module.owner_workspace_connect,
                    main_module.owner_workspace_disconnect):
        assert "_require_loopback(request)" in inspect.getsource(handler)


def test_a_failing_save_listener_never_breaks_a_write():
    def boom(_name, _data):
        raise RuntimeError("listener bug")

    state_store.add_save_listener(boom)
    try:
        state_store.save("deals", [{"id": "d1"}])
    finally:
        state_store.remove_save_listener(boom)
    assert state_store.load_list("deals") == [{"id": "d1"}]


def test_the_engine_starts_and_stops_with_the_app():
    before = list(state_store._save_listeners)
    with TestClient(app, client=PC):
        engine = sync_service._engine
        assert engine is not None and engine._thread is not None and engine._thread.is_alive()
        assert engine.on_store_saved in state_store._save_listeners
        assert engine.pending(), "the startup push is armed"
    assert sync_service._engine is None
    assert state_store._save_listeners == before
    assert not engine._thread


# ---------------------------------------------------------------------------
# Renderer: the Settings block, the dialog, Advanced, the rail line
# ---------------------------------------------------------------------------

def test_renderer_surfaces_connect_disconnect_status_advanced_and_the_rail_line():
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    form = html.split('id="settings-form"', 1)[1].split("</form>", 1)[0]
    primary, advanced = form.split('id="settings-advanced"', 1)
    advanced = advanced.split("</details>", 1)[0]
    for needle in ('>Owner Workspace<', 'id="settings-ows-connect"', 'id="settings-ows-disconnect"',
                   'id="settings-ows-status"', 'id="settings-dot-ows"'):
        assert needle in primary, needle
    # The Export button moved under Advanced; the site override lives there too.
    assert 'id="settings-export-snapshot"' in advanced
    assert 'id="settings-export-snapshot"' not in primary
    assert 'name="owner_workspace_url"' in advanced and html.count('name="owner_workspace_url"') == 1
    # The dialog sits OUTSIDE the settings form and asks for code + label.
    assert 'id="ows-connect-modal"' not in form
    modal = html.split('id="ows-connect-modal"', 1)[1].split("</form>", 1)[0]
    assert 'id="ows-connect-code"' in modal and 'type="password"' in modal
    assert 'id="ows-connect-label"' in modal
    # A small line in the rail footer.
    footer = html.split('class="rail-footer"', 1)[1].split("/rail-footer", 1)[0]
    assert 'id="rail-ows-status"' in footer
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    fields = app_js.split("const SETTINGS_FIELDS", 1)[1].split("];", 1)[0]
    assert "'owner_workspace_url'" in fields
    for route in ("/owner-workspace/status", "/owner-workspace/connect",
                  "/owner-workspace/disconnect"):
        assert route in app_js, route
    render = app_js.split("function _owsRender(", 1)[1].split("async function _owsRefresh(", 1)[0]
    assert "Connected as ${label}" in render and "last sync ${ago}" in render
    close = app_js.split("function _owsCloseConnect(", 1)[1][:500]
    assert "code.value = ''" in close, "the pasted code never lingers in the DOM"
