"""Background (fire-and-forget) runs v3.6 — the SAFE-ONLY contract.

A background run may only do safe, reversible work. Every approval gate
already parks the run by construction (the pause doesn't depend on a
listening client); the ONE gap was save_memory, which writes directly when
the user's command contains a save verb. These tests pin the new
deterministic check: a background run's save_memory call is REFUSED — no
memory write happens unattended — and the reviewable proposal queue remains
the working path, so the save waits for the operator instead of vanishing.
"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import operator_service
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

client = TestClient(app)


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors",
                "proposed_memory_updates"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


def _tool(name):
    return next(x for x in t.PLANNER_TOOLS if x.name == name)


def _bomb_memory_writes(monkeypatch):
    """Any direct memory write during the test is a contract violation."""
    for fn in ("add_contact", "add_fact", "add_follow_up", "add_decision"):
        def _bomb(_data, _fn=fn):
            raise AssertionError(f"memory written unattended via {_fn}")
        monkeypatch.setattr(t.memory_service, fn, _bomb)


# --------------------------------------------------------------------------
# THE contract test: background save_memory refuses — queue, don't write
# --------------------------------------------------------------------------

def test_background_save_memory_refuses_and_routes_to_proposals(tmp_path, monkeypatch):
    """A background run whose command contained a save verb: save_memory
    must refuse (no write), and the proposal queue must still work — the
    exact park-don't-act pattern of every other gate, through the REAL
    registered tools."""
    _bomb_memory_writes(monkeypatch)
    op = _ctx(tmp_path, {"background": True})
    set_current_operator(op)

    payload = json.loads(asyncio.run(_tool("save_memory").call({
        "kind": "contact",
        "payload": {"name": "Sarah Chen", "email": "sarah@test.com"},
    })))
    assert payload["reason"] == "background_run"
    assert "propose_memory_update" in payload["error"]   # the instructed route
    assert op.record["proposed_memory_updates"] == []    # nothing written OR queued yet

    # The instructed route works in background: the save becomes a proposal
    # awaiting the operator's confirmation — queued, never silently written.
    prop = json.loads(asyncio.run(_tool("propose_memory_update").call({
        "kind": "contact",
        "payload": {"name": "Sarah Chen", "email": "sarah@test.com"},
        "reason": "operator asked to save this contact (background run)",
    })))
    assert prop["status"] == "proposed"
    assert op.record["proposed_memory_updates"][0]["status"] == "proposed"


def test_foreground_save_memory_still_writes(tmp_path, monkeypatch):
    """Control: the background check changes NOTHING for attended runs.
    Post-merge with governance/memory-provenance, an attended direct write
    also requires the operator's explicit save command (save_intent) and
    carries the written_by provenance stamp."""
    seen = {}

    def fake_add_contact(data, *, written_by, source_op=""):
        seen.update(data)
        seen["written_by"] = written_by
        return {"id": "c_1", "name": data.get("name", "")}

    monkeypatch.setattr(t.memory_service, "add_contact", fake_add_contact)
    # Attended run whose command contained a save verb — both gates open.
    op = _ctx(tmp_path, {"save_intent": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("save_memory").call({
        "kind": "contact",
        "payload": {"name": "Sarah Chen"},
    })))
    assert payload["status"] == "saved"
    assert seen["name"] == "Sarah Chen"
    assert seen["written_by"] == "save_memory"


def test_background_refusal_wins_even_with_save_intent(tmp_path, monkeypatch):
    """Gate order pinned: a background run with an explicit save verb in the
    command (save_intent True) still refuses as background_run — unattended
    beats commanded, matching the SAFE-ONLY contract."""
    _bomb_memory_writes(monkeypatch)
    op = _ctx(tmp_path, {"background": True, "save_intent": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("save_memory").call({
        "kind": "contact",
        "payload": {"name": "Sarah Chen"},
    })))
    assert payload["reason"] == "background_run"


def test_background_refusal_covers_every_kind(tmp_path, monkeypatch):
    _bomb_memory_writes(monkeypatch)
    op = _ctx(tmp_path, {"background": True})
    set_current_operator(op)
    for kind, payload in (
        ("fact", {"fact": "Ridian launched Operator v3 in July 2026", "source": "operator"}),
        ("follow_up", {"what": "check in with the Chamber"}),
        ("decision", {"decision": "research runs stay on Sonnet"}),
    ):
        out = json.loads(asyncio.run(_tool("save_memory").call(
            {"kind": kind, "payload": payload})))
        assert out["reason"] == "background_run", kind


# --------------------------------------------------------------------------
# The background flag — mid-run flip + snapshot
# --------------------------------------------------------------------------

def _fake_session(tmp_path, record):
    op = _ctx(tmp_path, record)
    return operator_service._OperationSession(
        operator=op, folder=Path(tmp_path), system="s", input_list=[],
        upload_state_line="")


def test_mark_background_flips_live_session(tmp_path):
    record = {"id": "op_bg1"}
    operator_service._SESSIONS["op_bg1"] = _fake_session(tmp_path, record)
    try:
        assert operator_service.mark_background("op_bg1") is True
        assert record["background"] is True
    finally:
        operator_service._SESSIONS.pop("op_bg1", None)


def test_mark_background_false_when_session_gone():
    assert operator_service.mark_background("op_nope") is False


def test_background_endpoint(tmp_path):
    record = {"id": "op_bg2"}
    operator_service._SESSIONS["op_bg2"] = _fake_session(tmp_path, record)
    try:
        res = client.post("/operations/op_bg2/background")
        assert res.status_code == 200
        assert record["background"] is True
    finally:
        operator_service._SESSIONS.pop("op_bg2", None)
    assert client.post("/operations/op_gone/background").status_code == 404


def test_finalized_view_carries_background_flag():
    from app.services import operation_log_service as ols
    record = ols.build_record(command="research x", intent="planner",
                              artifact_folder="f")
    assert operator_service._finalized_view(record)["background"] is False
    record["background"] = True
    assert operator_service._finalized_view(record)["background"] is True
