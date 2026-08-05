"""merge_contacts / delete_contact (v5.1) — destructive contact admin.

Pins:
  1. merge moves the dropped contact's deals AND their logged touches onto
     the kept record, fills blank fields, then deletes the drop — no
     orphaned history;
  2. both tools are behind the signature-matched, buttons-only approval
     gate: the first call previews and writes NOTHING; approval comes only
     through _apply_contact_admin_answer; declined leaves both records
     intact;
  3. delete refuses while the contact has an OPEN deal;
  4. ambiguous names return candidates and write nothing.
"""
import asyncio
import json

import pytest

from app.services import memory_service, operator_service, pipeline_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


def _op(tmp_path):
    async def _emit(_ev):
        return None
    record = {"id": "op_admin", "steps": [], "tools_used": [], "artifacts": [],
              "errors": [], "user_stated_numbers": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _contact(name):
    return next(c for c in memory_service.list_contacts()
                if c.get("name") == name)


def _seed_duplicates(tmp_path):
    """Sandy twice: the keeper has the deal history but no email; the
    duplicate has the email and no deals."""
    op = _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty")
    _call("add_contact", name="Sandy A.", email="sandy@gulf.test",
          company="Gulf Realty LLC")
    dup = _contact("Sandy A.")
    _call("add_deal", contact=dup["id"], title="AI discovery engagement",
          stage="proposal", value_usd="4500")
    _call("log_touch", deal="discovery", note="Kickoff call went well.")
    return op


def _approve(op):
    note = operator_service._apply_contact_admin_answer(op, t.CONTACT_ADMIN_PROCEED)
    assert "APPROVED" in note
    return note


# --------------------------------------------------------------------------
# Merge: approval-gated, moves deals + touches, no orphaned history
# --------------------------------------------------------------------------

def test_merge_previews_first_and_writes_nothing(tmp_path):
    op = _seed_duplicates(tmp_path)
    out = _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    assert out.get("reason") == "contact_admin_pending"
    need = op.record["needs_input"][-1]
    assert need["buttons_only"] is True
    q = need["question"]
    assert "KEEP" in q and "DELETE" in q
    assert "AI discovery engagement" in q          # exactly what will move
    assert "1 logged touches" in q
    assert len(memory_service.list_contacts()) == 2    # nothing written
    assert pipeline_service.list_deals()[0]["contact_name"] == "Sandy A."


def test_merge_moves_deals_and_touches_then_deletes(tmp_path):
    op = _seed_duplicates(tmp_path)
    keep = _contact("Sandy Alvarez")
    _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    _approve(op)
    out = _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")

    assert out["moved_deals"] == 1 and out["moved_touches"] == 1
    # The deal (and its touch) now lives on the KEPT record.
    deal = pipeline_service.list_deals()[0]
    assert deal["contact_id"] == keep["id"]
    assert deal["contact_name"] == "Sandy Alvarez"
    assert deal["touches"][0]["note"] == "Kickoff call went well."
    # The keeper's blank email was filled from the dropped record...
    kept = _contact("Sandy Alvarez")
    assert kept["email"] == "sandy@gulf.test"
    assert kept["company"] == "Gulf Realty"        # non-blank field NOT overwritten
    assert "email" in out["filled_fields"]
    # ...and the duplicate is gone.
    assert len(memory_service.list_contacts()) == 1


def test_merge_declined_leaves_both_records_intact(tmp_path):
    op = _seed_duplicates(tmp_path)
    _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    operator_service._apply_contact_admin_answer(op, t.CONTACT_ADMIN_CANCEL)
    out = _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    assert out.get("reason") == "contact_admin_declined"
    assert len(memory_service.list_contacts()) == 2
    assert pipeline_service.list_deals()[0]["contact_name"] == "Sandy A."


def test_merge_approval_is_signature_matched(tmp_path):
    """An approval for one pair must not authorize a DIFFERENT pair."""
    op = _seed_duplicates(tmp_path)
    _call("add_contact", name="Casey Reed")
    _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    _approve(op)
    out = _call("merge_contacts", keep="Sandy Alvarez", drop="Casey Reed")
    assert out.get("reason") == "contact_admin_pending"     # re-asks, no write
    assert any(c["name"] == "Casey Reed" for c in memory_service.list_contacts())


def test_merge_ambiguous_returns_candidates(tmp_path):
    _seed_duplicates(tmp_path)
    out = _call("merge_contacts", keep="Sandy", drop="Sandy A.")
    assert "candidates" in out and len(out["candidates"]) == 2
    assert len(memory_service.list_contacts()) == 2


def test_merge_same_record_refuses(tmp_path):
    _seed_duplicates(tmp_path)
    out = _call("merge_contacts", keep="Sandy A.", drop="Sandy A.")
    assert "SAME contact" in out["error"]
    assert len(memory_service.list_contacts()) == 2


def test_merge_same_id_via_different_queries_refuses(tmp_path):
    """Self-merge guard binds to the RESOLVED id, not the query text:
    the id and the name are different strings for the same record."""
    _seed_duplicates(tmp_path)
    cid = _contact("Sandy Alvarez")["id"]
    out = _call("merge_contacts", keep=cid, drop="Sandy Alvarez")
    assert "SAME contact" in out["error"]
    assert len(memory_service.list_contacts()) == 2


def test_merge_receipt_disambiguates_same_display_names(tmp_path):
    """Two DISTINCT records can share a name — the success receipt must
    carry ids so it never reads like a self-merge."""
    op = _op(tmp_path)
    _call("add_contact", name="Patrick", company="Acme")
    _call("add_contact", name="Patrick", company="Beta Corp", email="p@beta.test")
    acme = next(c for c in memory_service.list_contacts() if c["company"] == "Acme")
    beta = next(c for c in memory_service.list_contacts() if c["company"] == "Beta Corp")
    _call("merge_contacts", keep=acme["id"], drop=beta["id"])
    operator_service._apply_contact_admin_answer(op, t.CONTACT_ADMIN_PROCEED)
    out = _call("merge_contacts", keep=acme["id"], drop=beta["id"])
    assert out["merged"]["deleted_id"] == beta["id"]
    detail = next(s for s in op.record["steps"] if s["name"] == "contacts")["detail"]
    assert acme["id"] in detail and beta["id"] in detail
    assert "Acme" in detail and "Beta Corp" in detail


def test_second_pending_change_refuses_and_first_approval_still_binds(tmp_path):
    """Staging a second destructive change while one awaits its answer is
    refused outright — the pending approval stays bound to the FIRST."""
    op = _seed_duplicates(tmp_path)
    _call("add_contact", name="Casey Reed")           # no deals — deletable
    first = _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    assert first.get("reason") == "contact_admin_pending"

    second = _call("delete_contact", contact="Casey Reed")
    assert second.get("reason") == "contact_admin_conflict"
    assert any(c["name"] == "Casey Reed" for c in memory_service.list_contacts())

    # The approval binds to the merge that asked — and ONLY to it.
    _approve(op)
    out = _call("merge_contacts", keep="Sandy Alvarez", drop="Sandy A.")
    assert out["moved_deals"] == 1
    assert not any(c["name"] == "Sandy A." for c in memory_service.list_contacts())
    # Casey was never authorized for deletion by that approval.
    still = _call("delete_contact", contact="Casey Reed")
    assert still.get("reason") == "contact_admin_pending"     # re-asks fresh
    assert any(c["name"] == "Casey Reed" for c in memory_service.list_contacts())


# --------------------------------------------------------------------------
# Delete: open-deal refusal + the same gate
# --------------------------------------------------------------------------

def test_delete_refuses_with_open_deal(tmp_path):
    _seed_duplicates(tmp_path)               # Sandy A. holds an open deal
    out = _call("delete_contact", contact="Sandy A.")
    assert "OPEN deal" in out["error"] and out["open_deals"]
    assert len(memory_service.list_contacts()) == 2


def test_delete_gated_then_deletes_when_deals_closed(tmp_path):
    op = _seed_duplicates(tmp_path)
    _call("update_deal", deal="discovery", stage="lost")
    first = _call("delete_contact", contact="Sandy A.")
    assert first.get("reason") == "contact_admin_pending"
    assert len(memory_service.list_contacts()) == 2      # preview wrote nothing
    _approve(op)
    out = _call("delete_contact", contact="Sandy A.")
    assert out["deleted"]["name"] == "Sandy A."
    assert not any(c["name"] == "Sandy A." for c in memory_service.list_contacts())


def test_delete_declined_keeps_contact(tmp_path):
    op = _seed_duplicates(tmp_path)
    _call("update_deal", deal="discovery", stage="lost")
    _call("delete_contact", contact="Sandy A.")
    operator_service._apply_contact_admin_answer(op, t.CONTACT_ADMIN_CANCEL)
    out = _call("delete_contact", contact="Sandy A.")
    assert out.get("reason") == "contact_admin_declined"
    assert any(c["name"] == "Sandy A." for c in memory_service.list_contacts())
