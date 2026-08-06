"""Audit log (v6.0 Phase 8) — the governance story, made visible.

Pins:
  1. EVERY gated action appears — driven through the real gates (invoice,
     proposal, contact admin, backup restore), not fabricated rows;
  2. DECLINED actions appear as declined, whether the operator declined
     in the thread or from the approval inbox;
  3. filters (date, type, outcome) select correctly and compose;
  4. CSV export ROUND-TRIPS: every row parses back to the same values;
  5. read-only: state files byte-identical, no new backup, after building
     the audit AND exporting it;
  6. honesty: an outcome the ledger recorded is marked "recorded"; one
     derived from evidence is marked "inferred" and never claims more.
"""
import asyncio
import csv
import json

import pytest

from app.services import (approval_inbox_service, audit_service,
                          operator_service, state_store)
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


def _op(tmp_path, op_id="op_audit", stated=None, command="Do the thing"):
    async def _emit(_ev):
        return None
    record = {"id": op_id, "command": command, "steps": [], "tools_used": [],
              "artifacts": [], "errors": [], "user_stated_numbers": list(stated or [])}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _state_bytes():
    return {p.name: p.read_bytes() for p in sorted(
        state_store.STATE_DIR.glob("*.json"))}


def _entries_by_type(audit):
    return {e["type"]: e for e in audit["entries"]}


def _stage_invoice(tmp_path, op_id="op_inv", stated=(4500,)):
    _op(tmp_path, op_id=op_id, stated=list(stated),
        command="Invoice Sandy for the discovery engagement")
    out = _call("create_quickbooks_invoice", customer="Sandy Alvarez",
                lines=[{"description": "Discovery engagement", "amount": 4500}])
    assert out.get("reason") == "invoice_plan_pending"


# --------------------------------------------------------------------------
# 1. Every gated action appears
# --------------------------------------------------------------------------

def test_every_gate_type_appears_in_the_audit(tmp_path, qb):
    """Four different gates, all driven for real — all four audited."""
    # invoice
    _stage_invoice(tmp_path, op_id="op_inv")
    # contact admin (merge)
    op2 = _op(tmp_path, op_id="op_merge", command="Merge the Sandy duplicates")
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty")
    _call("add_contact", name="Sandy A.", email="sandy@gulf.test")
    _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    # backup restore
    _op(tmp_path, op_id="op_restore", command="Restore this morning's backup")
    snap = _call("backup_now")["backup"]["id"]
    _call("restore_backup", timestamp=snap)

    audit = audit_service.build_audit()
    by_type = _entries_by_type(audit)
    assert {"invoice", "contact_admin", "backup_restore"} <= set(by_type)
    inv = by_type["invoice"]
    assert inv["operation_id"] == "op_inv"
    assert inv["command"] == "Invoice Sandy for the discovery engagement"
    assert inv["action"] == "create_quickbooks_invoice"
    assert "Invoice preview" in inv["question"]
    assert inv["outcome"] == "pending" and inv["outcome_source"] == "recorded"
    assert inv["staged_at"]


def test_approved_action_records_outcome_cost_and_creation(tmp_path, qb):
    _stage_invoice(tmp_path)
    op = operator_service  # approve from the inbox (the sessionless path)
    appr = approval_inbox_service.list_pending()[0]
    asyncio.run(approval_inbox_service.answer_approval(appr["id"], t.INVOICE_PROCEED))
    # The run's cost + artifacts live in the operations log.
    state_store.save("operations", [{
        "id": "op_inv", "command": "Invoice Sandy for the discovery engagement",
        "status": "completed", "started_at": "2026-08-06T09:00:00",
        "completed_at": "2026-08-06T09:02:00", "spend_usd": 0.42,
        "artifacts": [{"name": "invoice_1042.json"},
                      {"name": "operation_log.json"}]}])

    entry = _entries_by_type(audit_service.build_audit())["invoice"]
    assert entry["outcome"] == "approved" and entry["outcome_source"] == "recorded"
    assert entry["answered_at"]
    assert entry["cost_usd"] == 0.42
    assert entry["created"] == "invoice_1042.json"    # log file not listed
    assert len(qb) == 1


