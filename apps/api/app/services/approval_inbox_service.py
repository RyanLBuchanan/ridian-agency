"""Approval inbox (v6.0 Phase 3) — staged approvals survive the thread.

Every gate that stages an approval returns {"reason": "*_pending"} from its
tool; the planner_tool wrapper (single choke point) persists that staged
call here: which run owns it, what it will do (tool + exact kwargs + the
preview question), when it was staged, and a snapshot of the gate's
signature flags. Closing the thread — or restarting the app — no longer
loses the item.

Answering from the inbox uses the SAME signed path as answering in-thread:
the operation's gate flags are rebuilt onto a fresh record, the operator's
button value runs through the SAME _apply_*_answer functions (the only
writers of the approved/declined flags), and the staged tool is re-called
with the staged kwargs — so the gate itself re-verifies the payload
signature. A tampered payload fails that signature check and is REFUSED;
nothing executes.

Stale approvals (7+ days) are FLAGGED in the listing, never auto-expired.

Import discipline: this module imports only state_store + operator_context
at module level; operator_service / operator_tools are imported lazily
inside functions (they import this module's callers).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from . import state_store
from .operator_context import OperatorContext, set_current_operator

log = logging.getLogger("ridian.approvals")

_STORE = "approvals"
STALE_AFTER_DAYS = 7

# reason prefix -> (asked, approved, declined) record flags, for syncing
# in-thread answers back onto stored entries. The appliers in
# operator_service remain the only writers of these flags.
_GATE_FLAGS = {
    "invoice_plan_pending": ("invoice_plan_asked", "invoice_approved",
                             "invoice_declined"),
    "proposal_plan_pending": ("proposal_doc_asked", "proposal_doc_approved",
                              "proposal_doc_declined"),
    "contact_admin_pending": ("contact_admin_asked", "contact_admin_approved",
                              "contact_admin_declined"),
    "restore_pending": ("restore_asked", "restore_approved",
                        "restore_declined"),
    "research_plan_pending": ("research_plan_asked", "research_approved",
                              "research_declined"),
}


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _load() -> list[dict]:
    return state_store.load_list(_STORE)


def stage_from_tool(tool_name: str, kwargs: dict, result: dict) -> Optional[dict]:
    """Persist a staged-but-unanswered approval. Called by the planner_tool
    wrapper whenever an OUTERMOST tool call returns {"reason": "*_pending"}.
    Upserts on (operation_id, tool): a gate that re-asks with a changed
    payload replaces its previous staged entry."""
    from .operator_context import current_operator
    operator = current_operator()
    record = operator.record
    needs = record.get("needs_input") or []
    last = needs[-1] if needs else {}
    entry = {
        "id": "appr_" + uuid.uuid4().hex[:12],
        "operation_id": record.get("id", ""),
        "command": record.get("command", ""),
        "folder": str(operator.folder),
        "tool": tool_name,
        "kwargs": dict(kwargs),
        "reason": str(result.get("reason", "")),
        "question": str(last.get("question", "")),
        "options": list(last.get("options") or []),
        # The gate's signature/asked flags, exactly as staged — restoring
        # these is what lets the gate re-verify the payload later.
        "gate_flags": {k: v for k, v in record.items()
                       if k.endswith("_sig") or k.endswith("_asked")},
        "user_stated_numbers": list(record.get("user_stated_numbers") or []),
        "user_provided_emails": list(record.get("user_provided_emails") or []),
        "staged_at": _now_iso(),
        "status": "pending",
        "answered_at": "",
        "outcome": "",
    }
    items = _load()
    items = [a for a in items
             if not (a.get("status") == "pending"
                     and a.get("operation_id") == entry["operation_id"]
                     and a.get("tool") == tool_name)]
    items.insert(0, entry)
    state_store.save(_STORE, items)
    log.info("approvals.staged id=%s tool=%s op=%s", entry["id"], tool_name,
             entry["operation_id"])
    return entry


def list_pending(now: Optional[_dt.datetime] = None) -> list[dict]:
    """Pending approvals, newest first, each with a computed ``stale`` flag
    (staged 7+ days ago). ``now`` is injectable for deterministic tests."""
    now = now or _dt.datetime.now()
    cutoff = now - _dt.timedelta(days=STALE_AFTER_DAYS)
    out = []
    for a in _load():
        if a.get("status") != "pending":
            continue
        try:
            staged = _dt.datetime.fromisoformat(a.get("staged_at", ""))
            stale = staged <= cutoff
        except ValueError:
            stale = False
        out.append({**a, "stale": stale})
    return out


def _mark(approval_id: str, status: str, outcome: str) -> None:
    items = _load()
    for a in items:
        if a.get("id") == approval_id:
            a["status"] = status
            a["answered_at"] = _now_iso()
            a["outcome"] = outcome
    state_store.save(_STORE, items)


def _update_operation(operation_id: str, detail: str) -> None:
    """Reflect the inbox outcome on the persisted operation record so the
    run's history (and the morning brief) stops reporting it as waiting."""
    ops = state_store.load_list("operations")
    changed = False
    for op in ops:
        if op.get("id") == operation_id and op.get("status") == "awaiting_input":
            op["status"] = "completed"
            op.setdefault("steps", []).append({
                "name": "approval_inbox", "status": "completed",
                "started_at": _now_iso(), "completed_at": _now_iso(),
                "detail": detail})
            changed = True
    if changed:
        state_store.save("operations", ops)


