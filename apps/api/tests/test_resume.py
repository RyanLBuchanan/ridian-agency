"""Phase 2 — resumable operations: grounding-answer relaxation, awaiting_input
flag, and finalize-once (single history entry across pause -> resume).

The full planner run needs the SDK + network, so these tests cover the
deterministic pieces offline.
"""
import asyncio
from pathlib import Path

from app.services import operation_log_service as ols
from app.services import operator_service as osvc
from app.services.operator_context import OperatorContext


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    record.setdefault("steps", [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


# --------------------------------------------------------------------------
# _apply_grounding_answer — source-lock resume relaxation
# --------------------------------------------------------------------------

def test_grounding_answer_general_unlocks(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y"})
    note = osvc._apply_grounding_answer(op, "a - just do general web research instead")
    assert op.record.get("grounding_override") is True
    assert "general" in note.lower()


def test_grounding_answer_paste_writes_source(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y"})
    pasted = "Gold membership is $500 a year and includes ribbon cuttings. " * 4  # >120
    note = osvc._apply_grounding_answer(op, pasted)
    assert op.record.get("grounding_ok") is True
    assert (Path(tmp_path) / "source.md").is_file()
    assert "source.md" in note


def test_grounding_answer_noop_when_not_locked(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": ""})
    assert osvc._apply_grounding_answer(op, "general research please") == ""


def test_grounding_answer_noop_when_already_grounded(tmp_path):
    op = _ctx(tmp_path, {"source_locked_url": "https://x/y", "grounding_ok": True})
    assert osvc._apply_grounding_answer(op, "general research") == ""
    assert op.record.get("grounding_override") is None


# --------------------------------------------------------------------------
# emit_needs_input flags the run as awaiting
# --------------------------------------------------------------------------

def test_emit_needs_input_sets_awaiting(tmp_path):
    op = _ctx(tmp_path, {"steps": []})
    asyncio.run(op.emit_needs_input(question="Which email should I use?"))
    assert op.record.get("awaiting_input") is True


# --------------------------------------------------------------------------
# finalize-once: a pause upsert then a completion upsert = ONE history entry
# --------------------------------------------------------------------------

def test_upsert_operation_is_idempotent_by_id(monkeypatch):
    store = {"operations": []}
    monkeypatch.setattr(ols.state_store, "load_list", lambda name: list(store.get(name, [])))
    monkeypatch.setattr(ols.state_store, "save", lambda name, items: store.__setitem__(name, items))

    ols.upsert_operation({"id": "op_1", "status": "awaiting_input"})   # paused
    ols.upsert_operation({"id": "op_1", "status": "completed"})        # resumed -> done
    ols.upsert_operation({"id": "op_2", "status": "completed"})        # a different run

    ops = store["operations"]
    ids = [o["id"] for o in ops]
    assert ids.count("op_1") == 1          # not duplicated across pause -> resume
    assert len(ops) == 2
    op1 = next(o for o in ops if o["id"] == "op_1")
    assert op1["status"] == "completed"    # updated in place