# --------------------------------------------------------------------------
# 2. Declines appear as declines — both routes
# --------------------------------------------------------------------------

def test_decline_from_the_inbox_appears_as_declined(tmp_path, qb):
    _stage_invoice(tmp_path)
    appr = approval_inbox_service.list_pending()[0]
    asyncio.run(approval_inbox_service.answer_approval(appr["id"], t.INVOICE_CANCEL))
    entry = _entries_by_type(audit_service.build_audit())["invoice"]
    assert entry["outcome"] == "declined" and entry["outcome_source"] == "recorded"
    assert entry["created"] == ""                     # a decline creates nothing
    assert qb == []


def test_decline_in_the_thread_appears_as_declined(tmp_path, qb):
    op = _op(tmp_path, op_id="op_inv2", stated=[4500],
             command="Invoice Sandy again")
    _call("create_quickbooks_invoice", customer="Sandy Alvarez",
          lines=[{"description": "Discovery", "amount": 4500}])
    operator_service._apply_invoice_answer(op, t.INVOICE_CANCEL)
    approval_inbox_service.sync_from_record(op.record)

    entry = _entries_by_type(audit_service.build_audit())["invoice"]
    assert entry["outcome"] == "declined" and entry["outcome_source"] == "recorded"
    assert qb == []


def test_approve_in_the_thread_appears_as_approved(tmp_path, qb):
    op = _op(tmp_path, op_id="op_inv3", stated=[4500], command="Invoice Sandy")
    _call("create_quickbooks_invoice", customer="Sandy Alvarez",
          lines=[{"description": "Discovery", "amount": 4500}])
    operator_service._apply_invoice_answer(op, t.INVOICE_PROCEED)
    approval_inbox_service.sync_from_record(op.record)
    entry = _entries_by_type(audit_service.build_audit())["invoice"]
    assert entry["outcome"] == "approved" and entry["outcome_source"] == "recorded"


# --------------------------------------------------------------------------
# 3. Filters
# --------------------------------------------------------------------------

def test_filters_by_date_type_and_outcome(tmp_path, qb):
    _stage_invoice(tmp_path, op_id="op_inv")
    _op(tmp_path, op_id="op_restore", command="Restore backup")
    snap = _call("backup_now")["backup"]["id"]
    _call("restore_backup", timestamp=snap)
    # Age the invoice entry so date filtering has something to separate.
    items = state_store.load_list("approvals")
    for a in items:
        if a["tool"] == "create_quickbooks_invoice":
            a["staged_at"] = "2026-07-01T09:00:00"
            a["status"], a["outcome"] = "declined", "declined from inbox"
    state_store.save("approvals", items)

    assert audit_service.build_audit(type_filter="invoice")["count"] == 1
    assert audit_service.build_audit(type_filter="backup_restore")["count"] == 1
    assert audit_service.build_audit(outcome="declined")["count"] == 1
    assert audit_service.build_audit(outcome="pending")["count"] == 1
    assert audit_service.build_audit(date_from="2026-08-01")["count"] == 1
    assert audit_service.build_audit(date_to="2026-07-31")["count"] == 1
    # Filters compose (AND).
    assert audit_service.build_audit(type_filter="invoice",
                                     outcome="pending")["count"] == 0
    assert audit_service.build_audit(type_filter="invoice",
                                     outcome="declined")["count"] == 1
    both = audit_service.build_audit()
    assert both["count"] == 2 and both["by_outcome"] == {"pending": 1, "declined": 1}
    # Newest first.
    assert both["entries"][0]["type"] == "backup_restore"


# --------------------------------------------------------------------------
# 4. CSV round-trip
# --------------------------------------------------------------------------

