"""Morning brief (v6.0 Phase 2) — one assembled view, provably read-only.

Pins:
  1. every section populates from REAL records (deals seeded through the
     tools, invoices from the QBO service shape, approvals from persisted
     awaiting_input operations);
  2. empty sections are PRESENT and say so honestly — never omitted; an
     unreachable QuickBooks reads as UNKNOWN, never as zero;
  3. no writes: state store files are byte-identical before and after,
     both via the service and via the planner tool.
"""
import asyncio
import datetime as dt
import json

import pytest

from app.services import brief_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

TODAY = dt.date(2026, 8, 6)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


def _op(tmp_path):
    async def _emit(_ev):
        return None
    record = {"id": "op_brief", "steps": [], "tools_used": [], "artifacts": [],
              "errors": [], "user_stated_numbers": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _seed_pipeline(tmp_path):
    """Four deals through the real tools, then dates crafted around TODAY:
    overdue, due today, due in 3 days, quiet for 10 days, and one WON deal
    that must appear nowhere."""
    op = _op(tmp_path)
    for name in ("Ana Ortiz", "Ben Ide", "Cam Fox", "Dee Sol", "Eve Way"):
        _call("add_contact", name=name)
    _call("add_deal", contact="Ana Ortiz", title="Overdue deal", stage="proposal",
          next_action="Send contract", next_action_date="2026-08-04")
    _call("add_deal", contact="Ben Ide", title="Today deal", stage="meeting",
          next_action="Call Ben", next_action_date="2026-08-06")
    _call("add_deal", contact="Cam Fox", title="Week deal", stage="lead",
          next_action="Prep demo", next_action_date="2026-08-09")
    _call("add_deal", contact="Dee Sol", title="Quiet deal", stage="contacted")
    _call("add_deal", contact="Eve Way", title="Won deal", stage="won",
          next_action_date="2026-08-06")
    # Craft touch recency directly in the store: everyone fresh except Dee.
    deals = state_store.load_list("deals")
    for d in deals:
        d["last_touch_iso"] = ("2026-07-25T09:00:00" if d["title"] == "Quiet deal"
                               else "2026-08-05T09:00:00")
    state_store.save("deals", deals)
    return op


def _state_bytes():
    return {p.name: p.read_bytes() for p in sorted(
        state_store.STATE_DIR.glob("*.json"))}


# --------------------------------------------------------------------------
# Sections populate from real records
# --------------------------------------------------------------------------

def test_due_sections_split_today_and_week_and_flag_overdue(tmp_path):
    _seed_pipeline(tmp_path)
    s = brief_service.build_brief(today=TODAY)["sections"]
    today_titles = [r["title"] for r in s["due_today"]["items"]]
    assert today_titles == ["Overdue deal", "Today deal"]     # overdue joins today
    assert s["due_today"]["items"][0]["overdue"] is True
    assert s["due_today"]["items"][1]["overdue"] is False
    assert [r["title"] for r in s["due_this_week"]["items"]] == ["Week deal"]
    # The WON deal is nowhere despite its next_action_date being today.
    everywhere = json.dumps(s)
    assert "Won deal" not in everywhere


def test_stale_section_flags_only_the_quiet_deal(tmp_path):
    _seed_pipeline(tmp_path)
    s = brief_service.build_brief(today=TODAY)["sections"]
    assert [r["title"] for r in s["stale_deals"]["items"]] == ["Quiet deal"]


def test_unpaid_invoices_from_the_qbo_shape(tmp_path, monkeypatch):
    _seed_pipeline(tmp_path)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [
                            {"id": "9", "doc_number": "1042", "customer": "Sandy",
                             "date": "2026-08-01", "total": 4500.0,
                             "balance": 4500.0, "email_status": "NotSet"},
                            {"id": "8", "doc_number": "1041", "customer": "Paid Co",
                             "date": "2026-07-20", "total": 900.0,
                             "balance": 0, "email_status": "EmailSent"}])
    inv = brief_service.build_brief(today=TODAY)["sections"]["unpaid_invoices"]
    assert [i["doc_number"] for i in inv["items"]] == ["1042"]
    assert inv["empty"] is False and inv["unavailable"] is False


def test_awaiting_approval_lists_only_awaiting_input_runs(tmp_path):
    _op(tmp_path)
    state_store.save("operations", [
        {"id": "op_a", "status": "awaiting_input",
         "command": "Invoice Sandy for the discovery engagement",
         "started_at": "2026-08-06T08:00:00",
         "needs_input": [{"question": "Invoice preview — approve?"}]},
        {"id": "op_b", "status": "completed", "command": "Weekly review",
         "needs_input": [{"question": "answered long ago"}]},
    ])
    sec = brief_service.build_brief(today=TODAY)["sections"]["awaiting_approval"]
    assert len(sec["items"]) == 1
    assert sec["items"][0]["operation_id"] == "op_a"
    assert sec["items"][0]["question"] == "Invoice preview — approve?"


# --------------------------------------------------------------------------
# Honest empties + honest unavailability
# --------------------------------------------------------------------------

def test_empty_sections_are_present_and_say_so(tmp_path, monkeypatch):
    _op(tmp_path)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])
    # Phases 4-5 added today_events + needs_reply; with no Google connection
    # both report UNAVAILABLE (unknown) — the honest state. Here they are
    # forced to genuinely-empty so the empty-note contract is what's tested.
    monkeypatch.setattr(brief_service.calendar_service, "todays_events",
                        lambda today=None: [])
    monkeypatch.setattr(brief_service.inbox_service, "triage",
                        lambda **kw: {"needs_reply": [], "waiting_on": [],
                                      "gone_quiet": [], "from_contacts": [],
                                      "checked": 0})
    sections = brief_service.build_brief(today=TODAY)["sections"]
    assert set(sections) == {"obligations_due", "today_events", "needs_reply",
                             "due_today", "due_this_week", "stale_deals",
                             "unpaid_invoices", "awaiting_approval"}
    for name, sec in sections.items():
        assert sec["empty"] is True, name
        assert sec["items"] == [], name
        assert sec["note"], f"empty section {name} must SAY it is empty"


def test_unreachable_quickbooks_reads_unknown_not_zero(tmp_path, monkeypatch):
    _op(tmp_path)

    def boom(limit=20):
        raise RuntimeError("not connected")

    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices", boom)
    inv = brief_service.build_brief(today=TODAY)["sections"]["unpaid_invoices"]
    assert inv["unavailable"] is True
    assert "NOT zero" in inv["note"] and "not connected" in inv["note"]


# --------------------------------------------------------------------------
# Provably read-only
# --------------------------------------------------------------------------

def test_brief_never_writes_state(tmp_path, monkeypatch):
    _seed_pipeline(tmp_path)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])
    before = _state_bytes()
    snaps_before = [s["id"] for s in state_store.list_snapshots()]
    brief_service.build_brief(today=TODAY)
    out = _call("morning_brief")                     # the planner tool too
    assert "sections" in out
    assert _state_bytes() == before                  # byte-identical
    # And no new backup appeared — any write would have snapshotted first.
    assert [s["id"] for s in state_store.list_snapshots()] == snaps_before