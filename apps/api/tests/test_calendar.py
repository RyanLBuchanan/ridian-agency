"""Calendar, READ ONLY (v6.0 Phase 4).

Pins:
  1. the calendar.readonly SCOPE is requested (and the read-WRITE scope
     never is);
  2. reads work: ranges resolve deterministically, events normalize
     (timed + all-day), cancelled events are dropped, conflicts are found
     among timed events only;
  3. NO WRITE PATH EXISTS — introspection over calendar_service and the
     tool registry, the same guarantee shape as the QuickBooks
     create-only pin;
  4. today's events join the morning brief, and a Calendar that is not
     connected reads as UNKNOWN there, never as "nothing today".
"""
import asyncio
import datetime as dt
import json

import pytest

from app.services import brief_service, calendar_service, google_drive_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

TODAY = dt.date(2026, 8, 6)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


def _op(tmp_path):
    async def _emit(_ev):
        return None
    record = {"id": "op_cal", "steps": [], "tools_used": [], "artifacts": [],
              "errors": [], "user_stated_numbers": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _raw(id_, summary, start, end, *, all_day=False, status="confirmed",
         location=""):
    key = "date" if all_day else "dateTime"
    return {"id": id_, "summary": summary, "status": status,
            "location": location, "htmlLink": f"https://cal/{id_}",
            "start": {key: start}, "end": {key: end},
            "attendees": [{"email": "sandy@gulf.test"}],
            "organizer": {"email": "ryan@ridiantechnologies.com"}}


_DAY = [
    _raw("e1", "Standup", "2026-08-06T09:00:00", "2026-08-06T09:15:00"),
    _raw("e2", "Sandy discovery call", "2026-08-06T10:00:00",
         "2026-08-06T11:00:00", location="Zoom"),
    _raw("e3", "Overlapping review", "2026-08-06T10:30:00",
         "2026-08-06T11:30:00"),
    _raw("e4", "Company holiday", "2026-08-06", "2026-08-07", all_day=True),
    _raw("e5", "Cancelled thing", "2026-08-06T14:00:00",
         "2026-08-06T15:00:00", status="cancelled"),
]


@pytest.fixture()
def cal(monkeypatch):
    """Fake the ONE raw API call; everything above it is real code."""
    calls = []

    def fake_fetch(time_min, time_max, max_results=50):
        calls.append({"time_min": time_min, "time_max": time_max,
                      "max_results": max_results})
        return list(_DAY)

    monkeypatch.setattr(calendar_service, "_fetch_events", fake_fetch)
    return calls


# --------------------------------------------------------------------------
# 1. Scope
# --------------------------------------------------------------------------

def test_calendar_readonly_scope_is_requested_and_write_scope_is_not():
    scopes = google_drive_service.SCOPES
    assert "https://www.googleapis.com/auth/calendar.readonly" in scopes
    assert calendar_service.CALENDAR_SCOPE in scopes
    # The read-WRITE calendar scopes must never appear.
    assert "https://www.googleapis.com/auth/calendar" not in scopes
    assert "https://www.googleapis.com/auth/calendar.events" not in scopes


def test_scope_check_gates_the_read(monkeypatch):
    class _Creds:
        scopes = ["https://www.googleapis.com/auth/drive.file"]

    monkeypatch.setattr(calendar_service, "_load_credentials", lambda: _Creds())
    assert calendar_service.is_calendar_ready() is False
    with pytest.raises(calendar_service.CalendarError) as exc:
        calendar_service._fetch_events("a", "b")
    assert "not granted" in exc.value.detail


# --------------------------------------------------------------------------
# 2. Reads
# --------------------------------------------------------------------------

def test_range_resolution_is_deterministic():
    r = calendar_service.resolve_range
    assert r("today", today=TODAY) == (TODAY, TODAY, "today")
    assert r("tomorrow", today=TODAY) == (dt.date(2026, 8, 7), dt.date(2026, 8, 7), "tomorrow")
    assert r("week", today=TODAY) == (TODAY, dt.date(2026, 8, 13), "week")
    assert r("next week", today=TODAY) == (dt.date(2026, 8, 13), dt.date(2026, 8, 19), "next week")
    assert r("2026-09-01", today=TODAY)[:2] == (dt.date(2026, 9, 1), dt.date(2026, 9, 1))
    assert r("2026-09-05..2026-09-01", today=TODAY)[:2] == (   # reversed span fixed
        dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    # Junk falls back to the bounded default — never an unbounded fetch.
    assert r("whenever", today=TODAY) == (TODAY, dt.date(2026, 8, 13), "week")


def test_list_events_normalizes_and_drops_cancelled(cal, tmp_path):
    _op(tmp_path)
    out = calendar_service.list_events("today", today=TODAY)
    assert out["range"] == "today" and out["count"] == 4      # e5 cancelled, gone
    summaries = [e["summary"] for e in out["events"]]
    assert "Cancelled thing" not in summaries
    holiday = next(e for e in out["events"] if e["id"] == "e4")
    assert holiday["all_day"] is True
    timed = next(e for e in out["events"] if e["id"] == "e2")
    assert timed["all_day"] is False and timed["location"] == "Zoom"
    assert timed["attendees"] == ["sandy@gulf.test"]
    # The window really was today only (end is exclusive next midnight).
    assert cal[0]["time_min"].startswith("2026-08-06T00:00")
    assert cal[0]["time_max"].startswith("2026-08-07T00:00")


def test_find_conflicts_ignores_all_day_and_cancelled(cal, tmp_path):
    _op(tmp_path)
    out = _call("find_conflicts", range="today")
    assert out["count"] == 1
    pair = {out["conflicts"][0]["a"]["id"], out["conflicts"][0]["b"]["id"]}
    assert pair == {"e2", "e3"}                      # the only true overlap
    assert out["conflicts"][0]["overlap_start"].endswith("10:30")
    assert out["conflicts"][0]["overlap_end"].endswith("11:00")


def test_whats_my_day_reports_conflicts_and_events(cal, tmp_path):
    _op(tmp_path)
    out = _call("whats_my_day")
    assert out["count"] == 4
    assert len(out["conflicts"]) == 1
    assert "now" in out and isinstance(out["upcoming_count"], int)


def test_tools_surface_a_disconnected_calendar_honestly(tmp_path, monkeypatch):
    _op(tmp_path)

    def boom(*a, **kw):
        raise calendar_service.CalendarError("Calendar read access is not granted")

    monkeypatch.setattr(calendar_service, "_fetch_events", boom)
    out = _call("list_events", range="today")
    assert out["reason"] == "calendar_unavailable"
    assert "not granted" in out["error"]


# --------------------------------------------------------------------------
# 3. THE no-write guarantee (introspection — the QBO pin's shape)
# --------------------------------------------------------------------------

def test_no_write_path_exists_in_the_calendar_surface():
    forbidden = ("create", "insert", "update", "delete", "patch", "move",
                 "import", "cancel", "send", "quick_add", "quickadd")
    public = [n for n in dir(calendar_service) if not n.startswith("_")]
    for name in public:
        low = name.lower()
        assert not any(f in low for f in forbidden), f"write-shaped name: {name}"
    # The module's own source contains no mutating Calendar API call.
    import inspect
    src = inspect.getsource(calendar_service)
    for verb in (".insert(", ".update(", ".delete(", ".patch(", ".move(",
                 ".quickAdd(", ".import_("):
        assert verb not in src, f"mutating API call present: {verb}"
    # EVERY events() reference — code or prose — is a .list read. A future
    # events().insert() fails here even before it is wired to a tool.
    import re
    assert re.findall(r"events\(\)(?!\.list)", src) == []


def test_calendar_tool_registry_is_read_only():
    names = sorted(x.name for x in t.PLANNER_TOOLS
                   if x.name in ("list_events", "whats_my_day", "find_conflicts"))
    assert names == ["find_conflicts", "list_events", "whats_my_day"]
    # No registered tool is calendar-write shaped.
    for tool in t.PLANNER_TOOLS:
        low = tool.name.lower()
        if "event" in low or "calendar" in low:
            assert not any(v in low for v in ("create", "add", "delete", "update",
                                              "move", "cancel", "schedule")), tool.name


# --------------------------------------------------------------------------
# 4. The morning brief
# --------------------------------------------------------------------------

def test_todays_events_join_the_morning_brief(cal, tmp_path, monkeypatch):
    _op(tmp_path)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])
    sec = brief_service.build_brief(today=TODAY)["sections"]["today_events"]
    assert sec["empty"] is False and sec["unavailable"] is False
    # All-day first, then timed chronologically (the calendar-UI convention).
    assert [e["summary"] for e in sec["items"]] == [
        "Company holiday", "Standup", "Sandy discovery call", "Overlapping review"]


def test_disconnected_calendar_reads_unknown_in_the_brief(tmp_path, monkeypatch):
    _op(tmp_path)
    monkeypatch.setattr(brief_service.quickbooks_service, "list_invoices",
                        lambda limit=20: [])

    def boom(*a, **kw):
        raise calendar_service.CalendarError("Calendar read access is not granted")

    monkeypatch.setattr(calendar_service, "_fetch_events", boom)
    sec = brief_service.build_brief(today=TODAY)["sections"]["today_events"]
    assert sec["unavailable"] is True
    assert "NOT none" in sec["note"] and "not granted" in sec["note"]