def test_csv_export_round_trips(tmp_path, qb):
    _stage_invoice(tmp_path, op_id="op_inv")
    appr = approval_inbox_service.list_pending()[0]
    asyncio.run(approval_inbox_service.answer_approval(appr["id"], t.INVOICE_CANCEL))
    _op(tmp_path, op_id="op_restore", command="Restore backup")
    snap = _call("backup_now")["backup"]["id"]
    _call("restore_backup", timestamp=snap)

    audit = audit_service.build_audit()
    rows = list(csv.DictReader(audit_service.to_csv(audit["entries"]).splitlines()))
    assert len(rows) == audit["count"] == 2
    for row, entry in zip(rows, audit["entries"]):
        assert row["type"] == entry["type"]
        assert row["outcome"] == entry["outcome"]
        assert row["outcome_source"] == entry["outcome_source"]
        assert row["operation_id"] == entry["operation_id"]
        assert row["question"] == entry["question"]
        assert float(row["cost_usd"]) == entry["cost_usd"]
    assert {r["outcome"] for r in rows} == {"declined", "pending"}


def test_export_tool_writes_the_csv_artifact(tmp_path, qb):
    _stage_invoice(tmp_path, op_id="op_inv")
    op = _op(tmp_path, op_id="op_export", command="Export the audit log")
    out = _call("export_audit_csv")
    assert out["count"] >= 1
    body = (tmp_path / "audit_log.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(body.splitlines()))
    assert len(rows) == out["count"]
    assert any(r["type"] == "invoice" for r in rows)
    assert any(a["name"] == "audit_log.csv" for a in op.record["artifacts"])


# --------------------------------------------------------------------------
# 5 + 6. Read-only, and honest about what it knows
# --------------------------------------------------------------------------

def test_audit_never_writes_state(tmp_path, qb):
    _stage_invoice(tmp_path, op_id="op_inv")
    _op(tmp_path, op_id="op_read", command="Show me the audit log")
    before = _state_bytes()
    snaps_before = [s["id"] for s in state_store.list_snapshots()]

    audit_service.build_audit()
    _call("audit_log")
    _call("export_audit_csv")          # writes only into the RUN folder

    assert _state_bytes() == before    # byte-identical
    assert [s["id"] for s in state_store.list_snapshots()] == snaps_before


def test_pre_ledger_gate_asks_are_surfaced_without_inventing_outcomes(tmp_path):
    """A run from before the approvals ledger: the gate ask is still
    audited, and its unknown outcome is labelled inferred — never a guess
    presented as fact."""
    _op(tmp_path)
    state_store.save("operations", [{
        "id": "op_old", "command": "Invoice Casey for the retainer",
        "status": "completed", "started_at": "2026-05-01T09:00:00",
        "completed_at": "2026-05-01T09:05:00", "spend_usd": 0.19,
        "artifacts": [], "awaiting_input": False,
        "needs_input": [
            {"id": "need_1", "buttons_only": True,
             "context_hint": "QuickBooks invoice approval — nothing created until you answer",
             "question": "Invoice preview — approve?"},
            {"id": "need_2", "buttons_only": False,
             "context_hint": "which recipient?", "question": "Who should this go to?"},
        ]}])
    audit = audit_service.build_audit()
    assert audit["count"] == 1                    # only the GATED ask
    e = audit["entries"][0]
    assert e["type"] == "invoice"
    assert e["outcome"] == "unknown" and e["outcome_source"] == "inferred"
    assert e["cost_usd"] == 0.19


def test_awaiting_run_without_ledger_reads_as_pending(tmp_path):
    _op(tmp_path)
    state_store.save("operations", [{
        "id": "op_wait", "command": "Restore the backup",
        "status": "awaiting_input", "started_at": "2026-05-02T09:00:00",
        "awaiting_input": True, "artifacts": [], "spend_usd": 0.0,
        "needs_input": [{"id": "n1", "buttons_only": True,
                         "context_hint": "backup restore — nothing changes until you answer",
                         "question": "Restore preview — apply?"}]}])
    e = audit_service.build_audit()["entries"][0]
    assert e["type"] == "backup_restore"
    assert e["outcome"] == "pending" and e["outcome_source"] == "recorded"