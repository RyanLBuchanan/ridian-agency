"""draft_proposal (v5.0 Phase 5) — the invoice provenance pattern, exactly.

Pins:
  1. price provenance: operator-stated or the deal record's value — an
     invented price PARKS (needs-input) and composes nothing; timeline
     numbers obey the same rule;
  2. the approval gate is signature-matched and buttons-only: the first
     call asks and writes NOTHING; approval comes only through
     _apply_proposal_answer; changed arguments after approval re-ask;
     declined stays declined;
  3. after approval the composed document passes the deterministic number
     gate (lines with unsanctioned numbers are STRIPPED) and lands as
     proposal.md + proposal.docx in the run folder.
"""
import asyncio
import json

import pytest

from app.services import operator_service, pipeline_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


def _op(tmp_path, stated=None):
    async def _emit(_ev):
        return None
    record = {"id": "op_prop1", "steps": [], "tools_used": [], "artifacts": [],
              "errors": [], "user_stated_numbers": list(stated or [])}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _seed_deal(tmp_path, stated=None, value="4500"):
    op = _op(tmp_path, stated=stated)
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty",
          email="sandy@gulfrealty.test")
    _call("add_deal", contact="Sandy Alvarez", title="AI discovery engagement",
          stage="proposal", value_usd=value)
    return op


_COMPOSED = """# Proposal: AI discovery engagement — Sandy Alvarez

## Scope
Discovery of automation opportunities across intake and scheduling.

## Deliverables
A findings report and a prioritized automation roadmap.

## Timeline
Three weeks from signature.

## Price
$4,500.00, fixed.

## Terms
We can add a 15% rush surcharge for a 10 day turnaround.
Payment on invoice via QuickBooks; work begins on signature.
"""


def _fake_composer(text=_COMPOSED):
    async def fake(system, user_input, **kw):
        assert "enforced by CODE" in system
        return text
    return fake


# --------------------------------------------------------------------------
# Price / timeline provenance (the invoice rule)
# --------------------------------------------------------------------------

def test_invented_price_parks_and_composes_nothing(tmp_path, monkeypatch):
    op = _seed_deal(tmp_path)                      # deal value 4500, nothing stated
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())
    out = _call("draft_proposal", deal="Sandy", price="9999")
    assert out.get("reason") == "price_unverified"
    assert op.record["needs_input"][-1]["question"].startswith("The price $9,999.00")
    assert not (tmp_path / "proposal.md").exists()


def test_deal_record_value_is_a_sanctioned_price(tmp_path, monkeypatch):
    """Blank price falls back to the deal's recorded value and passes the
    gate straight to the approval ask — the deal record is provenance."""
    op = _seed_deal(tmp_path)
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())
    out = _call("draft_proposal", deal="Sandy")
    assert out.get("reason") == "proposal_plan_pending"     # asked, not refused
    need = op.record["needs_input"][-1]
    assert need["buttons_only"] is True
    assert "$4,500.00" in need["question"]
    assert not (tmp_path / "proposal.md").exists()          # nothing written


def test_unstated_timeline_numbers_park(tmp_path, monkeypatch):
    _seed_deal(tmp_path)
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())
    out = _call("draft_proposal", deal="Sandy", timeline="6 weeks, 20 hours/week")
    assert out.get("reason") == "timeline_unverified"


def test_missing_price_everywhere_parks(tmp_path, monkeypatch):
    _seed_deal(tmp_path, value="")
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())
    out = _call("draft_proposal", deal="Sandy")
    assert out.get("reason") == "price_missing"


# --------------------------------------------------------------------------
# The approval gate (signature-matched, buttons-only, re-ask on change)
# --------------------------------------------------------------------------

def test_approval_flow_writes_only_after_proceed(tmp_path, monkeypatch):
    op = _seed_deal(tmp_path, stated=[4500])
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())

    first = _call("draft_proposal", deal="Sandy", price="4500")
    assert first.get("reason") == "proposal_plan_pending"
    assert not (tmp_path / "proposal.docx").exists()

    # Approval comes ONLY through the resume-answer applier (the planner
    # cannot set the flags) — then the SAME call proceeds.
    note = operator_service._apply_proposal_answer(op, t.PROPOSAL_PROCEED)
    assert "APPROVED" in note
    out = _call("draft_proposal", deal="Sandy", price="4500")
    assert "proposal_docx" in out
    assert (tmp_path / "proposal.md").exists()
    assert (tmp_path / "proposal.docx").exists()


def test_changed_args_after_approval_reask(tmp_path, monkeypatch):
    op = _seed_deal(tmp_path, stated=[4500, 5000])
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())
    _call("draft_proposal", deal="Sandy", price="4500")
    operator_service._apply_proposal_answer(op, t.PROPOSAL_PROCEED)
    # Different price after approval: signature mismatch -> re-ask, no write.
    out = _call("draft_proposal", deal="Sandy", price="5000")
    assert out.get("reason") == "proposal_plan_pending"
    assert not (tmp_path / "proposal.docx").exists()


def test_declined_stays_declined(tmp_path, monkeypatch):
    op = _seed_deal(tmp_path, stated=[4500])
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())
    _call("draft_proposal", deal="Sandy", price="4500")
    operator_service._apply_proposal_answer(op, t.PROPOSAL_CANCEL)
    out = _call("draft_proposal", deal="Sandy", price="4500")
    assert out.get("reason") == "proposal_declined"
    assert not (tmp_path / "proposal.md").exists()


# --------------------------------------------------------------------------
# The number gate on the finished document
# --------------------------------------------------------------------------

def test_unsanctioned_numbers_are_stripped_from_the_document(tmp_path, monkeypatch):
    op = _seed_deal(tmp_path, stated=[4500])
    monkeypatch.setattr(t, "run_text_agent", _fake_composer())
    _call("draft_proposal", deal="Sandy", price="4500")
    operator_service._apply_proposal_answer(op, t.PROPOSAL_PROCEED)
    out = _call("draft_proposal", deal="Sandy", price="4500")

    body = (tmp_path / "proposal.md").read_text(encoding="utf-8")
    # The composer's invented "15% rush surcharge ... 10 day" line is GONE.
    assert "15%" not in body and "rush surcharge" not in body
    assert out["stripped_number_lines"] == 1
    # Sanctioned numbers survive.
    assert "$4,500.00" in body
    assert "Payment on invoice via QuickBooks" in body
    # And the docx is a real Word file built from the gated markdown.
    assert (tmp_path / "proposal.docx").stat().st_size > 1000
