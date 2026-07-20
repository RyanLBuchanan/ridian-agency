"""Memory write-path provenance + the save_memory code gate.

Every memory record is stamped at write time with written_by ("commit" |
"save_memory" | "manual") and the source operation — required, no default,
so a future write path can't silently claim a plausible label. "unknown"
exists only on backfilled pre-feature records and is rejected as a write
value. save_memory refuses in code unless the OPERATOR's own words commanded
a save — planner-inferred learnings can only become proposal cards.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app import main as m
from app.services import memory_service
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


# --------------------------------------------------------------------------
# The stamp itself
# --------------------------------------------------------------------------

def test_written_by_is_required_no_default():
    """Forgetting to declare the write path is a loud TypeError, not a
    quietly-inherited label."""
    with pytest.raises(TypeError):
        memory_service.add_fact({"fact": "x"})           # no written_by
    with pytest.raises(TypeError):
        memory_service.add_contact({"name": "x"})


def test_stamp_rejects_unknown_and_bogus_values():
    """'unknown' is reserved for the one-time backfill of pre-feature
    records — new code can never write it, nor any invented label."""
    with pytest.raises(ValueError):
        memory_service._stamp({}, "unknown", "")
    with pytest.raises(ValueError):
        memory_service._stamp({}, "planner", "")
    entry = memory_service._stamp({}, "manual", "")
    assert entry == {"written_by": "manual", "source_op": ""}


# --------------------------------------------------------------------------
# Path 1: approval/commit endpoint stamps written_by="commit" + operation id
# --------------------------------------------------------------------------

def test_commit_proposal_stamps_commit_and_operation(monkeypatch):
    seen = {}

    def fake_add_fact(data, *, written_by, source_op=""):
        seen.update({"data": data, "written_by": written_by, "source_op": source_op})
        return {"id": "f1", **data}

    monkeypatch.setattr(m.memory_service, "add_fact", fake_add_fact)
    m._commit_proposal(
        {"kind": "fact", "payload": {"fact": "the sky is blue"}},
        operation_id="op_abc123",
    )
    assert seen["written_by"] == "commit"
    assert seen["source_op"] == "op_abc123"
    assert seen["data"]["fact"] == "the sky is blue"


# --------------------------------------------------------------------------
# Path 2: save_memory — gate + stamp, through the REAL registered tool
# --------------------------------------------------------------------------

def _memory_bomb(monkeypatch):
    calls = {"n": 0}

    def bomb(*a, **kw):
        calls["n"] += 1
        raise AssertionError("memory write happened without an explicit save command")

    for fn in ("add_contact", "add_fact", "add_follow_up", "add_decision"):
        monkeypatch.setattr(t.memory_service, fn, bomb)
    return calls


def test_save_memory_refuses_planner_initiated_save(tmp_path, monkeypatch):
    """No save-verb in the operator's text → the direct write CANNOT happen,
    and the planner is steered to the proposal card."""
    calls = _memory_bomb(monkeypatch)
    op = _ctx(tmp_path, {"id": "op_x", "save_intent": False})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("save_memory").call({
        "kind": "contact", "payload": {"name": "Sneaky", "email": "s@x.com"},
    })))
    assert payload["reason"] == "save_not_commanded"
    assert "propose_memory_update" in payload["error"]
    assert calls["n"] == 0                       # zero writes — the guarantee


def test_save_memory_allows_explicit_command_and_stamps(tmp_path, monkeypatch):
    seen = {}

    def fake_add_contact(data, *, written_by, source_op=""):
        seen.update({"data": data, "written_by": written_by, "source_op": source_op})
        return {"id": "c1", **data}

    monkeypatch.setattr(t.memory_service, "add_contact", fake_add_contact)
    op = _ctx(tmp_path, {"id": "op_y", "save_intent": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("save_memory").call({
        "kind": "contact", "payload": {"name": "Ada", "email": "a@x.org"},
    })))
    assert payload["status"] == "saved"
    assert seen["written_by"] == "save_memory"
    assert seen["source_op"] == "op_y"


# --------------------------------------------------------------------------
# Path 3: manual Memory-panel POSTs stamp written_by="manual"
# --------------------------------------------------------------------------

def test_manual_endpoint_stamps_manual(monkeypatch):
    seen = {}

    def fake_add_contact(data, *, written_by, source_op=""):
        seen.update({"written_by": written_by, "source_op": source_op})
        return {"id": "c2", **data}

    monkeypatch.setattr(m.memory_service, "add_contact", fake_add_contact)
    payload = m.ContactPayload(name="Manual Entry", email="me@x.com")
    asyncio.run(m.memory_contacts_create(payload))
    assert seen["written_by"] == "manual"
    assert seen["source_op"] == ""


# --------------------------------------------------------------------------
# The save-verb detector — the operator's words, deterministically
# --------------------------------------------------------------------------

def test_detect_save_intent_positives():
    for text in (
        "Remember that my preferred font is Georgia",
        "Add a contact: Ada's Kids initiative, adaskids@example.org",
        "save this as a follow-up for next week",
        "Please note that down for later",
        "keep track of this vendor",
        "yes, remember that",
    ):
        assert t.detect_save_intent(text), text


def test_detect_save_intent_negatives():
    for text in (
        "Draft a check-in to Sarah",
        "Build a research packet on agentic AI frameworks this week",
        "How do you feel?",
        "Sweep my contacts and follow-ups, and draft emails",
        "",
    ):
        assert not t.detect_save_intent(text), text
