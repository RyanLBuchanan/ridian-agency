"""Audit log (v6.0 Phase 8) — every gated action, made visible.

READ-ONLY by construction: this module reads the two ledgers the app
already keeps and writes nothing.

  - the APPROVALS store (v6.0 Phase 3) is authoritative for outcomes. It
    records what was staged, when, which run owned it, and whether it was
    approved or declined — in the thread or from the inbox;
  - the OPERATIONS log supplies each run's command, cost, artifacts, and
    any gate ask (a buttons-only needs_input) that has no approvals entry
    — runs that predate the approvals ledger.

Where an outcome is genuinely recorded, ``outcome_source`` is "recorded".
Where it is derived from surrounding evidence it is "inferred", and where
the evidence does not exist the outcome is "unknown" — never a guess
dressed as a fact. The view shows that distinction.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import logging
from typing import Optional

from . import state_store

log = logging.getLogger("ridian.audit")

# reason / tool → the type shown in the view and used by the type filter.
_TYPE_BY_REASON = {
    "invoice_plan_pending": "invoice",
    "proposal_plan_pending": "proposal",
    "contact_admin_pending": "contact_admin",
    "restore_pending": "backup_restore",
    "research_plan_pending": "research",
}
_TYPE_BY_TOOL = {
    "create_quickbooks_invoice": "invoice",
    "invoice_deal": "invoice",
    "draft_proposal": "proposal",
    "merge_contacts": "contact_admin",
    "delete_contact": "contact_admin",
    "restore_backup": "backup_restore",
}
# Ordered, most specific first: a hint mentioning both "invoice" and
# "approval" must classify as invoice, not as a bare approval.
_TYPE_BY_HINT = (
    ("quickbooks invoice", "invoice"),
    ("invoice", "invoice"),
    ("proposal", "proposal"),
    ("contact merge", "contact_admin"),
    ("contact delete", "contact_admin"),
    ("backup restore", "backup_restore"),
    ("research plan", "research"),
)

OUTCOMES = ("approved", "declined", "pending", "unknown")
TYPES = ("invoice", "proposal", "contact_admin", "backup_restore", "research",
         "gated_action")

_CSV_FIELDS = ("staged_at", "answered_at", "type", "outcome", "outcome_source",
               "action", "question", "command", "operation_id", "cost_usd",
               "created", "approval_id")


def _classify(*, reason: str = "", tool: str = "", hint: str = "",
              question: str = "") -> str:
    if reason in _TYPE_BY_REASON:
        return _TYPE_BY_REASON[reason]
    if tool in _TYPE_BY_TOOL:
        return _TYPE_BY_TOOL[tool]
    blob = f"{hint} {question}".lower()
    for needle, kind in _TYPE_BY_HINT:
        if needle in blob:
            return kind
    return "gated_action"


def _outcome_from_status(status: str) -> str:
    """approvals-store status → audit outcome. 'answered' is Phase 3's
    in-thread resolution; its direction lives in ``outcome``."""
    return {"approved": "approved", "declined": "declined",
            "pending": "pending"}.get(str(status or ""), "unknown")


def _index_operations() -> dict:
    out = {}
    for op in state_store.load_list("operations"):
        if op.get("id"):
            out[op["id"]] = op
    return out


def _created_by(op: dict) -> str:
    names = [a.get("name", "") for a in (op.get("artifacts") or []) if a.get("name")]
    return "; ".join(n for n in names if n != "operation_log.json")


def _entry(**kw) -> dict:
    base = {"id": "", "approval_id": "", "operation_id": "", "command": "",
            "type": "gated_action", "action": "", "question": "",
            "staged_at": "", "answered_at": "", "outcome": "unknown",
            "outcome_source": "inferred", "cost_usd": 0.0, "created": ""}
    base.update(kw)
    return base


def _from_approvals(ops: dict) -> list[dict]:
    entries = []
    for a in state_store.load_list("approvals"):
        op = ops.get(a.get("operation_id", ""), {})
        status = str(a.get("status") or "")
        outcome_text = str(a.get("outcome") or "")
        if status == "answered":
            # Resolved in the thread; the direction is recorded in outcome.
            outcome = ("declined" if "declined" in outcome_text.lower()
                       else "approved" if "approved" in outcome_text.lower()
                       else "unknown")
        else:
            outcome = _outcome_from_status(status)
        entries.append(_entry(
            id=f"appr:{a.get('id', '')}",
            approval_id=a.get("id", ""),
            operation_id=a.get("operation_id", ""),
            command=a.get("command", "") or op.get("command", ""),
            type=_classify(reason=a.get("reason", ""), tool=a.get("tool", ""),
                           question=a.get("question", "")),
            action=a.get("tool", ""),
            question=a.get("question", ""),
            staged_at=a.get("staged_at", ""),
            answered_at=a.get("answered_at", ""),
            outcome=outcome,
            # The approvals ledger WROTE these outcomes; nothing is inferred.
            outcome_source="recorded",
            cost_usd=round(float(op.get("spend_usd", 0.0) or 0.0), 4),
            created=_created_by(op) if outcome == "approved" else "",
        ))
    return entries


def _from_operations(ops: dict, seen_ops: set) -> list[dict]:
    """Gate asks recorded in operation logs that the approvals ledger does
    not cover — runs from before Phase 3. Outcomes here are inferred from
    what the run shows, or honestly unknown."""
    entries = []
    for op_id, op in ops.items():
        for need in (op.get("needs_input") or []):
            if not need.get("buttons_only"):
                continue          # only signature-matched gates are audited
            if op_id in seen_ops:
                continue          # the approvals ledger already has this run
            kind = _classify(hint=need.get("context_hint", ""),
                             question=need.get("question", ""))
            if kind == "research":
                approved = bool(op.get("research_approved"))
                declined = bool(op.get("research_declined"))
                outcome = ("approved" if approved else
                           "declined" if declined else "unknown")
                source = "recorded" if (approved or declined) else "inferred"
            elif op.get("awaiting_input"):
                outcome, source = "pending", "recorded"
            else:
                outcome, source = "unknown", "inferred"
            entries.append(_entry(
                id=f"op:{op_id}:{need.get('id', '')}",
                operation_id=op_id,
                command=op.get("command", ""),
                type=kind,
                action=need.get("context_hint", ""),
                question=need.get("question", ""),
                staged_at=need.get("asked_at", "") or op.get("started_at", ""),
                answered_at="" if outcome in ("pending", "unknown")
                            else op.get("completed_at", ""),
                outcome=outcome,
                outcome_source=source,
                cost_usd=round(float(op.get("spend_usd", 0.0) or 0.0), 4),
                created=_created_by(op) if outcome == "approved" else "",
            ))
    return entries


def _in_range(value: str, date_from: str, date_to: str) -> bool:
    day = str(value or "")[:10]
    if date_from and (not day or day < date_from):
        return False
    if date_to and (not day or day > date_to):
        return False
    return True


def build_audit(*, date_from: str = "", date_to: str = "",
                type_filter: str = "", outcome: str = "",
                limit: Optional[int] = None) -> dict:
    """Every gated action, newest first. Filters are AND-ed; blank means
    no filter. READ-ONLY."""
    ops = _index_operations()
    entries = _from_approvals(ops)
    seen = {e["operation_id"] for e in entries if e["operation_id"]}
    entries += _from_operations(ops, seen)

    df, dt_ = str(date_from or "")[:10], str(date_to or "")[:10]
    kind = str(type_filter or "").strip().lower()
    want = str(outcome or "").strip().lower()
    filtered = [e for e in entries
                if _in_range(e["staged_at"], df, dt_)
                and (not kind or e["type"] == kind)
                and (not want or e["outcome"] == want)]
    filtered.sort(key=lambda e: (e.get("staged_at") or ""), reverse=True)
    if limit:
        filtered = filtered[:int(limit)]

    counts: dict = {}
    for e in filtered:
        counts[e["outcome"]] = counts.get(e["outcome"], 0) + 1
    return {"entries": filtered, "count": len(filtered),
            "total_cost_usd": round(sum(e["cost_usd"] for e in filtered), 4),
            "by_outcome": counts,
            "filters": {"date_from": df, "date_to": dt_,
                        "type": kind, "outcome": want},
            "types": list(TYPES), "outcomes": list(OUTCOMES)}


def to_csv(entries: list[dict]) -> str:
    """CSV text of the given entries — the same fields the view shows."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_CSV_FIELDS),
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for e in entries:
        writer.writerow({k: e.get(k, "") for k in _CSV_FIELDS})
    return buf.getvalue()
