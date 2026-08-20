"""Recurring obligations (v6.8) — reminders computed on demand, never timed.

ARCHITECTURE, both constraints structural:
  1. NO timer, NO cron. Nothing here schedules anything. due status is a
     PURE function of (obligation record, today) computed whenever asked —
     app launch, morning brief, the obligations view. A week of the app
     being closed changes nothing: the missed occurrence is still the most
     recent occurrence <= today and still surfaces, as overdue.
  2. NOTHING here creates artifacts. This module reads and writes ONLY the
     obligations store. It cannot reach QuickBooks, Gmail, or any tool —
     no imports exist for them (pinned by test). An obligation coming due
     produces a ROW; starting the task is the operator's click, and the
     task then walks every existing gate.

Cadences: monthly_first_business_day | monthly_day (clamped to month
length; NOT business-day shifted) | weekly (weekday 0=Mon) | once (date).
Business days skip Sat/Sun and US FEDERAL holidays (the 11, with observed
shifts). State/bank holidays are NOT modeled.

Missed periods: only the MOST RECENT missed occurrence surfaces, carrying
missed_periods (how many earlier occurrences were never completed) so a
long gap is reported honestly without a wall of cards. Completing records
that occurrence — the schedule catches up, and the same period can never
fire twice. Creation initializes the schedule as caught-up (nothing fires
retroactively for periods before the obligation existed); "once" is the
exception — a past date fires immediately as overdue.
"""

from __future__ import annotations

import datetime as _dt
import logging
import uuid
from typing import Optional

from . import state_store
from .memory_service import _stamp

log = logging.getLogger("ridian.obligations")

_STORE = "obligations"
CADENCE_KINDS = ("monthly_first_business_day", "monthly_day", "weekly", "once")
_SCAN_MONTHS = 120          # sanity bound for occurrence scans


class ObligationError(ValueError):
    """Deterministic refusal — raised BEFORE any write happens."""


# ---------------------------------------------------------------------------
# Business-day math (pure)
# ---------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    d = _dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + _dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _dt.date:
    nxt = (_dt.date(year, month, 28) + _dt.timedelta(days=4)).replace(day=1)
    d = nxt - _dt.timedelta(days=1)
    return d - _dt.timedelta(days=(d.weekday() - weekday) % 7)


def federal_holidays(year: int) -> frozenset:
    """US federal holidays for ``year`` incl. OBSERVED dates (a Saturday
    holiday observes Friday; a Sunday one observes Monday). Includes the
    Dec-31 observation when NEXT year's New Year's Day is a Saturday."""
    days: set = set()

    def _fixed(y: int, m: int, d: int) -> None:
        dt = _dt.date(y, m, d)
        days.add(dt)
        if dt.weekday() == 5:
            days.add(dt - _dt.timedelta(days=1))
        elif dt.weekday() == 6:
            days.add(dt + _dt.timedelta(days=1))

    for m, d in ((1, 1), (6, 19), (7, 4), (11, 11), (12, 25)):
        _fixed(year, m, d)
    if _dt.date(year + 1, 1, 1).weekday() == 5:      # next NYD observed Dec 31
        days.add(_dt.date(year, 12, 31))
    days.add(_nth_weekday(year, 1, 0, 3))            # MLK Day
    days.add(_nth_weekday(year, 2, 0, 3))            # Washington's Birthday
    days.add(_last_weekday(year, 5, 0))              # Memorial Day
    days.add(_nth_weekday(year, 9, 0, 1))            # Labor Day
    days.add(_nth_weekday(year, 10, 0, 2))           # Columbus Day
    days.add(_nth_weekday(year, 11, 3, 4))           # Thanksgiving
    # _fixed() for Jan 1 can add Dec 31 of the PRIOR year — that observation
    # belongs to the prior year's set (added there by the explicit branch).
    return frozenset(d for d in days if d.year == year)


def is_business_day(d: _dt.date) -> bool:
    return d.weekday() < 5 and d not in federal_holidays(d.year)


def first_business_day(year: int, month: int) -> _dt.date:
    d = _dt.date(year, month, 1)
    while not is_business_day(d):
        d += _dt.timedelta(days=1)
    return d


def _month_day_clamped(year: int, month: int, day: int) -> _dt.date:
    nxt = (_dt.date(year, month, 28) + _dt.timedelta(days=4)).replace(day=1)
    last = (nxt - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(int(day), last))


# ---------------------------------------------------------------------------
# Occurrence math (pure)
# ---------------------------------------------------------------------------

