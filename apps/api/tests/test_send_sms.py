"""send_sms (v7.0) — allowlist-only recipient, approval-gated, never silent.

Pins, mutation-style like the other gates:
  1. The recipient resolves ONLY from the SMS allowlist by label. A label
     that is not on it, a contact record's phone, or a raw number is refused
     before anything is staged. A mutated resolver (allowlist check disabled)
     is caught by the tool's independent second check.
  2. Nothing sends without the signature-matched approval: a forged flag,
     a changed allowlist, or a cancelled run never reaches Twilio.
  3. Body limit (320) and the verbatim-link rule are enforced in code.
  4. Failure is a visible error (timeline step + error), never a silent
     success; success records the Twilio SID, status and price on the
     operation and adds the price to the spend ledger.
  5. Credentials never appear in logs, operation records, the approval
     inbox, or the Owner Snapshot export; the export scrubs a number typed
     in a command and never carries the sms ledger.

Twilio is an httpx.MockTransport; no test touches the network. Every phone
number below is fictional (555-01xx).
"""
import asyncio
import inspect
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import (approval_inbox_service, memory_service,
                          operator_service, owner_snapshot_service,
                          settings_service, sms_service, state_store)
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

SID = "ACtest0000000000000000000000000001"
TOKEN = "authtoken-TESTSECRET-alpha-0001"
FROM = "+15550100100"
SARAH = "+15550100124"
ALLOW = "Sarah at the Chamber = +15550100124\nMe = +1 (555) 010-0125\n"
COMMAND = "Text Sarah at the Chamber that the deck is ready for Thursday"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "local_settings.json")
    monkeypatch.setattr(sms_service, "_transport", None)
    yield
    set_current_operator(None)


class _Twilio:
    """Records every request; answers the Account GET and the Messages POST."""

    def __init__(self, status=201, body=None, price="-0.00790"):
        self.status = status
        self.body = body
        self.price = price
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path.endswith(f"/Accounts/{SID}.json"):
            return httpx.Response(200, json={"friendly_name": "Ridian Test", "status": "active"})
        if request.method == "POST" and request.url.path.endswith("/Messages.json"):
            if self.body is not None:
                return httpx.Response(self.status, json=self.body)
            return httpx.Response(self.status, json={
                "sid": "SMtest00000000000000000000000000ab", "status": "queued",
                "price": self.price, "price_unit": "USD"})
        return httpx.Response(404, json={"message": "unexpected"})

    @property
    def posts(self):
        return [r for r in self.requests if r.method == "POST"]

    def install(self, monkeypatch):
        monkeypatch.setattr(sms_service, "_transport", httpx.MockTransport(self.handler))
        return self


def _configure(allowlist=ALLOW):
    """Store what the Settings API would: the canonical 'Label = +E164' text."""
    canonical = sms_service.format_allowlist(sms_service.parse_allowlist(allowlist)) if allowlist else ""
    settings_service.save_settings({
        "twilio_account_sid": SID, "twilio_auth_token": TOKEN,
        "twilio_from_number": FROM, "sms_recipient_allowlist": canonical})


async def _emit(_ev):
    return None


