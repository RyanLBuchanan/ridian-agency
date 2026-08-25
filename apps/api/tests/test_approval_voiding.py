"""Void-on-cancel (v6.9.3) — a dead run's approvals die with it.

THE INCIDENT this pins: on 2026-08-20 an invoice run for Greg Alexander
was cancelled, but its staged $500 approval stayed live in the inbox —
approvable from the phone — for five days, against a production
QuickBooks realm. Two deterministic layers now close that:

  1. dismiss_operation VOIDS every pending approval the run owns, in the
     same write breath as the cancellation;
  2. answer_approval refuses to EXECUTE any approval whose owning run is
     terminal or missing — and voids it right there, so refusal clears
     the inbox rather than leaving the trap armed. Refused, not hidden:
     the gate is in the answer path, not the listing.

Declining stays allowed on a dead owner — it is the cleanup action and
executes nothing.
"""
import asyncio
import json

import pytest

from app.services import approval_inbox_service, state_store
from app.services import operator_service
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

_CUSTOMERS = [{"id": "42", "name": "Sandy Alvarez", "email": "sandy@gulf.test"}]
_LINES = [{"description": "Discovery engagement", "amount": 4500}]


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


@pytest.fixture()
def qb(monkeypatch):
    created = []
    monkeypatch.setattr(t.quickbooks_service, "list_customers",
                        lambda: list(_CUSTOMERS))
    monkeypatch.setattr(t.quickbooks_service, "list_items", lambda: [])

    def fake_create(customer_id, lines, txn_date="", due_date=""):
        created.append({"customer_id": customer_id, "lines": lines})
        return {"id": "99", "doc_number": "1042", "customer": "Sandy Alvarez",
                "total": 4500.0, "email_status": "NotSet", "link": ""}

    monkeypatch.setattr(t.quickbooks_service, "create_invoice", fake_create)
    return created


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _stage(tmp_path, op_id="op_live", owner_status="awaiting_input"):
    """Stage a real invoice approval through the real gate, with a persisted
    owner run in ``owner_status`` — exactly what staging leaves behind."""
    async def _emit(_ev):
        return None
    record = {"id": op_id, "command": "Invoice Sandy for the discovery engagement",
              "steps": [], "tools_used": [], "artifacts": [], "errors": [],
              "user_stated_numbers": [4500]}
    op = OperatorContext(folder=tmp_path / "run", record=record, emit=_emit)
    set_current_operator(op)
    out = _call("create_quickbooks_invoice", customer="Sandy Alvarez",
                lines=[dict(l) for l in _LINES])
    assert out.get("reason") == "invoice_plan_pending"
    ops = state_store.load_list("operations")
    ops.insert(0, {"id": op_id, "status": owner_status,
                   "awaiting_input": owner_status == "awaiting_input",
                   "command": record["command"], "steps": [],
                   "needs_input": list(record.get("needs_input") or [])})
    state_store.save("operations", ops)
    set_current_operator(None)
    return approval_inbox_service.list_pending()[0]


def _answer(appr_id, value):
    return asyncio.run(approval_inbox_service.answer_approval(appr_id, value))


# --------------------------------------------------------------------------
# Layer 1: cancel voids, in the same breath
# --------------------------------------------------------------------------

def test_cancelling_a_run_voids_its_staged_approvals(tmp_path, qb):
    appr = _stage(tmp_path, op_id="op_live")
    assert approval_inbox_service.list_pending()          # armed
    out = operator_service.dismiss_operation("op_live")
    assert out["cancelled"] is True
    # The approval died WITH the run — inbox empty, record voided honestly.
    assert approval_inbox_service.list_pending() == []
    stored = next(a for a in state_store.load_list("approvals")
                  if a["id"] == appr["id"])
    assert stored["status"] == "declined"
    assert "voided" in stored["outcome"]
    assert "declined" in stored["outcome"]                # audit classifies by this
    assert stored["answered_at"]
    # And a later approve attempt refuses on the resolved status.
    again = _answer(appr["id"], t.INVOICE_PROCEED)
    assert "already resolved" in again["error"]
    assert qb == []                                       # nothing ever executed


def test_cancel_voids_only_the_cancelled_runs_approvals(tmp_path, qb):
    mine = _stage(tmp_path, op_id="op_doomed")
    other = _stage(tmp_path, op_id="op_healthy")
    operator_service.dismiss_operation("op_doomed")
    pending = approval_inbox_service.list_pending()
    assert [a["id"] for a in pending] == [other["id"]]
    assert next(a for a in state_store.load_list("approvals")
                if a["id"] == mine["id"])["status"] == "declined"


# --------------------------------------------------------------------------
# Layer 2: the answer path itself refuses on a dead owner — THE incident
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dead_status", ["cancelled", "failed", "completed",
                                         "partial"])
def test_approve_refuses_and_voids_when_owner_is_terminal(tmp_path, qb,
                                                          dead_status):
    """The 2026-08-20 shape, for every terminal status: approval pending,
    owner dead. Approving must execute NOTHING and clear the trap."""
    appr = _stage(tmp_path, op_id="op_dead", owner_status=dead_status)
    out = _answer(appr["id"], t.INVOICE_PROCEED)
    assert out.get("reason") == "owner_terminal"
    assert dead_status in out["error"] and "voided" in out["error"]
    assert qb == []                                       # NOTHING created
    assert approval_inbox_service.list_pending() == []    # trap disarmed
    stored = next(a for a in state_store.load_list("approvals")
                  if a["id"] == appr["id"])
    assert stored["status"] == "declined"
    assert "voided at answer time" in stored["outcome"]


def test_approve_refuses_when_owner_record_is_missing(tmp_path, qb):
    appr = _stage(tmp_path, op_id="op_gone")
    state_store.save("operations",
                     [o for o in state_store.load_list("operations")
                      if o.get("id") != "op_gone"])
    out = _answer(appr["id"], t.INVOICE_PROCEED)
    assert out.get("reason") == "owner_terminal"
    assert "missing" in out["error"]
    assert qb == []


def test_approve_still_works_when_owner_is_parked(tmp_path, qb):
    """The gate must not break the LEGITIMATE path: a live parked run's
    approval executes exactly as before."""
    appr = _stage(tmp_path, op_id="op_live", owner_status="awaiting_input")
    out = _answer(appr["id"], t.INVOICE_PROCEED)
    assert out.get("approved") is True
    assert len(qb) == 1 and qb[0]["customer_id"] == "42"


def test_decline_is_still_allowed_on_a_dead_owner(tmp_path, qb):
    """Declining executes nothing — it stays available as the manual
    cleanup even when the owner is terminal."""
    appr = _stage(tmp_path, op_id="op_dead", owner_status="cancelled")
    out = _answer(appr["id"], t.INVOICE_CANCEL)
    assert out.get("declined") is True
    assert qb == []
    assert approval_inbox_service.list_pending() == []
