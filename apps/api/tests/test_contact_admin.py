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