def _op(tmp_path, command=COMMAND, op_id="op_sms"):
    record = {"id": op_id, "command": command, "steps": [], "tools_used": [],
              "artifacts": [], "errors": [], "spend_usd": 0.0}
    op = OperatorContext(folder=tmp_path / "run", record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _persist_owner(op, status="awaiting_input"):
    ops = state_store.load_list("operations")
    ops.insert(0, {"id": op.record["id"], "status": status,
                   "awaiting_input": status == "awaiting_input",
                   "command": op.record["command"], "steps": [],
                   "needs_input": list(op.record.get("needs_input") or [])})
    state_store.save("operations", ops)


# --------------------------------------------------------------------------
# Allowlist parsing
# --------------------------------------------------------------------------

def test_allowlist_parses_labels_and_normalizes_numbers():
    entries = sms_service.parse_allowlist(ALLOW + "\n# a comment\n\n")
    assert entries == [{"label": "Sarah at the Chamber", "e164": "+15550100124"},
                       {"label": "Me", "e164": "+15550100125"}]
    assert sms_service.format_allowlist(entries) == (
        "Sarah at the Chamber = +15550100124\nMe = +15550100125")


@pytest.mark.parametrize("bad", [
    "Sarah 5550100124",                      # no separator
    "+15550100124 = +15550100125",           # a number as the label
    "Sarah = 555-0100",                      # not E.164
    "Sarah = +15550100124\nsarah = +15550100125",   # duplicate label
    "= +15550100124",                        # empty label
])
def test_allowlist_rejects_malformed_lines(bad):
    with pytest.raises(sms_service.SmsError) as exc:
        sms_service.parse_allowlist(bad)
    assert "Line" in exc.value.detail


def test_an_unparseable_saved_allowlist_reads_as_nobody(monkeypatch):
    _configure(allowlist="")
    settings_service.SETTINGS_PATH.write_text(json.dumps({
        "sms_recipient_allowlist": "garbage without a number"}), encoding="utf-8")
    assert sms_service.allowlist() == []
    assert sms_service.resolve_recipient("garbage without a number") is None


# --------------------------------------------------------------------------
# 1. Recipient: allowlist label ONLY
# --------------------------------------------------------------------------

def test_unknown_label_is_refused_and_nothing_is_staged(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    op = _op(tmp_path, command="Text Greg that the deck is ready")
    out = _call("send_sms", recipient_label="Greg", body="The deck is ready.")
    assert out["reason"] == "sms_recipient_unknown"
    assert "allowlist" in out["error"] and "Do NOT retry" in out["error"]
    assert tw.posts == []
    assert approval_inbox_service.list_pending() == []
    assert not op.record.get("needs_input")
    assert any(s["name"] == "sms" and s["status"] == "skipped" for s in op.record["steps"])


def test_contact_record_phone_and_raw_number_are_refused(monkeypatch, tmp_path):
    """A contact on file with a phone is NOT a recipient; neither is a number
    passed as the label, nor a number the operator typed in the command."""
    _configure()
    tw = _Twilio().install(monkeypatch)
    memory_service.add_contact({"name": "Greg Alexander", "phone": "+15550100177",
                                "email": "", "company": "Chamber"},
                               written_by="save_memory", source_op="op_x")
    op = _op(tmp_path, command="Text Greg Alexander at +1 555 010 0177 that we are on")
    for label in ("Greg Alexander", "+15550100177", "+1 555 010 0177", ""):
        out = _call("send_sms", recipient_label=label, body="We are on.")
        assert out["reason"] == "sms_recipient_unknown", label
    assert tw.posts == []
    assert approval_inbox_service.list_pending() == []
    assert op.record.get("sms_send_asked") is None


def test_disabled_allowlist_check_is_caught_by_the_second_check(monkeypatch, tmp_path):
    """Mutation: a resolver that hands back ANY label with a number of its
    own (the allowlist check disabled) must still be refused — the tool
    re-verifies label+number against the real allowlist."""
    _configure()
    tw = _Twilio().install(monkeypatch)
    monkeypatch.setattr(sms_service, "resolve_recipient",
                        lambda label: {"label": label, "e164": "+15550100199"})
    _op(tmp_path)
    out = _call("send_sms", recipient_label="Anyone At All", body="hi")
    assert out["reason"] == "sms_recipient_unknown"
    # Even a REAL label with a swapped number is refused.
    out = _call("send_sms", recipient_label="Sarah at the Chamber", body="hi")
    assert out["reason"] == "sms_recipient_unknown"
    assert tw.posts == [] and approval_inbox_service.list_pending() == []


def test_source_pin_both_recipient_checks_precede_the_send():
    src = inspect.getsource(t._send_sms)
    i_resolve = src.index("sms_service.resolve_recipient(")
    i_verify = src.index("sms_service.recipient_is_allowlisted(")
    i_gate = src.index("_sms_approval_gate(")
    i_send = src.index("sms_service.send_message")
    assert i_resolve < i_verify < i_gate < i_send
    # The command text is never a source of a number: no extraction call.
    assert "extract_phone" not in src and "record.get(\"command\")" not in src.split("_sms_untyped_url")[0]


# --------------------------------------------------------------------------
# 2. Approval: signature-matched, never bypassed
# --------------------------------------------------------------------------

def test_first_call_stages_an_approval_showing_label_number_and_body(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    op = _op(tmp_path)
    out = _call("send_sms", recipient_label="sarah at the chamber", body="The deck is ready for Thursday.")
    assert out["reason"] == "sms_send_pending"
    assert tw.posts == []
    need = op.record["needs_input"][-1]
    assert "Sarah at the Chamber" in need["question"]
    assert SARAH in need["question"]
    assert "The deck is ready for Thursday." in need["question"]
    assert need["buttons_only"] is True
    assert {o["value"] for o in need["options"]} == {t.SMS_PROCEED, t.SMS_CANCEL}
    pending = approval_inbox_service.list_pending()
    assert len(pending) == 1
    assert pending[0]["tool"] == "send_sms" and pending[0]["reason"] == "sms_send_pending"
    assert pending[0]["kwargs"] == {"recipient_label": "sarah at the chamber",
                                    "body": "The deck is ready for Thursday."}
    assert op.record["awaiting_input"] is True


def test_send_without_approval_is_refused_even_with_a_forged_flag(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    op = _op(tmp_path)
    op.record["sms_approved"] = True                 # forged: no signature
    out = _call("send_sms", recipient_label="Sarah at the Chamber", body="hi")
    assert out["reason"] == "sms_send_pending"       # re-asked, not sent
    assert tw.posts == []
    op.record["sms_approved"] = True
    op.record["sms_preview_sig"] = t._sms_sig("Sarah at the Chamber", SARAH, "different body")
    out = _call("send_sms", recipient_label="Sarah at the Chamber", body="hi")
    assert out["reason"] == "sms_send_pending"
    assert tw.posts == []


def test_declined_preview_is_not_sent_and_not_retried(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    op = _op(tmp_path)
    assert _call("send_sms", recipient_label="Me", body="ping")["reason"] == "sms_send_pending"
    note = operator_service._apply_sms_answer(op, t.SMS_CANCEL)
    assert "DECLINED" in note and op.record["sms_declined"] is True
    out = _call("send_sms", recipient_label="Me", body="ping")
    assert out["reason"] == "sms_declined"
    assert tw.posts == []


def test_approved_call_sends_once_and_records_sid_status_price_and_spend(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio(price="-0.00790").install(monkeypatch)
    op = _op(tmp_path)
    assert _call("send_sms", recipient_label="Sarah at the Chamber",
                 body="The deck is ready for Thursday.")["reason"] == "sms_send_pending"
    note = operator_service._apply_sms_answer(op, t.SMS_PROCEED)
    assert "APPROVED" in note
    out = _call("send_sms", recipient_label="Sarah at the Chamber",
                body="The deck is ready for Thursday.")
    assert out["sid"] == "SMtest00000000000000000000000000ab"
    assert out["status"] == "queued" and out["to"] == SARAH
    assert len(tw.posts) == 1
    form = dict(httpx.QueryParams(tw.posts[0].content.decode()))
    assert form == {"From": FROM, "To": SARAH, "Body": "The deck is ready for Thursday."}
    assert tw.posts[0].headers["authorization"].startswith("Basic ")
    ledger = op.record["sms_messages"]
    assert len(ledger) == 1
    assert ledger[0]["sid"] == out["sid"] and ledger[0]["to"] == SARAH
    assert ledger[0]["price"] == "-0.00790" and ledger[0]["price_unit"] == "USD"
    assert op.record["spend_usd"] == pytest.approx(0.0079)
    assert "send_sms" in op.record["tools_used"]
    assert any(s["name"] == "sms" and s["status"] == "completed" for s in op.record["steps"])
    # The persisted view carries the ledger so history and the audit see it.
    view = operator_service._finalized_view({**op.record, "intent": "sms", "artifact_folder": "",
                                             "started_at": "", "status": "completed",
                                             "sources_count": 0, "audio_generated": False,
                                             "audio_duration_seconds": 0})
    assert view["sms_messages"][0]["sid"] == out["sid"]
    # A second approved call is a NEW preview, not a second send.
    out2 = _call("send_sms", recipient_label="Sarah at the Chamber", body="Another one")
    assert out2["reason"] == "sms_send_pending" and len(tw.posts) == 1


def test_null_price_means_no_spend_and_an_honest_pending_note(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio(price=None).install(monkeypatch)
    op = _op(tmp_path)
    _call("send_sms", recipient_label="Me", body="ping")
    operator_service._apply_sms_answer(op, t.SMS_PROCEED)
    out = _call("send_sms", recipient_label="Me", body="ping")
    assert out["sid"] and len(tw.posts) == 1
    assert op.record["spend_usd"] == 0.0
    assert "price pending" in next(s["detail"] for s in op.record["steps"] if s["name"] == "sms")


def test_inbox_approval_re_executes_through_the_gate_and_a_changed_allowlist_is_refused(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    op = _op(tmp_path)
    assert _call("send_sms", recipient_label="Me", body="ping")["reason"] == "sms_send_pending"
    _persist_owner(op)
    appr = approval_inbox_service.list_pending()[0]
    set_current_operator(None)
    # Tamper: the operator edits the allowlist number between staging and approval.
    _configure(allowlist="Me = +15550100126\n")
    res = asyncio.run(approval_inbox_service.answer_approval(appr["id"], t.SMS_PROCEED))
    assert res.get("reason") == "signature_mismatch"
    assert tw.posts == []
    # The refusal re-staged a preview signed for the EDITED number. Restoring
    # the allowlist makes that signature stale too, so approving it is refused
    # again and a third preview is staged for the restored number.
    appr2 = approval_inbox_service.list_pending()[0]
    assert appr2["id"] != appr["id"] and "+15550100126" in appr2["question"]
    _configure()
    set_current_operator(None)
    res = asyncio.run(approval_inbox_service.answer_approval(appr2["id"], t.SMS_PROCEED))
    assert res.get("reason") == "signature_mismatch"
    assert tw.posts == []
    # Only the preview that matches the CURRENT allowlist sends, exactly once.
    appr3 = approval_inbox_service.list_pending()[0]
    assert appr3["id"] != appr2["id"] and "+15550100125" in appr3["question"]
    set_current_operator(None)
    res = asyncio.run(approval_inbox_service.answer_approval(appr3["id"], t.SMS_PROCEED))
    assert res.get("approved") is True, res
    assert len(tw.posts) == 1
    assert approval_inbox_service.list_pending() == []


def test_cancelling_the_run_voids_the_staged_sms_approval(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    op = _op(tmp_path, op_id="op_cancel_me")
    assert _call("send_sms", recipient_label="Me", body="ping")["reason"] == "sms_send_pending"
    _persist_owner(op)
    appr = approval_inbox_service.list_pending()[0]
    set_current_operator(None)
    operator_service.dismiss_operation("op_cancel_me")
    assert approval_inbox_service.list_pending() == []
    stored = next(a for a in state_store.load_list("approvals") if a["id"] == appr["id"])
    assert stored["status"] == "declined" and "voided" in stored["outcome"]
    res = asyncio.run(approval_inbox_service.answer_approval(appr["id"], t.SMS_PROCEED))
    assert "error" in res
    assert tw.posts == []


# --------------------------------------------------------------------------
# 3. Body limit and the verbatim-link rule
# --------------------------------------------------------------------------

def test_body_over_320_characters_is_refused(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    _op(tmp_path)
    out = _call("send_sms", recipient_label="Me", body="x" * 321)
    assert out["reason"] == "sms_body_too_long"
    assert tw.posts == [] and approval_inbox_service.list_pending() == []
    assert _call("send_sms", recipient_label="Me", body="y" * 320)["reason"] == "sms_send_pending"


def test_link_not_typed_in_the_command_is_refused_and_a_verbatim_one_is_allowed(monkeypatch, tmp_path):
    _configure()
    tw = _Twilio().install(monkeypatch)
    _op(tmp_path, command="Text Me a reminder about the deck")
    out = _call("send_sms", recipient_label="Me", body="Deck: https://docs.example/deck-123")
    assert out["reason"] == "sms_url_not_typed"
    assert tw.posts == [] and approval_inbox_service.list_pending() == []
    _op(tmp_path, command="Text Me the link https://docs.example/deck-123 for the deck")
    out = _call("send_sms", recipient_label="Me", body="Deck: https://docs.example/deck-123")
    assert out["reason"] == "sms_send_pending"


def test_not_configured_refuses_before_staging(monkeypatch, tmp_path):
    settings_service.save_settings({"sms_recipient_allowlist": ALLOW})
    tw = _Twilio().install(monkeypatch)
    _op(tmp_path)
    out = _call("send_sms", recipient_label="Me", body="ping")
    assert out["reason"] == "sms_not_configured"
    assert "twilio_account_sid" in out["error"]
    assert tw.posts == [] and approval_inbox_service.list_pending() == []


# --------------------------------------------------------------------------
# 4. Delivery failure is visible
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status,body", [
    (400, {"code": 21211, "message": "The 'To' number is not a valid phone number."}),
    (500, {"message": "Internal error"}),
    (201, {"status": "queued"}),                       # no SID → failure
])
def test_twilio_failure_is_a_visible_error_never_a_silent_success(monkeypatch, tmp_path, status, body):
    _configure()
    tw = _Twilio(status=status, body=body).install(monkeypatch)
    op = _op(tmp_path)
    _call("send_sms", recipient_label="Me", body="ping")
    operator_service._apply_sms_answer(op, t.SMS_PROCEED)
    out = _call("send_sms", recipient_label="Me", body="ping")
    assert out["reason"] == "sms_send_failed"
    assert len(tw.posts) == 1
    assert op.record["errors"] and "send_sms failed" in op.record["errors"][-1]
    assert any(s["name"] == "sms" and s["status"] == "failed" for s in op.record["steps"])
    assert not op.record.get("sms_messages")
    assert op.record["spend_usd"] == 0.0
    if status == 400:
        assert "21211" in out["error"]


def test_network_failure_is_visible(monkeypatch, tmp_path):
    _configure()

    def boom(request):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(sms_service, "_transport", httpx.MockTransport(boom))
    op = _op(tmp_path)
    _call("send_sms", recipient_label="Me", body="ping")
    operator_service._apply_sms_answer(op, t.SMS_PROCEED)
    out = _call("send_sms", recipient_label="Me", body="ping")
    assert out["reason"] == "sms_send_failed" and "ConnectError" in out["error"]
    assert not op.record.get("sms_messages")


# --------------------------------------------------------------------------
# 5. Credentials never leak; the export scrubs numbers and drops the ledger
# --------------------------------------------------------------------------

def test_credentials_never_appear_in_logs_records_inbox_or_export(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    _configure()
    tw = _Twilio().install(monkeypatch)
    command = "Text Sarah at the Chamber at +1 555 010 0124 that the deck is ready"
    op = _op(tmp_path, command=command, op_id="op_export")
    _call("send_sms", recipient_label="Sarah at the Chamber", body="The deck is ready.")
    operator_service._apply_sms_answer(op, t.SMS_PROCEED)
    out = _call("send_sms", recipient_label="Sarah at the Chamber", body="The deck is ready.")
    assert out["sid"] and len(tw.posts) == 1

    secrets = (TOKEN, SID)
    for leaked in secrets:
        assert leaked not in caplog.text, leaked
        assert leaked not in json.dumps(op.record), leaked
        assert leaked not in json.dumps(state_store.load_list("approvals")), leaked
    # Logs carry the message SID and status only — never the number or body.
    assert SARAH not in caplog.text and "The deck is ready." not in caplog.text

    # Persist the finished run and export the Owner Snapshot.
    view = operator_service._finalized_view({**op.record, "intent": "sms", "artifact_folder": "",
                                             "started_at": "2026-09-13T10:00:00+00:00",
                                             "completed_at": "2026-09-13T10:00:05+00:00",
                                             "status": "completed", "sources_count": 0,
                                             "audio_generated": False, "audio_duration_seconds": 0})
    state_store.save("operations", [view])
    monkeypatch.setattr(owner_snapshot_service.brief_service, "build_brief",
                        lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    doc = owner_snapshot_service.build_snapshot(version="test")
    text = json.dumps(doc)
    row = doc["recentWork"][0]
    assert "send_sms" in row["toolsUsed"]
    assert row["command"] == "Text Sarah at the Chamber at [phone] that the deck is ready"
    for leaked in (TOKEN, SID, FROM, SARAH, "5550100124", "555 010 0124", "sms_messages", out["sid"]):
        assert leaked not in text, leaked
    assert "sms_messages" in owner_snapshot_service.DENY_SOURCE_FIELDS


def test_credentials_are_dpapi_wrapped_at_rest_and_the_token_is_write_only():
    _configure()
    disk = settings_service.SETTINGS_PATH.read_text(encoding="utf-8")
    for plain in (SID, TOKEN, FROM):
        assert plain not in disk, plain
    assert disk.count('"dpapi1:') >= 3
    loaded = settings_service.load_settings()
    assert (loaded["twilio_account_sid"], loaded["twilio_auth_token"],
            loaded["twilio_from_number"]) == (SID, TOKEN, FROM)
    # Blank token on a later save keeps the stored one (write-only secret).
    settings_service.save_settings({"twilio_auth_token": ""})
    assert settings_service.load_settings()["twilio_auth_token"] == TOKEN
    view = settings_service.public_view()
    assert view["twilio_auth_token_configured"] is True
    assert "twilio_auth_token" not in view
    assert view["twilio_account_sid"] == SID and view["twilio_from_number"] == FROM


# --------------------------------------------------------------------------
# 6. Settings API and the connection test
# --------------------------------------------------------------------------

client = TestClient(app)


def test_settings_api_round_trips_and_normalizes_the_allowlist():
    r = client.post("/settings", json={
        "twilio_account_sid": SID, "twilio_auth_token": TOKEN,
        "twilio_from_number": "+1 (555) 010-0100",
        "sms_recipient_allowlist": "Sarah at the Chamber: +1 555 010 0124\n\nMe | +15550100125"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["twilio_account_sid"] == SID
    assert body["twilio_from_number"] == FROM
    assert body["twilio_auth_token_configured"] is True
    assert body["sms_recipient_allowlist"] == "Sarah at the Chamber = +15550100124\nMe = +15550100125"
    assert TOKEN not in r.text
    assert sms_service.resolve_recipient("me") == {"label": "Me", "e164": "+15550100125"}


def test_settings_api_refuses_a_bad_allowlist_line_and_saves_nothing():
    _configure()
    r = client.post("/settings", json={"sms_recipient_allowlist": "Sarah = 555-0124\nMe = +15550100125",
                                       "twilio_from_number": "+15550100101"})
    assert r.status_code == 400
    assert "Line 1" in r.json()["detail"]
    s = settings_service.load_settings()
    assert s["sms_recipient_allowlist"] == "Sarah at the Chamber = +15550100124\nMe = +15550100125"
    assert s["twilio_from_number"] == FROM
    r = client.post("/settings", json={"twilio_from_number": "555-0100"})
    assert r.status_code == 400 and "E.164" in r.json()["detail"]


def test_connection_test_reads_the_account_and_sends_nothing(monkeypatch):
    _configure()
    tw = _Twilio().install(monkeypatch)
    r = client.post("/settings/test-twilio")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "Nothing was sent" in body["detail"]
    assert "Ridian Test" in body["detail"]
    assert [req.method for req in tw.requests] == ["GET"]
    assert tw.requests[0].url.path.endswith(f"/Accounts/{SID}.json")
    assert TOKEN not in r.text


def test_connection_test_reports_bad_credentials_and_missing_config(monkeypatch):
    r = client.post("/settings/test-twilio").json()
    assert r["ok"] is False and r["source"] == "none" and "twilio_account_sid" in r["detail"]
    _configure()
    monkeypatch.setattr(sms_service, "_transport", httpx.MockTransport(
        lambda request: httpx.Response(401, json={"message": "Authenticate"})))
    r = client.post("/settings/test-twilio").json()
    assert r["ok"] is False and "rejected" in r["detail"]
