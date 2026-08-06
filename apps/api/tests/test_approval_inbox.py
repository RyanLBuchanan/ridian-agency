"""Approval inbox (v6.0 Phase 3) — staged approvals survive the thread.

Pins:
  1. a gated tool that parks *_pending persists the staged call (owner run,
     tool, exact kwargs, preview question, staged time) — and it is still
     there after the thread's context is gone;
  2. approving FROM THE INBOX executes exactly the staged action, through
     the same _apply_*_answer writers and the same gate signature check;
  3. a tampered stored payload REFUSES at the signature even when the
     provenance list is forged too — nothing executes;
  4. declining from the inbox executes nothing and stays declined;
  5. staleness (7+ days) is flagged, never auto-expired;
  6. buttons only — arbitrary text is rejected;
  7. an in-thread answer clears the inbox entry (sync);
  8. nested tool calls stage ONE entry (the outermost).
"""
import asyncio
import datetime as dt
import json

import pytest

from app.services import (approval_inbox_service, memory_service,
                          operator_service, pipeline_service, state_store)
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

_CUSTOMERS = [{"id": "42", "name": "Sandy Alvarez", "email": "sandy@gulf.test"}]


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


@pytest.fixture()
def qb(monkeypatch):
    created = []
    monkeypatch.setattr(t.quickbooks_service, "list_customers", lambda: list(_CUSTOMERS))
    monkeypatch.setattr(t.quickbooks_service, "list_items", lambda: [])

    def fake_create(customer_id, lines, txn_date="", due_date=""):
        created.append({"customer_id": customer_id, "lines": lines})
        return {"id": "99", "doc_number": "1042", "customer": "Sandy Alvarez",
                "total": 4500.0, "email_status": "NotSet", "link": ""}

    monkeypatch.setattr(t.quickbooks_service, "create_invoice", fake_create)
    return created


