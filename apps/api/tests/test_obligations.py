"""Recurring obligations (v6.8) — computed on demand, never autonomous.

Pins:
  1. business-day math incl. US federal holidays with observed shifts
     (weekend 1sts, Labor Day, New Year's Friday, Dec-31 observation);
  2. the Greg scenario end-to-end: caught-up at creation, due on the
     first business day, OVERDUE (not skipped) after a week closed,
     complete records the period, the same month never fires twice, and
     the next month fires again;
  3. missed periods surface honestly on ONE card; dismiss kills only the
     current occurrence;
  4. THE autonomy pin: an obligation coming due cannot create anything —
     the module imports no tool services, and with every artifact writer
     rigged to explode, brief generation + due computation run clean,
     stage nothing, and leave business state byte-identical;
  5. CRUD refuses bad input before writing; records are provenance-stamped;
  6. brief section present with an honest empty note;
  7. cancelled is its own renderer state — never "Failed".
"""
import datetime as dt
import inspect
import json
from pathlib import Path

import pytest

from app.services import brief_service, obligations_service as ob, state_store

_RENDERER = Path(__file__).resolve().parents[3] / "desktop" / "renderer"

AUG20 = dt.date(2026, 8, 20)          # creation day (Thursday)
SEP1 = dt.date(2026, 9, 1)            # first business day of Sep 2026 (Tue)
OCT1 = dt.date(2026, 10, 1)           # first business day of Oct 2026 (Thu)

GREG = {"name": "WRN Monthly Support Retainer — Greg Alexander",
        "task": "Invoice Greg Alexander $1,000 for the WRN Monthly Support "
                "Retainer, Net 15",
        "cadence": {"kind": "monthly_first_business_day"}}


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


# ==========================================================================
# 1. Business-day math
# ==========================================================================

def test_first_business_day_skips_weekends_and_federal_holidays():
    assert dt.date(2026, 8, 1).weekday() == 5           # Saturday
    assert ob.first_business_day(2026, 8) == dt.date(2026, 8, 3)
    # Labor Day: Sep 1 2025 is Monday AND Labor Day → Tuesday.
    assert ob.first_business_day(2025, 9) == dt.date(2025, 9, 2)
    # New Year's Day 2027 is a Friday → the following Monday.
    assert ob.first_business_day(2027, 1) == dt.date(2027, 1, 4)
    # An ordinary month is just the 1st.
    assert ob.first_business_day(2026, 9) == dt.date(2026, 9, 1)


def test_observed_holiday_shifts():
    # July 4 2026 is a Saturday → observed Friday July 3.
    assert not ob.is_business_day(dt.date(2026, 7, 3))
    assert ob.first_business_day(2026, 7) == dt.date(2026, 7, 1)  # unaffected
    # New Year's 2028 is a Saturday → Dec 31 2027 (Friday) is observed.
    assert not ob.is_business_day(dt.date(2027, 12, 31))


def test_monthly_day_clamps_to_month_length():
    assert ob._month_day_clamped(2027, 2, 31) == dt.date(2027, 2, 28)
    assert ob._month_day_clamped(2028, 2, 31) == dt.date(2028, 2, 29)  # leap
    assert ob._month_day_clamped(2026, 4, 31) == dt.date(2026, 4, 30)
    assert ob._month_day_clamped(2026, 4, 15) == dt.date(2026, 4, 15)


# ==========================================================================
# 2. The Greg scenario
# ==========================================================================

def _add_greg(today=AUG20):
    return ob.add_obligation(dict(GREG), written_by="manual", today=today)


def test_greg_caught_up_at_creation_then_due_then_overdue():
    greg = _add_greg(today=AUG20)
    assert greg["written_by"] == "manual"               # provenance stamped
    # Created Aug 20: August's occurrence (Aug 3) is behind us — caught up,
    # nothing fires retroactively.
    assert ob.due_status(greg, AUG20) is None
    assert ob.next_due(greg, AUG20) == "2026-09-01"
    # Sep 1: due today.
    due = ob.due_status(greg, SEP1)
    assert due == {"due_date": "2026-09-01", "status": "due_today",
                   "days_overdue": 0, "missed_periods": 0}
    # App closed for a week: NOT silently skipped — overdue, loudly.
    due = ob.due_status(greg, dt.date(2026, 9, 8))
    assert due["status"] == "overdue" and due["days_overdue"] == 7
    assert due["due_date"] == "2026-09-01"


