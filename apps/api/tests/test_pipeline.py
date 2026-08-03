"""Contacts + deals pipeline (v5.0 Phase 1) — the back-office spine.

Pins:
  1. deterministic refusal BEFORE write: bad stage/value/date leaves the
     store untouched;
  2. a deal can only exist linked to a REAL contact record;
  3. provenance stamps (written_by="pipeline", source_op) on deals and
     touches, same contract as memory records;
  4. contacts added through the tool feed the recipient provenance gate —
     contact records are the ONLY valid email source downstream;
  5. the follow-up report classifies stale/due and ignores closed deals.
"""
import asyncio
import datetime as dt
import json

import pytest

from app.services import memory_service, pipeline_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


def _op(tmp_path):
    async def _emit(_ev):
        return None
    record = {"id": "op_test123", "steps": [], "tools_used": [],
              "artifacts": [], "errors": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


def _call(_tool_name, **kwargs):
    raw = asyncio.run(_tool(_tool_name).call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


# --------------------------------------------------------------------------
# Refuse-before-write (deterministic Python, no prompt rules)
# --------------------------------------------------------------------------

def test_invalid_stage_value_date_refuse_before_any_write():
    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.add_deal({"contact_id": "c1", "stage": "negotiation"},
                                  written_by="pipeline")
    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.add_deal({"contact_id": "c1", "value_usd": "a lot"},
                                  written_by="pipeline")
    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.add_deal({"contact_id": "c1", "value_usd": "-5"},
                                  written_by="pipeline")
    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.add_deal({"contact_id": "c1", "next_action_date": "next tuesday"},
                                  written_by="pipeline")
    assert pipeline_service.list_deals() == []          # nothing landed


def test_update_refuses_invalid_stage_without_writing():
    deal = pipeline_service.add_deal({"contact_id": "c1", "stage": "lead"},
                                     written_by="pipeline")
    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.update_deal(deal["id"], {"stage": "maybe"})
    assert pipeline_service.list_deals()[0]["stage"] == "lead"


def test_deal_requires_contact_link():
    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.add_deal({"stage": "lead"}, written_by="pipeline")


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

def test_deals_and_touches_carry_provenance_stamps():
    deal = pipeline_service.add_deal(
        {"contact_id": "c1", "contact_name": "Sandy", "value_usd": "$4,500"},
        written_by="pipeline", source_op="op_abc")
    assert deal["written_by"] == "pipeline"
    assert deal["source_op"] == "op_abc"
    assert deal["value_usd"] == "4500.00"               # canonicalized
    touched = pipeline_service.log_touch(deal["id"], "Call went well",
                                         written_by="pipeline", source_op="op_abc")
    touch = touched["touches"][0]
    assert touch["written_by"] == "pipeline"
    assert touch["source_op"] == "op_abc"
    assert touched["last_touch_iso"] == touch["when"]


def test_bogus_written_by_refuses():
    with pytest.raises(ValueError):
        pipeline_service.add_deal({"contact_id": "c1"}, written_by="totally_legit")


# --------------------------------------------------------------------------
# Tools: the natural-language spine
# --------------------------------------------------------------------------

def test_add_contact_feeds_the_recipient_gate(tmp_path):
    """THE Phase-1 gate requirement: a contact added by the tool becomes a
    valid recipient; an address in nobody's record stays refused."""
    op = _op(tmp_path)
    out = _call("add_contact", name="Patrick", company="cpc-tx.com",
                email="patrick@cpc-tx.com", source="referral")
    assert out["contact"]["written_by"] == "pipeline"
    assert out["contact"]["source"] == "referral"
    assert t._recipient_is_known(op, "patrick@cpc-tx.com") is True
    assert t._recipient_is_known(op, "Patrick <patrick@cpc-tx.com>") is True
    assert t._recipient_is_known(op, "invented@nowhere.example") is False


def test_add_deal_tool_refuses_unknown_contact(tmp_path):
    _op(tmp_path)
    out = _call("add_deal", contact="Sandy")
    assert "error" in out and "add_contact" in out["error"]
    assert pipeline_service.list_deals() == []


def test_ambiguous_contact_returns_candidates_and_writes_nothing(tmp_path):
    _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty")
    _call("add_contact", name="Sandy Bell", company="Bell Dental")
    out = _call("add_deal", contact="Sandy")
    assert "error" in out and len(out["candidates"]) == 2
    assert pipeline_service.list_deals() == []


def test_move_sandy_to_proposal_4500(tmp_path):
    """'Move Sandy to proposal, $4,500.' — resolve deal by contact name,
    update stage + value in one call."""
    _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty",
          email="sandy@gulfrealty.test")
    _call("add_deal", contact="Sandy Alvarez", title="AI discovery engagement")
    out = _call("update_deal", deal="Sandy", stage="proposal", value_usd="$4,500")
    assert out["deal"]["stage"] == "proposal"
    assert out["deal"]["value_usd"] == "4500.00"


def test_log_touch_and_followup_report(tmp_path, monkeypatch):
    _op(tmp_path)
    _call("add_contact", name="Patrick Cole", email="p@cpc-tx.test")
    _call("add_contact", name="Sandy Alvarez", email="s@gulf.test")
    _call("add_deal", contact="Patrick Cole", title="Automation audit")
    _call("add_deal", contact="Sandy Alvarez", title="Discovery",
          next_action="Send proposal", next_action_date="2026-08-05")
    _call("log_touch", deal="Sandy", note="Great call, wants a proposal.")

    today = dt.date(2026, 8, 3)
    report = pipeline_service.deals_needing_followup(today=today)
    stale_names = [d["contact_name"] for d in report["stale"]]
    due_names = [d["contact_name"] for d in report["due"]]
    assert stale_names == ["Patrick Cole"]      # never touched
    assert due_names == ["Sandy Alvarez"]       # action due within 7 days

    # Closed deals leave the report entirely.
    sandy_deal = report["due"][0]
    pipeline_service.update_deal(sandy_deal["id"], {"stage": "won"})
    report2 = pipeline_service.deals_needing_followup(today=today)
    assert [d["contact_name"] for d in report2["due"]] == []


def test_list_tools_are_read_only_shapes(tmp_path):
    _op(tmp_path)
    _call("add_contact", name="Patrick Cole", company="CPC")
    _call("add_deal", contact="Patrick Cole", title="Audit", stage="meeting")
    contacts = _call("list_contacts", query="cpc")
    assert contacts["count"] == 1
    deals = _call("list_deals", stage="meeting")
    assert deals["count"] == 1
    bad = _call("list_deals", stage="negotiation")
    assert "error" in bad
    followup = _call("list_deals", needs_followup=True)
    assert set(followup.keys()) >= {"stale", "due"}


def test_duplicate_contact_refused_with_pointer_to_update(tmp_path):
    _op(tmp_path)
    _call("add_contact", name="Patrick Cole", email="p@cpc-tx.test")
    out = _call("add_contact", name="Patrick Cole")
    assert "error" in out and "update_contact" in out["error"]
