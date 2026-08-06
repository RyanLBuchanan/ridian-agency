"""Google Calendar — READ ONLY (v6.0 Phase 4).

An assistant that can't see the week can't prioritize. This module reads
the operator's calendar and NOTHING else: the granted scope is
``calendar.readonly``, and by construction there is no create/insert/
update/delete/patch/move/import path anywhere in this file — pinned by
introspection the same way the QuickBooks create-only surface is pinned.
Event creation is deliberately out of scope for this phase.

Shape mirrors gmail_service exactly: one Google sign-in, credentials via
google_drive_service._load_credentials, a scope check the renderer can
poll, a real-API verify for the Settings row's Test button, and errors
raised as CalendarError with renderer-safe detail.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .google_drive_service import _load_credentials

log = logging.getLogger("ridian.calendar")

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# How far a bare range keyword reaches. Deterministic in code — the planner
# never invents a window. ORDER MATTERS: matching is substring-based, so the
# most specific keyword must be tested first ("next week" contains "week").
_RANGE_KEYS = ("next week", "this week", "tomorrow", "today", "week", "month")
_RANGE_DAYS = {"today": 0, "tomorrow": 1, "week": 7, "this week": 7,
               "next week": 14, "month": 30}
_DEFAULT_RANGE = "week"


class CalendarError(Exception):
    """Raised when a Calendar call is invalid or fails. ``detail`` is safe
    to surface (never tokens or attendee lists beyond names/emails the
    operator already has access to)."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def _build_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _has_calendar_scope(creds: Optional[Credentials]) -> bool:
    if not creds:
        return False
    return CALENDAR_SCOPE in set(creds.scopes or [])


def is_calendar_ready() -> bool:
    """True only if the saved token actually grants calendar.readonly."""
    try:
        creds = _load_credentials()
    except Exception:  # noqa: BLE001
        return False
    return _has_calendar_scope(creds)


