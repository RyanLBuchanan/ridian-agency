"""Inbox triage, READ ONLY (v6.0 Phase 5).

Pins:
  1. classification is DETERMINISTIC given fixed input — the same threads
     and clock always produce the same four buckets;
  2. the contacts store is the cross-reference: exact address match only,
     and pipeline mail surfaces FIRST in every bucket;
  3. NO WRITES, NO SENDS — introspection over inbox_service (no send /
     modify / trash / label path, only threads().list and threads().get),
     the tool registry, and a run that proves no state file changed;
  4. message BODIES are never read (metadata format, headers only);
  5. triage joins the morning brief, and an unreachable Gmail reads there
     as UNKNOWN, never as zero.
"""
import asyncio
import datetime as dt
import json

import pytest

from app.services import (brief_service, google_drive_service, inbox_service,
                          state_store)
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

NOW = dt.datetime(2026, 8, 6, 9, 0, 0)
ME = "ryan@ridiantechnologies.com"


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


def _op(tmp_path):
    async def _emit(_ev):
        return None
    record = {"id": "op_inbox", "steps": [], "tools_used": [], "artifacts": [],
              "errors": [], "user_stated_numbers": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _ms(d: dt.datetime) -> str:
    return str(int(d.timestamp() * 1000))


def _msg(frm, to, subject, when, snippet="…", labels=("INBOX",)):
    return {"internalDate": _ms(when), "snippet": snippet,
            "labelIds": list(labels),
            "payload": {"headers": [
                {"name": "From", "value": frm}, {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": when.isoformat()}]}}


# Four threads covering every bucket, with a fixed clock of 2026-08-06 09:00.
_THREADS = {
    # They spoke last, yesterday, and they're a PIPELINE contact.
    "t1": {"id": "t1", "messages": [
        _msg(ME, "Sandy Alvarez <sandy@gulf.test>", "Discovery scope",
             dt.datetime(2026, 8, 4, 10, 0)),
        _msg("Sandy Alvarez <sandy@gulf.test>", ME, "Re: Discovery scope",
             dt.datetime(2026, 8, 5, 16, 0), labels=("INBOX", "UNREAD"))]},
    # I spoke last, 2 days ago — waiting on them. Known contact, NOT in pipeline.
    "t2": {"id": "t2", "messages": [
        _msg("Casey Reed <casey@reed.test>", ME, "Invoice question",
             dt.datetime(2026, 8, 3, 9, 0)),
        _msg(ME, "casey@reed.test", "Re: Invoice question",
             dt.datetime(2026, 8, 4, 9, 0))]},
    # A stranger spoke last, 20 days ago — needs reply AND gone quiet.
    "t3": {"id": "t3", "messages": [
        _msg("Random Vendor <sales@vendor.test>", ME, "Partnership?",
             dt.datetime(2026, 7, 17, 8, 0))]},
    # I spoke last, 30 days ago — waiting on them AND gone quiet.
    "t4": {"id": "t4", "messages": [
        _msg(ME, "quiet@client.test", "Following up", dt.datetime(2026, 7, 7, 8, 0))]},
}


@pytest.fixture()
def gmail(monkeypatch):
    """Fake ONLY the two raw API calls; all logic under test is real."""
    calls = {"list": [], "get": []}

    def fake_ids(query, max_threads):
        calls["list"].append({"query": query, "max_threads": max_threads})
        return list(_THREADS)

    def fake_thread(tid):
        calls["get"].append(tid)
        return _THREADS[tid]

    monkeypatch.setattr(inbox_service, "_fetch_thread_ids", fake_ids)
    monkeypatch.setattr(inbox_service, "_fetch_thread", fake_thread)
    monkeypatch.setattr(inbox_service, "_me_address", lambda: ME)
    return calls


def _seed_contacts(tmp_path):
    """Sandy has an ACTIVE deal (pipeline); Casey is a contact with none."""
    _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez", email="sandy@gulf.test",
          company="Gulf Realty")
    _call("add_contact", name="Casey Reed", email="casey@reed.test")
    _call("add_deal", contact="Sandy Alvarez", title="AI discovery",
          stage="proposal", value_usd="4500")


# --------------------------------------------------------------------------
# 1 + 2. Deterministic classification, contact cross-reference
# --------------------------------------------------------------------------

def test_classification_is_deterministic_and_correct(gmail, tmp_path):
    _seed_contacts(tmp_path)
    a = inbox_service.triage(now=NOW)
    b = inbox_service.triage(now=NOW)
    assert a == b                                    # same input, same output

    assert [r["id"] for r in a["needs_reply"]] == ["t1", "t3"]   # they spoke last
    assert [r["id"] for r in a["waiting_on"]] == ["t2", "t4"]    # I spoke last
    assert sorted(r["id"] for r in a["gone_quiet"]) == ["t3", "t4"]   # 7+ days
    assert a["checked"] == 4 and a["quiet_after_days"] == 7
    # Day math is computed from the injected clock, not the wall clock.
    assert next(r for r in a["needs_reply"] if r["id"] == "t1")["days_quiet"] == 0
    assert next(r for r in a["gone_quiet"] if r["id"] == "t3")["days_quiet"] == 20


def test_contact_cross_reference_and_pipeline_first(gmail, tmp_path):
    _seed_contacts(tmp_path)
    out = inbox_service.triage(now=NOW)
    ids = [r["id"] for r in out["from_contacts"]]
    assert ids == ["t1", "t2"]                       # pipeline contact FIRST
    sandy = out["from_contacts"][0]["contact"]
    assert sandy["name"] == "Sandy Alvarez" and sandy["in_pipeline"] is True
    casey = out["from_contacts"][1]["contact"]
    assert casey["name"] == "Casey Reed" and casey["in_pipeline"] is False
    # Strangers carry no contact at all.
    assert next(r for r in out["needs_reply"] if r["id"] == "t3")["contact"] is None
    # And pipeline mail leads needs_reply even though t3 is older.
    assert out["needs_reply"][0]["id"] == "t1"


def test_lookalike_address_is_not_a_known_contact(gmail, tmp_path, monkeypatch):
    """Exact address match only — a lookalike domain never counts."""
    _seed_contacts(tmp_path)
    fake = {"id": "t9", "messages": [
        _msg("Sandy Alvarez <sandy@gulf.test.evil.com>", ME, "Wire instructions",
             dt.datetime(2026, 8, 5, 12, 0))]}
    monkeypatch.setattr(inbox_service, "_fetch_thread_ids",
                        lambda q, m: ["t9"])
    monkeypatch.setattr(inbox_service, "_fetch_thread", lambda tid: fake)
    out = inbox_service.triage(now=NOW)
    assert out["from_contacts"] == []
    assert out["needs_reply"][0]["contact"] is None


def test_no_contacts_store_still_classifies(gmail, tmp_path):
    _op(tmp_path)                                    # no contacts seeded
    out = inbox_service.triage(now=NOW)
    assert out["from_contacts"] == []
    assert len(out["needs_reply"]) == 2 and len(out["waiting_on"]) == 2


# --------------------------------------------------------------------------
# 3 + 4. Read-only: no writes, no sends, no bodies
# --------------------------------------------------------------------------

def test_no_send_or_modify_path_exists_in_the_triage_surface():
    forbidden = ("send", "modify", "trash", "delete", "insert", "update",
                 "batch_modify", "batchmodify", "label", "archive", "reply",
                 "draft", "import", "forward")
    public = [n for n in dir(inbox_service) if not n.startswith("_")]
    for name in public:
        assert not any(f in name.lower() for f in forbidden), f"write-shaped: {name}"
    import inspect
    import re
    src = inspect.getsource(inbox_service)
    for verb in (".send(", ".modify(", ".trash(", ".untrash(", ".delete(",
                 ".batchModify(", ".insert(", ".drafts("):
        assert verb not in src, f"mutating Gmail call present: {verb}"
    # Every threads() reference is a .list or .get read.
    assert re.findall(r"threads\(\)(?!\.(list|get))", src) == []
    # Bodies are never requested: metadata format only.
    assert 'format="metadata"' in src and '"full"' not in src


def test_triage_tool_is_registered_and_writes_no_state(gmail, tmp_path):
    _seed_contacts(tmp_path)
    before = {p.name: p.read_bytes() for p in
              sorted(state_store.STATE_DIR.glob("*.json"))}
    snaps_before = [s["id"] for s in state_store.list_snapshots()]
    out = _call("triage_inbox")
    assert out["checked"] == 4
    after = {p.name: p.read_bytes() for p in
             sorted(state_store.STATE_DIR.glob("*.json"))}
    assert after == before                           # byte-identical
    assert [s["id"] for s in state_store.list_snapshots()] == snaps_before


def test_gmail_read_scope_is_requested():
    scopes = google_drive_service.SCOPES
    assert inbox_service.GMAIL_READ_SCOPE in scopes
    # The full-mailbox-control scopes are never requested.
    assert "https://www.googleapis.com/auth/gmail.modify" not in scopes
    assert "https://www.googleapis.com/auth/gmail.send" not in scopes
    assert "https://www.googleapis.com/auth/gmail" not in scopes


def test_scope_check_gates_the_read(monkeypatch):
    class _Creds:
        scopes = ["https://www.googleapis.com/auth/gmail.compose"]

    monkeypatch.setattr(inbox_service, "_load_credentials", lambda: _Creds())
    assert inbox_service.is_inbox_ready() is False
    with pytest.raises(inbox_service.InboxError) as exc:
        inbox_service._fetch_thread_ids("in:inbox", 5)
    assert "not granted" in exc.value.detail


def test_the_fetch_is_bounded(gmail, tmp_path):
    _op(tmp_path)
    inbox_service.triage(now=NOW)
    q = gmail["list"][0]
    assert "in:inbox" in q["query"] and "newer_than:30d" in q["query"]
    assert q["max_threads"] == inbox_service.DEFAULT_MAX_THREADS


def test_disconnected_gmail_surfaces_honestly(tmp_path, monkeypatch):
    _op(tmp_path)

    def boom(*a, **kw):
        raise inbox_service.InboxError("Gmail read access is not granted")

    monkeypatch.setattr(inbox_service, "_fetch_thread_ids", boom)
    out = _call("triage_inbox")
    assert out["reason"] == "inbox_unavailable" and "not granted" in out["error"]


# --------------------------------------------------------------------------
# 5. The morning brief
# --------------------------------------------------------------------------

def test_needs_reply_joins_the_morning_brief(gmail, tmp_path, monkeypatch):
    _seed_contacts(tmp_path)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])
    monkeypatch.setattr(brief_service.calendar_service, "todays_events",
                        lambda today=None: [])
    sec = brief_service.build_brief(today=dt.date(2026, 8, 6))["sections"]["needs_reply"]
    assert sec["empty"] is False and sec["unavailable"] is False
    assert [r["id"] for r in sec["items"]] == ["t1", "t3"]


def test_unreachable_gmail_reads_unknown_in_the_brief(tmp_path, monkeypatch):
    _op(tmp_path)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])
    monkeypatch.setattr(brief_service.calendar_service, "todays_events",
                        lambda today=None: [])

    def boom(*a, **kw):
        raise inbox_service.InboxError("Gmail read access is not granted")

    monkeypatch.setattr(inbox_service, "_fetch_thread_ids", boom)
    sec = brief_service.build_brief(today=dt.date(2026, 8, 6))["sections"]["needs_reply"]
    assert sec["unavailable"] is True
    assert "NOT zero" in sec["note"] and "not granted" in sec["note"]