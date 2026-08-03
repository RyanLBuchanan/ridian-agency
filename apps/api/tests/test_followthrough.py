"""Follow-through (v5.0 Phase 3): grounded follow-up drafts + weekly review.

Pins:
  1. draft_followup refuses BEFORE composing when the contact is unknown /
     ambiguous / has no email on record / has no logged touch — a follow-up
     recaps something that actually happened;
  2. the composed draft goes through the SAME gated path as any draft
     (_draft_gmail → recipient provenance gate → gmail_service.create_draft),
     to the contact RECORD's address, and the composer receives ONLY logged
     record facts (the touch note rides in; nothing else invents content);
  3. weekly_review is read-only: pipeline classification + unpaid-invoice
     surfacing, and it degrades gracefully when QuickBooks is unavailable —
     with zero writes to any store.
"""
import asyncio
import json

import pytest

from app.services import pipeline_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


def _op(tmp_path):
    async def _emit(_ev):
        return None
    record = {"id": "op_ft1", "steps": [], "tools_used": [],
              "artifacts": [], "errors": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


def _call(_tool_name, **kwargs):
    raw = asyncio.run(_tool(_tool_name).call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _seed(tmp_path, with_touch=True, with_email=True):
    op = _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty",
          email=("sandy@gulfrealty.test" if with_email else ""))
    _call("add_deal", contact="Sandy Alvarez", title="AI discovery engagement",
          stage="proposal", value_usd="4500",
          next_action="Send the proposal", next_action_date="2026-08-07")
    if with_touch:
        _call("log_touch", deal="Sandy",
              note="Walked through the discovery scope; she wants the proposal by Friday.")
    return op


class _AgentBomb:
    def __init__(self):
        self.called = False

    async def __call__(self, *a, **k):
        self.called = True
        raise AssertionError("composer must not run on a refused follow-up")


# --------------------------------------------------------------------------
# Refusals BEFORE composing
# --------------------------------------------------------------------------

def test_refuses_unknown_contact_before_composing(tmp_path, monkeypatch):
    _op(tmp_path)
    bomb = _AgentBomb()
    monkeypatch.setattr(t, "run_text_agent", bomb)
    out = _call("draft_followup", contact="Nobody Realman")
    assert "error" in out and "add_contact" in out["error"]
    assert bomb.called is False


def test_refuses_contact_without_email(tmp_path, monkeypatch):
    _seed(tmp_path, with_email=False)
    bomb = _AgentBomb()
    monkeypatch.setattr(t, "run_text_agent", bomb)
    out = _call("draft_followup", contact="Sandy Alvarez")
    assert "error" in out and "update_contact" in out["error"]
    assert bomb.called is False


def test_refuses_when_no_touch_logged(tmp_path, monkeypatch):
    _seed(tmp_path, with_touch=False)
    bomb = _AgentBomb()
    monkeypatch.setattr(t, "run_text_agent", bomb)
    out = _call("draft_followup", contact="Sandy Alvarez")
    assert "error" in out and "log_touch" in out["error"]
    assert bomb.called is False


# --------------------------------------------------------------------------
# The grounded, gated happy path
# --------------------------------------------------------------------------

def test_draft_goes_through_the_gated_path_with_record_facts(tmp_path, monkeypatch):
    _seed(tmp_path)
    seen = {}

    async def fake_agent(system, user_input, **kw):
        seen["system"] = system
        seen["facts"] = user_input
        return "Quick recap + proposal timing\n\nGreat speaking today — proposal by Friday.\n— Ryan"

    created = {}

    def fake_create_draft(to, subject, body, **kw):
        created.update({"to": to, "subject": subject, "body": body})
        return {"draft_id": "d123", "compose_url": "https://mail.google.com/x", "to": to}

    monkeypatch.setattr(t, "run_text_agent", fake_agent)
    monkeypatch.setattr(t.gmail_service, "create_draft", fake_create_draft)

    out = _call("draft_followup", contact="Sandy",
                context="Mention the Friday deadline.")

    # The composer saw ONLY logged record facts — including the touch note,
    # the deal, the next action, and the operator guidance.
    assert "she wants the proposal by Friday" in seen["facts"]
    assert "AI discovery engagement" in seen["facts"]
    assert "Send the proposal" in seen["facts"]
    assert "Mention the Friday deadline." in seen["facts"]
    assert "Never invent" in seen["system"]

    # The draft went to the contact RECORD's address through _draft_gmail
    # (recipient gate passed by construction) — and was created as a DRAFT.
    assert created["to"] == "sandy@gulfrealty.test"
    assert created["subject"] == "Quick recap + proposal timing"
    assert "Great speaking today" in created["body"]
    assert out.get("draft_id") == "d123"


def test_recipient_gate_still_refuses_invented_addresses(tmp_path, monkeypatch):
    """The gate itself is untouched: a direct draft to an address on no
    record still refuses (the follow-up path can't weaken it)."""
    op = _seed(tmp_path)
    result = asyncio.run(t._draft_gmail(op, "invented@nowhere.example", "s", "b"))
    assert "error" in result or "question" in json.dumps(result).lower()
    assert "invented@nowhere.example" not in json.dumps(result.get("draft_id", ""))


# --------------------------------------------------------------------------
# weekly_review — read-only
# --------------------------------------------------------------------------

def test_weekly_review_shapes_and_qbo_degradation(tmp_path, monkeypatch):
    _seed(tmp_path)   # Sandy: touched today -> not stale; action due soon

    monkeypatch.setattr(t.quickbooks_service, "list_invoices", lambda limit=50: [
        {"id": "1", "doc_number": "1001", "customer": "Coastal",
         "total": 500.0, "balance": 500.0, "email_status": "NotSet"},
        {"id": "2", "doc_number": "1002", "customer": "Gulf",
         "total": 900.0, "balance": 0.0, "email_status": "EmailSent"},
    ])
    before = json.dumps(pipeline_service.list_deals(), sort_keys=True)
    out = _call("weekly_review")
    after = json.dumps(pipeline_service.list_deals(), sort_keys=True)

    assert before == after                          # READ-ONLY, provably
    assert [v["doc_number"] for v in out["unpaid_invoices"]] == ["1001"]
    assert out["due_actions"] and out["due_actions"][0]["contact"] == "Sandy Alvarez"

    # QuickBooks down -> the rest of the review still lands.
    def boom(limit=50):
        raise t.quickbooks_service.QuickBooksError("QuickBooks is not connected.", 400)
    monkeypatch.setattr(t.quickbooks_service, "list_invoices", boom)
    out2 = _call("weekly_review")
    assert "unpaid_invoices_error" in out2
    assert "due_actions" in out2 and "stale_deals" in out2