def _validate_cadence(cadence: dict) -> dict:
    kind = str((cadence or {}).get("kind") or "")
    if kind not in CADENCE_KINDS:
        raise ObligationError(
            f"cadence.kind must be one of {'/'.join(CADENCE_KINDS)} — got {kind!r}.")
    out: dict = {"kind": kind}
    if kind == "monthly_day":
        try:
            day = int(cadence.get("day"))
        except (TypeError, ValueError):
            raise ObligationError("monthly_day needs a numeric 'day' (1-31).")
        if not 1 <= day <= 31:
            raise ObligationError("monthly_day 'day' must be 1-31.")
        out["day"] = day
    elif kind == "weekly":
        try:
            wd = int(cadence.get("weekday"))
        except (TypeError, ValueError):
            raise ObligationError("weekly needs a numeric 'weekday' (0=Mon..6=Sun).")
        if not 0 <= wd <= 6:
            raise ObligationError("weekly 'weekday' must be 0-6.")
        out["weekday"] = wd
    elif kind == "once":
        try:
            out["date"] = _dt.date.fromisoformat(str(cadence.get("date"))).isoformat()
        except (TypeError, ValueError):
            raise ObligationError("once needs an ISO 'date' (YYYY-MM-DD).")
    return out


def _occurrence_for_month(cadence: dict, year: int, month: int) -> _dt.date:
    if cadence["kind"] == "monthly_first_business_day":
        return first_business_day(year, month)
    return _month_day_clamped(year, month, cadence["day"])


def occurrences_up_to(cadence: dict, after: Optional[_dt.date],
                      up_to: _dt.date) -> list:
    """Occurrence dates D with after < D <= up_to, oldest first. ``after``
    None means 'from the beginning' (bounded by _SCAN_MONTHS)."""
    kind = cadence["kind"]
    if kind == "once":
        d = _dt.date.fromisoformat(cadence["date"])
        return [d] if (after is None or d > after) and d <= up_to else []
    if kind == "weekly":
        # Latest matching weekday <= up_to, then walk back.
        end = up_to - _dt.timedelta(days=(up_to.weekday() - cadence["weekday"]) % 7)
        floor = after or (up_to - _dt.timedelta(weeks=_SCAN_MONTHS * 5))
        out = []
        d = end
        while d > floor:
            out.append(d)
            d -= _dt.timedelta(days=7)
        return list(reversed(out))
    # Monthly kinds: walk months.
    out = []
    year, month = up_to.year, up_to.month
    for _ in range(_SCAN_MONTHS):
        occ = _occurrence_for_month(cadence, year, month)
        if occ <= up_to and (after is None or occ > after):
            out.append(occ)
        if after is not None and occ <= after:
            break
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(out)


def _parse_date(value: str) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def due_status(ob: dict, today: Optional[_dt.date] = None) -> Optional[dict]:
    """None when nothing is due; else {due_date, status, days_overdue,
    missed_periods}. PURE — reads only the record and the calendar."""
    today = today or _dt.date.today()
    last = _parse_date(ob.get("last_completed_period", ""))
    occ = occurrences_up_to(ob.get("cadence") or {}, last, today)
    dismissed = _parse_date(ob.get("dismissed_period", ""))
    occ = [d for d in occ if d != dismissed]
    if not occ:
        return None
    current = occ[-1]
    return {
        "due_date": current.isoformat(),
        "status": "due_today" if current == today else "overdue",
        "days_overdue": (today - current).days,
        "missed_periods": len(occ) - 1,
    }


def next_due(ob: dict, today: Optional[_dt.date] = None) -> str:
    """The next occurrence STRICTLY after max(today, last completed) — what
    the UI shows as 'next due' for a caught-up obligation."""
    today = today or _dt.date.today()
    cadence = ob.get("cadence") or {}
    last = _parse_date(ob.get("last_completed_period", ""))
    floor = max(today, last) if last else today
    horizon = floor + _dt.timedelta(days=800)
    kind = cadence.get("kind")
    if kind == "once":
        d = _dt.date.fromisoformat(cadence["date"])
        return d.isoformat() if d > floor else ""
    probe = floor + _dt.timedelta(days=1)
    while probe <= horizon:
        occ = occurrences_up_to(cadence, floor, probe)
        if occ:
            return occ[0].isoformat()
        probe += _dt.timedelta(days=27)
    return ""


# ---------------------------------------------------------------------------
# CRUD (provenance-stamped, refuse-before-write)
# ---------------------------------------------------------------------------

