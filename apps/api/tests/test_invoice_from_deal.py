"""invoice_deal (v5.0 Phase 6) — the pipeline wired to invoicing.

Pins:
  1. "Invoice Sandy for the discovery engagement" end-to-end: the deal's
     RECORDED value passes the line-provenance gate as "deal-record" (no
     user-stated number needed), the standard signature-matched approval
     pauses the run, and ONLY the approved second call creates the invoice;
  2. the deal flips to WON with a logged touch naming the invoice — only
     AFTER the invoice actually exists (pending/declined never mark won);
  3. tamper pin: a line citing a deal with an amount that is NOT the
     deal's stored value parks (the store is re-read; the planner's claim
     is never trusted);
  4. refusals: lost deals and value-less deals never reach QuickBooks.
"""
import asyncio
import json

import pytest

from app.services import operator_service, pipeline_service, state_store
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
                "total": 4500.0, "email_status": "NotSet",
                "link": "https://sandbox.qbo.intuit.com/app/invoice?txnId=99&deeplinkcompanyid=555"}

    monkeypatch.setattr(t.quickbooks_service, "create_invoice", fake_create)
    return created


def _op(tmp_path, stated=None):
    async def _emit(_ev):
        return None
    record = {"id": "op_p6", "steps": [], "tools_used": [], "artifacts": [],
              "errors": [], "user_stated_numbers": list(stated or [])}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _seed(tmp_path, value="4500", stage="proposal", stated=None):
    op = _op(tmp_path, stated=stated)
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty",
          email="sandy@gulf.test")
    _call("add_deal", contact="Sandy Alvarez", title="AI discovery engagement",
          stage=stage, value_usd=value)
    return op


def _deal():
    return pipeline_service.list_deals()[0]


# --------------------------------------------------------------------------
# The end-to-end flow: deal-record provenance + approval + won-on-create
# --------------------------------------------------------------------------

def test_invoice_sandy_end_to_end(tmp_path, qb):
    """NOTHING user-stated this run: the deal's recorded value alone
    satisfies the line gate (deal-record source), the approval pauses,
    and the approved call creates + marks won."""
    op = _seed(tmp_path)                                   # no stated numbers

    first = _call("invoice_deal", deal="Sandy")
    assert first.get("reason") == "invoice_plan_pending"   # standard approval ask
    need = op.record["needs_input"][-1]
    assert need["buttons_only"] is True and "$4500.00" in need["question"]
    assert qb == []                                        # nothing created
    assert _deal()["stage"] == "proposal"                  # NOT won while pending

    note = operator_service._apply_invoice_answer(op, t.INVOICE_PROCEED)
    assert "APPROVED" in note
    out = _call("invoice_deal", deal="Sandy")
    assert out["doc_number"] == "1042"
    assert len(qb) == 1
    assert qb[0]["customer_id"] == "42"                    # REAL QBO customer
    assert qb[0]["lines"][0]["amount"] == 4500.0

    won = _deal()
    assert won["stage"] == "won"
    assert out["deal"]["stage"] == "won"
    touch = won["touches"][-1]
    assert "1042" in touch["note"] and touch["written_by"] == "pipeline"


def test_declined_never_marks_won(tmp_path, qb):
    op = _seed(tmp_path)
    _call("invoice_deal", deal="Sandy")
    operator_service._apply_invoice_answer(op, t.INVOICE_CANCEL)
    out = _call("invoice_deal", deal="Sandy")
    assert out.get("reason") == "invoice_declined"
    assert qb == [] and _deal()["stage"] == "proposal"


# --------------------------------------------------------------------------
# Tamper pin: the store is the provenance, not the planner's claim
# --------------------------------------------------------------------------

def test_wrong_amount_with_deal_id_parks(tmp_path, qb):
    """A line citing the deal but carrying a DIFFERENT amount fails the
    deal-record check and parks — exactly like any unverified number."""
    op = _seed(tmp_path)
    out = _call("create_quickbooks_invoice", customer="Sandy Alvarez",
                lines=[{"description": "Discovery", "amount": 9999,
                        "deal_id": _deal()["id"]}])
    assert out.get("reason") == "line_value_unverified"
    assert qb == []
    assert "9999" in op.record["needs_input"][-1]["question"]


def test_deal_id_for_nonexistent_deal_parks(tmp_path, qb):
    _seed(tmp_path)
    out = _call("create_quickbooks_invoice", customer="Sandy Alvarez",
                lines=[{"description": "Discovery", "amount": 4500,
                        "deal_id": "deal_doesnotexist"}])
    assert out.get("reason") == "line_value_unverified"
    assert qb == []


# --------------------------------------------------------------------------
# Refusals before QuickBooks is ever touched
# --------------------------------------------------------------------------

def test_lost_deal_refuses(tmp_path, qb):
    _seed(tmp_path, stage="lost")
    out = _call("invoice_deal", deal="Sandy")
    assert "error" in out and "LOST" in out["error"]
    assert qb == []


def test_missing_value_parks(tmp_path, qb):
    op = _seed(tmp_path, value="")
    out = _call("invoice_deal", deal="Sandy")
    assert out.get("reason") == "deal_value_missing"
    assert "update_deal" in op.record["needs_input"][-1]["question"]
    assert qb == []


def test_unknown_deal_errors(tmp_path, qb):
    _op(tmp_path)
    out = _call("invoice_deal", deal="Nobody")
    assert "error" in out and "list_deals" in out["error"]
    assert qb == []
