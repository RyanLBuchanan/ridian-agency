"""Ambient watch (v6.9.8) — Ridian notices; it never acts.

Four rules over data the app already holds (one new READ-ONLY QuickBooks
query was added for the first — list_invoices never fetched DueDate and
paged newest-first, hiding exactly the invoices a past-due watch is for):

  invoice_overdue    an open invoice past DueDate + grace days
  deal_quiet         an active deal with no recorded activity in N days
                     (activity = logged touch OR any edit OR creation —
                     a deal being worked through edits is not "quiet")
  inbound_match      an inbound email whose SENDER (contact.email ==
                     last_from — a CC'd contact does not count) is a
                     known contact with an active deal
  obligation_missed  an occurrence that came and went uncompleted: a
                     "once" obligation now overdue, or a cadence with
                     missed_periods >= 1

SURFACES, NEVER ACTS — pinned two ways by test: an AST allowlist over
this module's imports AND over the attributes it calls on them (an
imported module's write functions are unreachable if never named), plus
a no-writes pin (state files byte-identical AND the snapshot list
unchanged across an evaluation). This module deliberately does not
import state_store at all; token-file refreshes performed by the
underlying READ clients (Google/QBO) are their own behavior, out of
scope here.

PUSH TIER (deny by default, per the push charter): only invoice_overdue
(production books only — a sandbox test company's fake receivables never
interrupt anyone) and deal_quiet push; both keys are occurrence-stable.
inbound_match does NOT push — the phone's native Gmail notification
already covers the same message, and re-pushing it minutes later is how
an operator learns to ignore the channel. obligation_missed does NOT
push — the existing ob:<id>:<due_date> key already pushes every rollover
with "N earlier missed" in the body; a second push would be a duplicate.
Everything surfaces in the brief and the Due tab regardless.

The Due tab is served ONLY from this module's in-memory cache (the last
completed evaluation, stamped computed_at) — a phone rendering a local
list must never block on QuickBooks + Gmail.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from typing import Optional

from . import (inbox_service, obligations_service, pipeline_service,
               quickbooks_service, settings_service)

log = logging.getLogger("ridian.watch")

DEFAULT_DEAL_QUIET_DAYS = 14     # Ryan's stated number; the brief's 7d
                                 # "gone quiet" row stays the informational tier
DEFAULT_INVOICE_GRACE_DAYS = 3   # QBO fills DueDate=TxnDate when no payment
                                 # terms exist, and payments post late — day-0
                                 # pushes would be noise wearing a straight face

_cache_lock = threading.Lock()
_cache: dict = {"findings": [], "computed_at": "", "unavailable": {}}


def thresholds() -> dict:
    return {
        "deal_quiet_days": settings_service.get_int_setting(
            "watch_deal_quiet_days", DEFAULT_DEAL_QUIET_DAYS, minimum=1),
        "invoice_grace_days": settings_service.get_int_setting(
            "watch_invoice_grace_days", DEFAULT_INVOICE_GRACE_DAYS, minimum=0),
    }


# ---------------------------------------------------------------------------
# Pure rule core — no I/O, computed over inputs the caller fetched
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


def findings_from(*, today: _dt.date, limits: dict,
                  deals: Optional[list] = None,
                  invoices: Optional[list] = None, invoice_env: str = "",
                  needs_reply: Optional[list] = None,
                  obligations_due: Optional[list] = None) -> list[dict]:
    """The four rules, pure. A ``None`` input means that source could not
    be read — its rules are simply absent (the caller carries the honest
    note). Each finding: {kind, key, push, title, detail, tab}."""
    out: list[dict] = []

    for inv in (invoices or []):
        due = _parse_date(inv.get("due_date"))
        if due is None or float(inv.get("balance") or 0) <= 0:
            continue
        if (today - due).days <= limits["invoice_grace_days"]:
            continue
        env = (invoice_env or "sandbox").lower()
        label = "" if env == "production" else f" [{env}]"
        out.append({
            "kind": "invoice_overdue",
            "key": f"watch:inv:{env}:{inv.get('id')}:{inv.get('due_date')}",
            # Sandbox books never interrupt a phone; they still surface.
            "push": env == "production",
            "title": f"Invoice #{inv.get('doc_number')} is past due",
            "detail": (f"{inv.get('customer')} — ${inv.get('balance')} was due "
                       f"{inv.get('due_date')}{label}"),
            "tab": "due"})

    quiet_before = today - _dt.timedelta(days=limits["deal_quiet_days"])
    for d in (deals or []):
        if d.get("stage") not in pipeline_service.ACTIVE_STAGES:
            continue
        # Activity = the latest of touch/creation/edit. All blank = no
        # basis to call it quiet — never fire on missing data.
        stamp = max(filter(None, [str(d.get("last_touch_iso") or ""),
                                  str(d.get("created_iso") or ""),
                                  str(d.get("updated_iso") or "")]), default="")
        stamp_date = _parse_date(stamp)
        if stamp_date is None or stamp_date > quiet_before:
            continue
        days = (today - stamp_date).days
        out.append({
            "kind": "deal_quiet",
            "key": f"watch:deal:{d.get('id')}:{stamp}",
            "push": True,
            "title": f"Deal gone quiet {days}d: {d.get('title')}",
            "detail": (f"{d.get('contact_name') or 'no contact'} — last "
                       f"activity {stamp[:10]}"),
            "tab": "due"})

    for row in (needs_reply or []):
        contact = row.get("contact") or {}
        if row.get("from_me") or not contact.get("in_pipeline"):
            continue
        if (contact.get("email") or "").lower() != (row.get("last_from") or "").lower():
            continue                      # the SENDER must match, not a CC
        out.append({
            "kind": "inbound_match",
            "key": f"watch:mail:{row.get('id')}:{row.get('last_message_at')}",
            "push": False,                # Gmail already notified the phone
            "title": f"{contact.get('name')} wrote about an active deal",
            "detail": str(row.get("subject") or ""),
            "tab": "due"})

    for ob in (obligations_due or []):
        missed = int(ob.get("missed_periods") or 0)
        once_overdue = ((ob.get("cadence") or {}).get("kind") == "once"
                        and ob.get("status") == "overdue")
        if not (missed >= 1 or once_overdue):
            continue
        what = (f"{missed} occurrence(s) came and went uncompleted"
                if missed else f"due {ob.get('due_date')}, never completed")
        out.append({
            "kind": "obligation_missed",
            "key": f"watch:obmiss:{ob.get('id')}:{ob.get('due_date')}",
            "push": False,                # ob:<id>:<due_date> already pushes
            "title": f"Missed: {ob.get('name')}",
            "detail": what,
            "tab": "due"})
    return out


# ---------------------------------------------------------------------------
# Gathering — each source isolated so one outage never blinds the rest
# ---------------------------------------------------------------------------

def gather_and_evaluate(today: Optional[_dt.date] = None) -> dict:
    """Fetch every source (per-source try/except), run the rules, refresh
    the cache. Called by the push evaluation (outside its ledger lock) —
    the brief instead passes its own already-fetched inputs via
    evaluate_with() so one render never fetches twice."""
    today = today or _dt.date.today()
    unavailable: dict[str, str] = {}

    deals: Optional[list] = None
    try:
        deals = pipeline_service.list_deals()
    except Exception as exc:  # noqa: BLE001
        unavailable["deals"] = f"pipeline unreadable ({type(exc).__name__})"

    invoices: Optional[list] = None
    invoice_env = ""
    try:
        invoices = quickbooks_service.list_unpaid_invoices()
        invoice_env = quickbooks_service.get_environment()
    except Exception as exc:  # noqa: BLE001
        unavailable["invoices"] = f"QuickBooks unreachable ({exc})"[:200]

    needs_reply: Optional[list] = None
    try:
        needs_reply = inbox_service.triage()["needs_reply"]
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        unavailable["mail"] = f"Gmail unreachable ({detail})"[:200]

    obligations_due: Optional[list] = None
    try:
        obligations_due = obligations_service.due_obligations(today=today)
    except Exception as exc:  # noqa: BLE001
        unavailable["obligations"] = f"obligations unreadable ({type(exc).__name__})"

    return evaluate_with(today=today, deals=deals, invoices=invoices,
                         invoice_env=invoice_env, needs_reply=needs_reply,
                         obligations_due=obligations_due,
                         unavailable=unavailable)


def evaluate_with(*, today: _dt.date, deals=None, invoices=None,
                  invoice_env: str = "", needs_reply=None,
                  obligations_due=None, unavailable: Optional[dict] = None
                  ) -> dict:
    """Run the rules over caller-fetched inputs and refresh the cache."""
    findings = findings_from(today=today, limits=thresholds(), deals=deals,
                             invoices=invoices, invoice_env=invoice_env,
                             needs_reply=needs_reply,
                             obligations_due=obligations_due)
    snapshot = {
        "findings": findings,
        "computed_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "unavailable": dict(unavailable or {}),
    }
    with _cache_lock:
        _cache.update(snapshot)
    log.info("watch.evaluated findings=%s unavailable=%s",
             len(findings), list(snapshot["unavailable"]))
    return snapshot


def cached() -> dict:
    """The last completed evaluation — what the Due tab renders. NEVER
    triggers a fetch: a phone rendering a local list must not block on
    QuickBooks + Gmail. Empty computed_at = not evaluated yet, said so."""
    with _cache_lock:
        return {"findings": list(_cache["findings"]),
                "computed_at": _cache["computed_at"],
                "unavailable": dict(_cache["unavailable"])}


def push_candidates(today: Optional[_dt.date] = None) -> list[tuple[str, dict]]:
    """(key, payload) pairs for the PUSH tier only. Called by push_service
    outside its ledger lock; honors the watch kill-switch so a noisy rule
    can be silenced without losing approval/park pushes."""
    if not settings_service.get_bool_setting("watch_push_enabled", default=True):
        return []
    snapshot = gather_and_evaluate(today)
    return [(f["key"], {"title": f["title"], "body": f["detail"],
                        "tab": f["tab"]})
            for f in snapshot["findings"] if f.get("push")]


def brief_section(snapshot: dict) -> dict:
    """The morning-brief section shape, honest about partial evaluation."""
    items = [{"kind": f["kind"], "title": f["title"], "detail": f["detail"]}
             for f in snapshot["findings"]]
    notes = "; ".join(snapshot["unavailable"].values())
    if items:
        return {"items": items, "empty": False, "unavailable": False,
                "note": (f"Not fully evaluated — {notes}" if notes else "")}
    if notes:
        return {"items": [], "empty": True, "unavailable": True,
                "note": f"Could not evaluate every rule — {notes}"}
    return {"items": [], "empty": True, "unavailable": False,
            "note": "Nothing needs your attention — Ridian looked."}