def _op(tmp_path, op_id="op_thread", stated=None):
    async def _emit(_ev):
        return None
    record = {"id": op_id, "command": "Invoice Sandy for the discovery engagement",
              "steps": [], "tools_used": [], "artifacts": [], "errors": [],
              "user_stated_numbers": list(stated or [])}
    op = OperatorContext(folder=tmp_path / "run", record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


_LINES = [{"description": "Discovery engagement", "amount": 4500}]


def _stage_invoice(tmp_path, stated=(4500,)):
    """Stage an invoice approval in a 'thread', then drop that thread's
    context entirely — the inbox is all that remains."""
    op = _op(tmp_path, stated=list(stated))
    out = _call("create_quickbooks_invoice", customer="Sandy Alvarez",
                lines=[dict(l) for l in _LINES])
    assert out.get("reason") == "invoice_plan_pending"
    set_current_operator(None)                       # the thread is GONE
    return op


# --------------------------------------------------------------------------
# Staging + survival
# --------------------------------------------------------------------------

def test_staged_approval_survives_the_thread(tmp_path, qb):
    _stage_invoice(tmp_path)
    pending = approval_inbox_service.list_pending()
    assert len(pending) == 1
    a = pending[0]
    assert a["operation_id"] == "op_thread"          # which run owns it
    assert a["command"] == "Invoice Sandy for the discovery engagement"
    assert a["tool"] == "create_quickbooks_invoice"
    assert a["kwargs"]["lines"] == _LINES            # what it will do, exactly
    assert "Invoice preview" in a["question"]
    assert a["staged_at"] and a["status"] == "pending"
    assert {o["value"] for o in a["options"]} == {t.INVOICE_PROCEED, t.INVOICE_CANCEL}
    assert a["gate_flags"]["invoice_preview_sig"]    # the signed payload rode along


def test_reask_replaces_the_staged_entry_not_duplicates(tmp_path, qb):
    op = _op(tmp_path, stated=[4500, 900])
    _call("create_quickbooks_invoice", customer="Sandy Alvarez",
          lines=[dict(l) for l in _LINES])
    _call("create_quickbooks_invoice", customer="Sandy Alvarez",
          lines=[{"description": "Retainer", "amount": 900}])
    pending = approval_inbox_service.list_pending()
    assert len(pending) == 1                         # upsert, not a pile-up
    assert pending[0]["kwargs"]["lines"][0]["amount"] == 900


def test_nested_tool_calls_stage_one_entry_the_outermost(tmp_path, qb):
    op = _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez", email="sandy@gulf.test")
    _call("add_deal", contact="Sandy Alvarez", title="AI discovery engagement",
          stage="proposal", value_usd="4500")
    out = _call("invoice_deal", deal="Sandy")
    assert out.get("reason") == "invoice_plan_pending"
    pending = approval_inbox_service.list_pending()
    assert [a["tool"] for a in pending] == ["invoice_deal"]


# --------------------------------------------------------------------------
# Answering from the inbox — the same signed path
# --------------------------------------------------------------------------

def test_approve_from_inbox_executes_exactly_the_staged_action(tmp_path, qb):
    _stage_invoice(tmp_path)
    appr = approval_inbox_service.list_pending()[0]
    out = asyncio.run(approval_inbox_service.answer_approval(
        appr["id"], t.INVOICE_PROCEED))
    assert out.get("approved") is True
    assert out["result"]["doc_number"] == "1042"
    assert len(qb) == 1
    assert qb[0]["customer_id"] == "42"
    assert qb[0]["lines"][0]["amount"] == 4500.0     # the STAGED line, exactly
    stored = next(a for a in state_store.load_list("approvals")
                  if a["id"] == appr["id"])
    assert stored["status"] == "approved" and stored["answered_at"]
    assert approval_inbox_service.list_pending() == []


def test_tampered_payload_refuses_at_the_signature(tmp_path, qb):
    """Forge BOTH the kwargs amount and the provenance list in the store —
    the gate's signature (staged before the tamper) still refuses."""
    _stage_invoice(tmp_path)
    items = state_store.load_list("approvals")
    items[0]["kwargs"]["lines"][0]["amount"] = 9999
    items[0]["user_stated_numbers"] = [9999]         # forged provenance too
    state_store.save("approvals", items)
    appr_id = items[0]["id"]

    out = asyncio.run(approval_inbox_service.answer_approval(
        appr_id, t.INVOICE_PROCEED))
    assert out.get("reason") == "signature_mismatch"
    assert "Nothing was executed" in out["error"]
    assert qb == []                                  # nothing created


def test_decline_from_inbox_executes_nothing(tmp_path, qb):
    _stage_invoice(tmp_path)
    appr = approval_inbox_service.list_pending()[0]
    out = asyncio.run(approval_inbox_service.answer_approval(
        appr["id"], t.INVOICE_CANCEL))
    assert out.get("declined") is True
    assert qb == []
    assert approval_inbox_service.list_pending() == []
    # A second answer refuses — it was already resolved.
    again = asyncio.run(approval_inbox_service.answer_approval(
        appr["id"], t.INVOICE_PROCEED))
    assert "already resolved" in again["error"]
    assert qb == []


def test_buttons_only_arbitrary_text_rejected(tmp_path, qb):
    _stage_invoice(tmp_path)
    appr = approval_inbox_service.list_pending()[0]
    out = asyncio.run(approval_inbox_service.answer_approval(
        appr["id"], "yes go ahead"))
    assert "Buttons only" in out["error"]
    assert qb == []
    assert len(approval_inbox_service.list_pending()) == 1   # still pending


# --------------------------------------------------------------------------
# Staleness + in-thread sync
# --------------------------------------------------------------------------

def test_stale_is_flagged_not_expired(tmp_path, qb):
    _stage_invoice(tmp_path)
    items = state_store.load_list("approvals")
    items[0]["staged_at"] = "2026-07-28T09:00:00"
    state_store.save("approvals", items)
    now = dt.datetime(2026, 8, 6, 9, 0, 0)
    pending = approval_inbox_service.list_pending(now=now)
    assert len(pending) == 1                         # NOT expired
    assert pending[0]["stale"] is True
    fresh = approval_inbox_service.list_pending(now=dt.datetime(2026, 7, 30))
    assert fresh[0]["stale"] is False


def test_in_thread_answer_clears_the_inbox_entry(tmp_path, qb):
    op = _op(tmp_path, stated=[4500])
    _call("create_quickbooks_invoice", customer="Sandy Alvarez",
          lines=[dict(l) for l in _LINES])
    assert len(approval_inbox_service.list_pending()) == 1
    operator_service._apply_invoice_answer(op, t.INVOICE_PROCEED)
    approval_inbox_service.sync_from_record(op.record)
    assert approval_inbox_service.list_pending() == []
    stored = state_store.load_list("approvals")[0]
    assert stored["status"] == "answered"
    assert stored["outcome"] == "approved in thread"


def test_approving_restore_from_inbox_restores_state(tmp_path):
    """Cross-gate generality: a staged backup RESTORE approved from the
    inbox actually restores — through the same restore gate."""
    op = _op(tmp_path, op_id="op_restore")
    _call("add_contact", name="Sandy Alvarez")
    snap_id = _call("backup_now")["backup"]["id"]
    _call("add_contact", name="Casey Reed")
    out = _call("restore_backup", timestamp=snap_id)
    assert out.get("reason") == "restore_pending"
    set_current_operator(None)                       # thread gone

    appr = next(a for a in approval_inbox_service.list_pending()
                if a["tool"] == "restore_backup")
    result = asyncio.run(approval_inbox_service.answer_approval(
        appr["id"], t.RESTORE_PROCEED))
    assert result.get("approved") is True
    names = [c["name"] for c in memory_service.list_contacts()]
    assert names == ["Sandy Alvarez"]                # Casey rolled back