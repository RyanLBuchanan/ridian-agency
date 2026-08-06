"""Morning brief (v6.0 Phase 2) — one assembled, READ-ONLY view.

Four sections, each ALWAYS present with an honest note when empty or
unavailable — an empty section states that nothing is there, and a
section whose source is unreachable (QuickBooks not connected) says so
instead of masquerading as zero:

  - today's calendar events (v6.0 Phase 4, read-only)
  - inbox threads needing a reply (v6.0 Phase 5, read-only)
  - next actions due today (incl. overdue) and due this week
  - active deals with no touch in 7+ days
  - unpaid QuickBooks invoices (balance > 0)
  - runs currently awaiting the operator's answer (staged approvals)

Everything here READS. No state store is written; the no-writes pin
byte-compares the store files before and after.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from . import (calendar_service, inbox_service, pipeline_service,
               quickbooks_service, state_store)

log = logging.getLogger("ridian.brief")


def _deal_row(d: dict, today: _dt.date) -> dict:
    nad = (d.get("next_action_date") or "").strip()
    overdue = False
    if nad:
        try:
            overdue = _dt.date.fromisoformat(nad) < today
        except ValueError:
            pass
    return {"id": d.get("id"), "contact": d.get("contact_name"),
            "title": d.get("title"), "stage": d.get("stage"),
            "value_usd": d.get("value_usd"),
            "next_action": d.get("next_action"),
            "next_action_date": nad,
            "last_touch": d.get("last_touch_iso") or "",
            "overdue": overdue}


def _section(items: list, empty_note: str, *, unavailable: str = "") -> dict:
    """Uniform section shape. ``unavailable`` (a reason string) means the
    SOURCE could not be read — honestly distinct from a true zero."""
    if unavailable:
        return {"items": [], "empty": True, "unavailable": True,
                "note": unavailable}
    return {"items": items, "empty": not items, "unavailable": False,
            "note": "" if items else empty_note}


def build_brief(today: Optional[_dt.date] = None) -> dict:
    """Assemble the brief. ``today`` is injectable for deterministic tests."""
    today = today or _dt.date.today()
    week_end = today + _dt.timedelta(days=7)

    due_today: list[dict] = []
    due_week: list[dict] = []
    for d in pipeline_service.list_deals():
        if d.get("stage") not in pipeline_service.ACTIVE_STAGES:
            continue
        nad = (d.get("next_action_date") or "").strip()
        if not nad:
            continue
        try:
            due = _dt.date.fromisoformat(nad)
        except ValueError:
            continue
        if due <= today:
            due_today.append(_deal_row(d, today))
        elif due <= week_end:
            due_week.append(_deal_row(d, today))
    due_today.sort(key=lambda r: r["next_action_date"])
    due_week.sort(key=lambda r: r["next_action_date"])

    stale = [_deal_row(d, today) for d in
             pipeline_service.deals_needing_followup(
                 days_stale=7, due_within_days=7, today=today)["stale"]]

    try:
        unpaid = [i for i in quickbooks_service.list_invoices(limit=50)
                  if float(i.get("balance") or 0) > 0]
        invoices = _section(unpaid, "No unpaid invoices.")
    except Exception as exc:  # noqa: BLE001 — the note carries the reason
        log.info("brief.quickbooks_unavailable %s", type(exc).__name__)
        invoices = _section(
            [], "", unavailable=(f"QuickBooks unreachable ({exc}) — unpaid "
                                 "invoices unknown, NOT zero."))

    pending = []
    for op in state_store.load_list("operations"):
        if op.get("status") != "awaiting_input":
            continue
        needs = op.get("needs_input") or []
        pending.append({
            "operation_id": op.get("id"),
            "command": op.get("command", ""),
            "question": (needs[-1].get("question", "") if needs else ""),
            "started_at": op.get("started_at", ""),
        })

    try:
        events = calendar_service.todays_events(today=today)
        calendar = _section(events, "Nothing on the calendar today.")
    except Exception as exc:  # noqa: BLE001 — the note carries the reason
        log.info("brief.calendar_unavailable %s", type(exc).__name__)
        detail = getattr(exc, "detail", None) or str(exc)
        calendar = _section(
            [], "", unavailable=(f"Calendar unreachable ({detail}) — today's "
                                 "events unknown, NOT none."))

    try:
        triaged = inbox_service.triage(
            now=_dt.datetime.combine(today, _dt.time(9, 0)))
        needs = triaged["needs_reply"]
        inbox = _section(needs, "No inbox threads are waiting on your reply.")
    except Exception as exc:  # noqa: BLE001 — the note carries the reason
        log.info("brief.inbox_unavailable %s", type(exc).__name__)
        detail = getattr(exc, "detail", None) or str(exc)
        inbox = _section(
            [], "", unavailable=(f"Gmail unreachable ({detail}) — threads "
                                 "needing a reply unknown, NOT zero."))

    return {
        "generated_for": today.isoformat(),
        "sections": {
            "today_events": calendar,
            "needs_reply": inbox,
            "due_today": _section(
                due_today, "Nothing due today — no next actions dated today "
                           "or overdue."),
            "due_this_week": _section(
                due_week, "Nothing coming due in the next 7 days."),
            "stale_deals": _section(
                stale, "No active deals have gone quiet — every deal has a "
                       "touch in the last 7 days."),
            "unpaid_invoices": invoices,
            "awaiting_approval": _section(
                pending, "Nothing is awaiting your approval."),
        },
    }