def test_completing_records_the_period_and_never_fires_twice():
    _add_greg()
    greg_id = ob.list_obligations()[0]["id"]
    ob.mark_complete(greg_id, today=dt.date(2026, 9, 8))
    greg = ob.list_obligations()[0]
    assert greg["last_completed_period"] == "2026-09-01"
    assert ob.due_status(greg, dt.date(2026, 9, 8)) is None    # gone
    assert ob.due_status(greg, dt.date(2026, 9, 30)) is None   # ALL month
    # Completing again with nothing due REFUSES — the same period can
    # never be completed (or fired) twice.
    with pytest.raises(ob.ObligationError):
        ob.mark_complete(greg_id, today=dt.date(2026, 9, 8))
    # October fires on its own first business day.
    assert ob.due_status(greg, OCT1)["status"] == "due_today"


def test_missed_periods_surface_on_one_card_honestly():
    greg = ob.add_obligation(dict(GREG), written_by="manual",
                             today=dt.date(2026, 5, 20))       # caught up May
    due = ob.due_status(greg, dt.date(2026, 8, 10))
    assert due["due_date"] == "2026-08-03"              # most recent occurrence
    assert due["missed_periods"] == 2                   # Jun 1, Jul 1 missed
    # Completing catches the schedule up entirely.
    ob.mark_complete(greg["id"], today=dt.date(2026, 8, 10))
    assert ob.due_status(ob.list_obligations()[0], dt.date(2026, 8, 20)) is None


def test_dismiss_kills_only_the_current_occurrence():
    _add_greg()
    greg_id = ob.list_obligations()[0]["id"]
    ob.dismiss_occurrence(greg_id, today=SEP1)
    greg = ob.list_obligations()[0]
    assert ob.due_status(greg, dt.date(2026, 9, 15)) is None   # this one gone
    assert ob.due_status(greg, OCT1)["status"] == "due_today"  # next returns


# ==========================================================================
# 3. Other cadences
# ==========================================================================

def test_weekly_and_once_and_monthly_day():
    wk = ob.add_obligation({"name": "Weekly review", "task": "Run the weekly review",
                            "cadence": {"kind": "weekly", "weekday": 0}},
                           written_by="manual", today=dt.date(2026, 8, 20))
    assert ob.due_status(wk, dt.date(2026, 8, 24))["status"] == "due_today"  # Mon
    once = ob.add_obligation({"name": "File the annual report", "task": "File it",
                              "cadence": {"kind": "once", "date": "2026-08-01"}},
                             written_by="manual", today=dt.date(2026, 8, 20))
    due = ob.due_status(once, dt.date(2026, 8, 20))
    assert due["status"] == "overdue"                   # past date STILL owed
    ob.mark_complete(once["id"], today=dt.date(2026, 8, 20))
    assert ob.due_status(ob.list_obligations()[0], dt.date(2027, 1, 1)) is None
    md = ob.add_obligation({"name": "Rent", "task": "Pay rent",
                            "cadence": {"kind": "monthly_day", "day": 31}},
                           written_by="manual", today=dt.date(2027, 1, 15))
    assert ob.due_status(md, dt.date(2027, 2, 28))["due_date"] == "2027-02-28"


# ==========================================================================
# 4. THE autonomy pin — an obligation coming due creates NOTHING
# ==========================================================================

def test_obligations_module_cannot_reach_any_tool():
    src = inspect.getsource(ob)
    for forbidden in ("quickbooks", "gmail", "operator_tools", "create_invoice",
                      "create_draft", "httpx"):
        assert forbidden not in src, f"obligations_service references {forbidden}"