def list_obligations() -> list:
    return state_store.load_list(_STORE)


def add_obligation(data: dict, *, written_by: str, source_op: str = "",
                   today: Optional[_dt.date] = None) -> dict:
    today = today or _dt.date.today()
    name = str(data.get("name") or "").strip()
    task = str(data.get("task") or "").strip()
    if not name:
        raise ObligationError("an obligation needs a name.")
    if not task:
        raise ObligationError("an obligation needs the task it starts.")
    cadence = _validate_cadence(data.get("cadence") or {})
    entry = {
        "id": "obl_" + uuid.uuid4().hex[:12],
        "name": name,
        "task": task,
        "cadence": cadence,
        "created_iso": _dt.datetime.now().isoformat(timespec="seconds"),
        "last_completed_iso": "",
        # Caught-up at creation: recurring cadences never fire retroactively
        # for periods before the obligation existed. "once" fires even for a
        # past date — a dated obligation you enter late is still owed.
        "last_completed_period": "",
        "dismissed_period": "",
    }
    if cadence["kind"] != "once":
        prior = occurrences_up_to(cadence, None, today)
        if prior:
            entry["last_completed_period"] = prior[-1].isoformat()
    _stamp(entry, written_by, source_op)
    items = list_obligations()
    items.insert(0, entry)
    state_store.save(_STORE, items)
    log.info("obligation.added id=%s cadence=%s", entry["id"], cadence["kind"])
    return entry


def update_obligation(obligation_id: str, data: dict) -> Optional[dict]:
    items = list_obligations()
    for i, ob in enumerate(items):
        if ob.get("id") == obligation_id:
            if "name" in data:
                ob["name"] = str(data["name"] or "").strip() or ob["name"]
            if "task" in data:
                ob["task"] = str(data["task"] or "").strip() or ob["task"]
            if "cadence" in data:
                ob["cadence"] = _validate_cadence(data["cadence"])
            items[i] = ob
            state_store.save(_STORE, items)
            return ob
    return None


def delete_obligation(obligation_id: str) -> bool:
    items = list_obligations()
    kept = [ob for ob in items if ob.get("id") != obligation_id]
    if len(kept) == len(items):
        return False
    state_store.save(_STORE, kept)
    return True


def mark_complete(obligation_id: str, today: Optional[_dt.date] = None) -> dict:
    """Record the CURRENTLY DUE occurrence as completed. The server
    recomputes which occurrence that is — the client is never trusted with
    the period key. Refuses when nothing is due (same period can never be
    completed twice)."""
    today = today or _dt.date.today()
    items = list_obligations()
    for i, ob in enumerate(items):
        if ob.get("id") != obligation_id:
            continue
        due = due_status(ob, today)
        if due is None:
            raise ObligationError(
                f"{ob.get('name')!r} has nothing due — already completed for "
                "this period.")
        ob["last_completed_iso"] = _dt.datetime.now().isoformat(timespec="seconds")
        ob["last_completed_period"] = due["due_date"]
        ob["dismissed_period"] = ""
        items[i] = ob
        state_store.save(_STORE, items)
        log.info("obligation.completed id=%s period=%s", obligation_id,
                 due["due_date"])
        return ob
    raise ObligationError(f"no obligation {obligation_id!r}.")


def dismiss_occurrence(obligation_id: str, today: Optional[_dt.date] = None) -> dict:
    """Dismiss the CURRENT occurrence only — the next one surfaces again."""
    today = today or _dt.date.today()
    items = list_obligations()
    for i, ob in enumerate(items):
        if ob.get("id") != obligation_id:
            continue
        due = due_status(ob, today)
        if due is None:
            raise ObligationError(f"{ob.get('name')!r} has nothing due to dismiss.")
        ob["dismissed_period"] = due["due_date"]
        items[i] = ob
        state_store.save(_STORE, items)
        return ob
    raise ObligationError(f"no obligation {obligation_id!r}.")


def due_obligations(today: Optional[_dt.date] = None) -> list:
    """Every obligation with something due, oldest-due first — the morning
    brief section and the in-app banner. READ-ONLY."""
    today = today or _dt.date.today()
    out = []
    for ob in list_obligations():
        due = due_status(ob, today)
        if due:
            out.append({"id": ob["id"], "name": ob["name"], "task": ob["task"],
                        "cadence": ob["cadence"], **due})
    out.sort(key=lambda r: r["due_date"])
    return out