def sync_from_record(record: dict) -> None:
    """After IN-THREAD appliers run, mark this operation's stored entries
    answered when their gate flags flipped — the inbox never shows an item
    the operator already answered in the thread."""
    items = _load()
    changed = False
    for a in items:
        if a.get("status") != "pending" or a.get("operation_id") != record.get("id"):
            continue
        flags = _GATE_FLAGS.get(a.get("reason", ""))
        if not flags:
            continue
        _asked, approved_key, declined_key = flags
        if record.get(approved_key) or record.get(declined_key):
            a["status"] = "answered"
            a["answered_at"] = _now_iso()
            a["outcome"] = ("approved in thread" if record.get(approved_key)
                            else "declined in thread")
            changed = True
    if changed:
        state_store.save(_STORE, items)


async def answer_approval(approval_id: str, value: str) -> dict:
    """Approve or cancel a staged item FROM THE INBOX. Buttons only: the
    value must be one of the staged options. Runs the operator's answer
    through the SAME _apply_*_answer writers and re-executes the staged
    tool call, so every gate (provenance, signature match) applies
    unchanged — a tampered payload fails its signature and is refused."""
    from . import operator_service as _osvc          # lazy: import cycle
    from .operator_tools import PLANNER_TOOLS        # lazy: import cycle

    appr = next((a for a in _load() if a.get("id") == approval_id), None)
    if appr is None:
        return {"error": f"No approval {approval_id!r}."}
    if appr.get("status") != "pending":
        return {"error": (f"Approval {approval_id} was already resolved "
                          f"({appr.get('status')}, {appr.get('answered_at')}).")}
    allowed = {str(o.get("value")) for o in (appr.get("options") or [])}
    if str(value) not in allowed:
        return {"error": ("Buttons only — the answer must be one of the staged "
                          "options."), "options": sorted(allowed)}

    # Rebuild the gate state exactly as staged, on a fresh record.
    folder = Path(appr.get("folder") or ".")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    async def _null_emit(_event: dict) -> None:
        return None

    record = {
        "id": appr.get("operation_id", ""),
        "command": appr.get("command", ""),
        "steps": [], "tools_used": [], "artifacts": [], "errors": [],
        "needs_input": [], "awaiting_input": False,
        "user_stated_numbers": list(appr.get("user_stated_numbers") or []),
        "user_provided_emails": list(appr.get("user_provided_emails") or []),
        **(appr.get("gate_flags") or {}),
    }
    operator = OperatorContext(folder=folder, record=record, emit=_null_emit)
    set_current_operator(operator)

    # The SAME answer writers as in-thread — nothing else sets these flags.
    notes = [n for n in (
        _osvc._apply_grounding_answer(operator, value),
        _osvc._apply_research_answer(operator, value),
        _osvc._apply_invoice_answer(operator, value),
        _osvc._apply_proposal_answer(operator, value),
        _osvc._apply_contact_admin_answer(operator, value),
        _osvc._apply_restore_answer(operator, value),
    ) if n]
    approved = any("APPROVED" in n for n in notes)
    declined = any("DECLINED" in n for n in notes)

    if declined:
        _mark(approval_id, "declined", "declined from inbox")
        _update_operation(appr["operation_id"],
                          f"Declined from the approval inbox: {appr['question']}")
        _osvc._drop_session(appr["operation_id"])
        return {"declined": True, "approval": appr["id"]}
    if not approved:
        return {"error": "No gate recognized that answer — nothing was done."}

    tool = next((t for t in PLANNER_TOOLS if t.name == appr.get("tool")), None)
    if tool is None:
        return {"error": f"Staged tool {appr.get('tool')!r} no longer exists."}
    raw = await tool.call(dict(appr.get("kwargs") or {}))
    result = json.loads(raw) if isinstance(raw, str) else raw

    if result.get("error"):
        if str(result.get("reason", "")).endswith("_pending"):
            # The gate did NOT accept the approval: the payload no longer
            # matches the staged signature (tampering, or state moved).
            # Nothing executed. The gate's fresh re-ask replaced the entry —
            # its preview now shows what would ACTUALLY run.
            return {"error": ("REFUSED: the gate did not accept the approval — "
                              "the staged payload no longer matches its "
                              "signature. Nothing was executed; review the "
                              "re-staged preview before approving again."),
                    "reason": "signature_mismatch"}
        return {"error": result["error"], "reason": result.get("reason", ""),
                "detail": "The staged action refused at execution; it remains pending."}

    _mark(approval_id, "approved",
          json.dumps(result, default=str)[:500])
    _update_operation(appr["operation_id"],
                      f"Approved from the approval inbox: {appr['question']}")
    _osvc._drop_session(appr["operation_id"])
    log.info("approvals.executed id=%s tool=%s", approval_id, appr.get("tool"))
    return {"approved": True, "approval": appr["id"], "result": result}
