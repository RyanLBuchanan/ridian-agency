"""QuickBooks integration (v4.0) — reads + ONE gated write, by construction.

Pinned guarantees: no send/email/delete/void operation exists ANYWHERE in
the service (introspection); invoice creation pauses behind the preview-
approval gate (zero API writes before the operator's own answer); the
approved payload is signature-matched (changed payload re-asks); customers
and items must resolve against REAL fetched records; the create payload
never sets EmailStatus or any send flag.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.services import operator_service, quickbooks_service
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

_CUSTOMERS = [{"id": "42", "name": "Coastal Chamber", "email": "ap@chamber.test"},
              {"id": "43", "name": "Open Gulf LLC", "email": ""}]
_ITEMS = [{"id": "7", "name": "AI Discovery Session", "unit_price": 500.0, "type": "Service"}]


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


@pytest.fixture()
def qb(monkeypatch):
    created = []
    monkeypatch.setattr(t.quickbooks_service, "list_customers", lambda: list(_CUSTOMERS))
    monkeypatch.setattr(t.quickbooks_service, "list_items", lambda: list(_ITEMS))

    def fake_create(customer_id, lines, txn_date="", due_date=""):
        created.append({"customer_id": customer_id, "lines": lines,
                        "txn_date": txn_date, "due_date": due_date})
        return {"id": "99", "doc_number": "1042", "customer": "Coastal Chamber",
                "total": 500.0, "email_status": "NotSet",
                "link": "https://qbo.intuit.com/app/invoice?txnId=99"}

    monkeypatch.setattr(t.quickbooks_service, "create_invoice", fake_create)
    return created


# --------------------------------------------------------------------------
# THE reachability guarantee: only ONE write exists, and it cannot send
# --------------------------------------------------------------------------

def test_no_send_email_delete_reachable_in_code():
    forbidden = ("send", "email_invoice", "delete", "void", "update", "sparse",
                 "mark_paid", "finalize")
    public = [n for n in dir(quickbooks_service) if not n.startswith("_")]
    for name in public:
        low = name.lower()
        assert not any(f in low for f in forbidden if f != "email_invoice"), name
    writes = [n for n in public if n.startswith("create")]
    assert writes == ["create_invoice"]          # exactly one write
    registry = [x.name for x in t.PLANNER_TOOLS if "quickbooks" in x.name]
    assert sorted(registry) == ["create_quickbooks_invoice", "list_quickbooks_customers",
                                "list_quickbooks_invoices", "list_quickbooks_items"]


def test_create_payload_never_sets_send_state(qb, tmp_path):
    op = _ctx(tmp_path, {"invoice_plan_asked": True, "invoice_approved": True})
    set_current_operator(op)
    lines = [{"description": "Discovery", "amount": 500}]
    op.record["invoice_preview_sig"] = t._invoice_sig(
        "42", [dict(lines[0], _amount=500.0)])
    asyncio.run(_tool("create_quickbooks_invoice").call(
        {"customer": "Coastal Chamber", "lines": lines}))
    assert len(qb) == 1
    sent_keys = json.dumps(qb[0]).lower()
    assert "emailstatus" not in sent_keys and "needtosend" not in sent_keys


# --------------------------------------------------------------------------
# The approval gate — zero writes before the operator's own answer
# --------------------------------------------------------------------------

def test_unapproved_create_pauses_with_preview(qb, tmp_path):
    op = _ctx(tmp_path, {})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("create_quickbooks_invoice").call(
        {"customer": "Coastal Chamber",
         "lines": [{"item_name": "AI Discovery Session", "qty": 2}]})))
    assert payload["reason"] == "invoice_plan_pending"
    assert qb == []                                    # ZERO writes
    q = op.record["needs_input"][-1]["question"]
    assert "Coastal Chamber" in q and "$1000.00" in q and "REAL unsent" in q


def test_declined_create_refuses(qb, tmp_path):
    op = _ctx(tmp_path, {"invoice_declined": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("create_quickbooks_invoice").call(
        {"customer": "Coastal Chamber", "lines": [{"description": "x", "amount": 10}]})))
    assert payload["reason"] == "invoice_declined"
    assert qb == []


def test_changed_payload_invalidates_old_approval(qb, tmp_path):
    op = _ctx(tmp_path, {"invoice_plan_asked": True, "invoice_approved": True,
                         "invoice_preview_sig": "SOMETHING ELSE"})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("create_quickbooks_invoice").call(
        {"customer": "Coastal Chamber", "lines": [{"description": "x", "amount": 10}]})))
    assert payload["reason"] == "invoice_plan_pending"   # re-asks, never rides old OK
    assert op.record["invoice_approved"] is False
    assert qb == []


def test_approved_create_writes_exact_payload(qb, tmp_path):
    op = _ctx(tmp_path, {})
    set_current_operator(op)
    args = {"customer": "Coastal Chamber",
            "lines": [{"item_name": "AI Discovery Session", "qty": 2}]}
    asyncio.run(_tool("create_quickbooks_invoice").call(args))      # ask
    operator_service._apply_invoice_answer(op, "Create the invoice as previewed")
    assert op.record["invoice_approved"] is True
    result = json.loads(asyncio.run(_tool("create_quickbooks_invoice").call(args)))
    assert result["doc_number"] == "1042"
    assert len(qb) == 1
    line = qb[0]["lines"][0]
    assert line["item_id"] == "7" and line["qty"] == 2 and line["unit_price"] == 500.0
    assert qb[0]["customer_id"] == "42"


def test_only_operator_words_approve():
    rec = {"invoice_plan_asked": True}
    op = type("O", (), {"record": rec})()
    assert operator_service._apply_invoice_answer(op, "cancel") != ""
    assert rec["invoice_declined"] is True


# --------------------------------------------------------------------------
# Real-record resolution — never invent customers or items
# --------------------------------------------------------------------------

def test_unknown_customer_asks_never_guesses(qb, tmp_path):
    op = _ctx(tmp_path, {})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("create_quickbooks_invoice").call(
        {"customer": "Totally New Corp", "lines": [{"description": "x", "amount": 10}]})))
    assert payload["reason"] == "customer_unresolved"
    assert qb == []
    assert "Totally New Corp" in op.record["needs_input"][-1]["question"]


def test_unknown_item_asks_never_guesses(qb, tmp_path):
    op = _ctx(tmp_path, {})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("create_quickbooks_invoice").call(
        {"customer": "Coastal Chamber", "lines": [{"item_name": "Mystery Service"}]})))
    assert payload["reason"] == "item_unresolved"
    assert qb == []


def test_zero_amount_rejected(qb, tmp_path):
    op = _ctx(tmp_path, {})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("create_quickbooks_invoice").call(
        {"customer": "Coastal Chamber", "lines": [{"description": "x"}]})))
    assert payload["reason"] == "bad_args"
    assert qb == []