def test_due_obligation_produces_no_artifact_and_stages_nothing(monkeypatch):
    _add_greg()

    def bomb(*a, **kw):
        raise AssertionError("AUTONOMOUS ARTIFACT — an obligation created something")

    from app.services import gmail_service, quickbooks_service
    monkeypatch.setattr(quickbooks_service, "create_invoice", bomb)
    monkeypatch.setattr(gmail_service, "create_draft", bomb)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])
    monkeypatch.setattr(brief_service.calendar_service, "todays_events",
                        lambda today=None: [])
    monkeypatch.setattr(brief_service.inbox_service, "triage",
                        lambda **kw: {"needs_reply": []})

    before = {p.name: p.read_bytes() for p in sorted(
        state_store.STATE_DIR.glob("*.json"))}
    # The obligation IS due — and everything that surfaces it runs clean.
    brief = brief_service.build_brief(today=SEP1)
    sec = brief["sections"]["obligations_due"]
    assert sec["items"][0]["name"].startswith("WRN Monthly Support")
    assert sec["items"][0]["status"] == "due_today"
    assert ob.due_obligations(today=dt.date(2026, 9, 8))[0]["days_overdue"] == 7
    # No invoice, no draft, no staged approval, no state change.
    after = {p.name: p.read_bytes() for p in sorted(
        state_store.STATE_DIR.glob("*.json"))}
    assert after == before
    assert state_store.load_list("approvals") == []


# ==========================================================================
# 5. CRUD refusals + 6. brief section
# ==========================================================================

def test_crud_refuses_bad_input_before_writing():
    for bad in (
        {"name": "", "task": "x", "cadence": {"kind": "weekly", "weekday": 0}},
        {"name": "x", "task": "", "cadence": {"kind": "weekly", "weekday": 0}},
        {"name": "x", "task": "x", "cadence": {"kind": "quarterly"}},
        {"name": "x", "task": "x", "cadence": {"kind": "monthly_day", "day": 0}},
        {"name": "x", "task": "x", "cadence": {"kind": "monthly_day", "day": 32}},
        {"name": "x", "task": "x", "cadence": {"kind": "weekly", "weekday": 7}},
        {"name": "x", "task": "x", "cadence": {"kind": "once", "date": "soon"}},
    ):
        with pytest.raises(ob.ObligationError):
            ob.add_obligation(bad, written_by="manual")
    assert ob.list_obligations() == []                  # nothing written
    greg = _add_greg()
    assert ob.update_obligation(greg["id"], {"cadence": {"kind": "monthly_day",
                                                         "day": 15}})
    assert ob.list_obligations()[0]["cadence"]["day"] == 15
    assert ob.delete_obligation(greg["id"]) is True
    assert ob.list_obligations() == []


def test_brief_section_present_with_honest_empty_note(monkeypatch):
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])
    monkeypatch.setattr(brief_service.calendar_service, "todays_events",
                        lambda today=None: [])
    monkeypatch.setattr(brief_service.inbox_service, "triage",
                        lambda **kw: {"needs_reply": []})
    sec = brief_service.build_brief(today=AUG20)["sections"]["obligations_due"]
    assert sec["empty"] is True and sec["note"] == "No obligations are due."


# ==========================================================================
# 7. Cancelled is not failed
# ==========================================================================

def test_cancelled_is_its_own_renderer_state():
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "'is-cancelled'" in app_js
    assert "'Cancelled'" in app_js
    cancel_fn = app_js.split("async function _opCancelPendingTask", 1)[1].split("\n}", 1)[0]
    assert "_opSetStatusDot('cancelled')" in cancel_fn
    assert "_opSetStatusDot('failed')" not in cancel_fn
    # The obligations view participates in the single-view manager.
    assert "'obligations-view'" in app_js.split("WORKSPACE_VIEW_IDS", 1)[1][:200]
    html = (_RENDERER / "index.html").read_text(encoding="utf-8")
    assert "operator-obligations-strip" in html          # persistent banner