def get_primary_calendar() -> Optional[dict]:
    """One real read (calendars.get on 'primary') proving access works.
    Returns {"id", "summary", "timezone"} or None. Used by the Settings
    row's Test button — verified-only, never inferred from a token."""
    creds = _load_credentials()
    if not _has_calendar_scope(creds):
        return None
    try:
        cal = _build_service(creds).calendars().get(calendarId="primary").execute()
        return {"id": cal.get("id", ""), "summary": cal.get("summary", ""),
                "timezone": cal.get("timeZone", "")}
    except Exception as exc:  # noqa: BLE001
        log.info("calendar.primary_lookup_failed type=%s", type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# Range resolution + event normalization (pure — no API, fully testable)
# ---------------------------------------------------------------------------

def resolve_range(range_text: str = "",
                  today: Optional[_dt.date] = None) -> tuple[_dt.date, _dt.date, str]:
    """Deterministic window from a keyword or an ISO date / date..date.
    Returns (start_date, end_date_inclusive, label). Unrecognized input
    falls back to the default week — never an unbounded fetch."""
    today = today or _dt.date.today()
    raw = str(range_text or "").strip().lower()
    if not raw:
        raw = _DEFAULT_RANGE
    if ".." in raw:
        a, _, b = raw.partition("..")
        try:
            start = _dt.date.fromisoformat(a.strip())
            end = _dt.date.fromisoformat(b.strip())
            if end < start:
                start, end = end, start
            return start, end, f"{start.isoformat()}..{end.isoformat()}"
        except ValueError:
            pass
    try:
        one = _dt.date.fromisoformat(raw)
        return one, one, one.isoformat()
    except ValueError:
        pass
    for key in _RANGE_KEYS:
        if key in raw:
            if key == "tomorrow":
                d = today + _dt.timedelta(days=1)
                return d, d, "tomorrow"
            if key == "today":
                return today, today, "today"
            if key == "next week":
                start = today + _dt.timedelta(days=7)
                return start, start + _dt.timedelta(days=6), "next week"
            return today, today + _dt.timedelta(days=_RANGE_DAYS[key]), key
    return today, today + _dt.timedelta(days=_RANGE_DAYS[_DEFAULT_RANGE]), _DEFAULT_RANGE


def _parse_edge(node: dict) -> tuple[str, bool]:
    """(iso string, all_day) from a Calendar start/end node."""
    if not isinstance(node, dict):
        return "", False
    if node.get("dateTime"):
        return str(node["dateTime"]), False
    if node.get("date"):
        return str(node["date"]), True
    return "", False


def _as_dt(iso: str, all_day_end: bool = False) -> Optional[_dt.datetime]:
    """Naive datetime for ordering/overlap. All-day dates become midnight
    (end-exclusive dates stay as given — Google already makes them
    exclusive). Timezone offsets are dropped to a common naive frame."""
    if not iso:
        return None
    try:
        if len(iso) == 10:
            d = _dt.date.fromisoformat(iso)
            return _dt.datetime.combine(d, _dt.time.min)
        return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def normalize_event(raw: dict) -> dict:
    start_iso, start_all = _parse_edge(raw.get("start") or {})
    end_iso, end_all = _parse_edge(raw.get("end") or {})
    return {
        "id": raw.get("id", ""),
        "summary": raw.get("summary", "(no title)"),
        "start": start_iso,
        "end": end_iso,
        "all_day": bool(start_all or end_all),
        "location": raw.get("location", ""),
        "status": raw.get("status", ""),
        "link": raw.get("htmlLink", ""),
        "attendees": [a.get("email", "") for a in (raw.get("attendees") or [])
                      if a.get("email")],
        "organizer": (raw.get("organizer") or {}).get("email", ""),
    }


def find_overlaps(events: list[dict]) -> list[dict]:
    """Pure conflict detection: every pair of TIMED events whose intervals
    overlap. All-day events never conflict (they don't block a slot), and
    cancelled events are ignored. Deterministic ordering."""
    timed = []
    for e in events:
        if e.get("all_day") or e.get("status") == "cancelled":
            continue
        s, t = _as_dt(e.get("start", "")), _as_dt(e.get("end", ""))
        if s and t and t > s:
            timed.append((s, t, e))
    timed.sort(key=lambda r: (r[0], r[1]))
    out = []
    for i in range(len(timed)):
        s1, e1, ev1 = timed[i]
        for j in range(i + 1, len(timed)):
            s2, e2, ev2 = timed[j]
            if s2 >= e1:
                break                     # sorted: nothing later can overlap
            out.append({
                "a": ev1, "b": ev2,
                "overlap_start": max(s1, s2).isoformat(timespec="minutes"),
                "overlap_end": min(e1, e2).isoformat(timespec="minutes"),
            })
    return out


# ---------------------------------------------------------------------------
# The only API surface: reads
# ---------------------------------------------------------------------------

def _fetch_events(time_min: str, time_max: str, max_results: int = 50) -> list[dict]:
    """RAW events().list — the single Calendar API call in this module.
    Tests monkeypatch this; everything above it is pure."""
    creds = _load_credentials()
    if not _has_calendar_scope(creds):
        raise CalendarError(
            "Calendar read access is not granted — Connect (or Reconnect) "
            "Google in Settings to grant it.", status=400)
    try:
        resp = _build_service(creds).events().list(
            calendarId="primary", timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime",
            maxResults=max(1, min(int(max_results), 250)),
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise CalendarError(f"Calendar read failed: {exc}", status=502) from exc
    return list(resp.get("items") or [])


def list_events(range_text: str = "", today: Optional[_dt.date] = None,
                max_results: int = 50) -> dict:
    """Normalized events in a resolved window. READ ONLY."""
    start, end, label = resolve_range(range_text, today=today)
    time_min = _dt.datetime.combine(start, _dt.time.min).isoformat() + "Z"
    time_max = (_dt.datetime.combine(end, _dt.time.min)
                + _dt.timedelta(days=1)).isoformat() + "Z"
    items = [normalize_event(e) for e in _fetch_events(time_min, time_max, max_results)]
    items = [e for e in items if e.get("status") != "cancelled"]
    # All-day events first (they frame the day rather than occupy a slot),
    # then timed events chronologically — the order every calendar UI uses.
    items.sort(key=lambda e: (0 if e.get("all_day") else 1, e.get("start") or ""))
    return {"range": label, "start": start.isoformat(), "end": end.isoformat(),
            "events": items, "count": len(items)}


def todays_events(today: Optional[_dt.date] = None) -> list[dict]:
    """Today's events, or [] when Calendar isn't connected — the morning
    brief calls this and must never fail the whole brief on a missing
    integration (it reports unavailability instead)."""
    return list_events("today", today=today)["events"]
