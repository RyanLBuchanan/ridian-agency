"""Backup & restore (v6.0 Phase 1) — the state stores get a safety net.

Pins:
  1. every state_store.save() snapshots the PRE-write state first, so the
     newest backup always undoes the most recent change;
  2. rotation keeps exactly SNAPSHOT_KEEP (30) snapshots;
  3. restore_backup is behind the signature-matched, buttons-only approval
     gate with a preview naming what the snapshot contains and what will
     be overwritten; approval binds to ONE snapshot id;
  4. a declined restore leaves state byte-untouched;
  5. contacts + deals CSV export round-trips.
"""
import asyncio
import csv
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
    record = {"id": "op_backup", "steps": [], "tools_used": [], "artifacts": [],
              "errors": [], "user_stated_numbers": []}
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _seed(tmp_path):
    op = _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez", company="Gulf Realty",
          email="sandy@gulf.test")
    _call("add_deal", contact="Sandy Alvarez", title="AI discovery engagement",
          stage="proposal", value_usd="4500")
    _call("log_touch", deal="discovery", note="Kickoff call went well.")
    return op


def _state_bytes():
    """Byte-exact map of every store file — the 'untouched' oracle."""
    return {p.name: p.read_bytes() for p in sorted(
        state_store.STATE_DIR.glob("*.json"))}


# --------------------------------------------------------------------------
# Snapshot on write + rotation
# --------------------------------------------------------------------------

def test_every_write_snapshots_the_pre_write_state(tmp_path):
    _op(tmp_path)
    _call("add_contact", name="Sandy Alvarez")       # first write: no state yet
    _call("add_contact", name="Casey Reed")          # second write: snapshots first
    snaps = state_store.list_snapshots()
    assert snaps, "a write with existing state must leave a snapshot"
    newest = snaps[0]
    assert newest["reason"] == "pre-write contacts"
    assert newest["stores"] == {"contacts": 1}       # PRE-write: only Sandy
    # The snapshot file really contains the pre-write records.
    snap_file = (state_store._backups_dir() / newest["id"] / "contacts.json")
    names = [c["name"] for c in json.loads(snap_file.read_text(encoding="utf-8"))]
    assert names == ["Sandy Alvarez"]


def test_rotation_keeps_exactly_thirty(tmp_path):
    _op(tmp_path)
    for i in range(state_store.SNAPSHOT_KEEP + 5):
        state_store.save("contacts", [{"id": f"c{i}", "name": f"P{i}"}])
    snaps = state_store.list_snapshots()
    assert len(snaps) == state_store.SNAPSHOT_KEEP
    # Newest snapshot holds the state before the LAST write.
    newest_file = (state_store._backups_dir() / snaps[0]["id"] / "contacts.json")
    assert json.loads(newest_file.read_text(encoding="utf-8"))[0]["id"] == \
        f"c{state_store.SNAPSHOT_KEEP + 3}"


def test_backup_now_and_list(tmp_path):
    _seed(tmp_path)
    out = _call("backup_now")
    assert out["backup"]["reason"] == "manual (operator asked)"
    assert out["backup"]["stores"]["contacts"] == 1
    assert out["backup"]["stores"]["deals"] == 1
    listed = _call("list_backups")
    assert listed["count"] >= 1 and listed["keep_limit"] == 30
    assert listed["backups"][0]["id"] == out["backup"]["id"]


def test_backup_now_with_no_state_is_an_honest_error(tmp_path):
    _op(tmp_path)
    out = _call("backup_now")
    assert "error" in out and "Nothing to back up" in out["error"]


# --------------------------------------------------------------------------
# Restore: approval-gated, previewed, declined leaves state untouched
# --------------------------------------------------------------------------

def test_restore_requires_approval_and_restores_on_proceed(tmp_path):
    op = _seed(tmp_path)
    snap_id = _call("backup_now")["backup"]["id"]
    _call("add_contact", name="Casey Reed")          # state moves on
    assert len(memory_service.list_contacts()) == 2

    first = _call("restore_backup", timestamp=snap_id)
    assert first.get("reason") == "restore_pending"
    need = op.record["needs_input"][-1]
    assert need["buttons_only"] is True
    q = need["question"]
    assert snap_id in q and "OVERWRITE" in q
    assert "contacts: 1" in q                        # what the snapshot contains
    assert "contacts: 2" in q                        # what will be overwritten
    assert len(memory_service.list_contacts()) == 2  # nothing restored yet

    note = operator_service._apply_restore_answer(op, t.RESTORE_PROCEED)
    assert "APPROVED" in note
    out = _call("restore_backup", timestamp=snap_id)
    assert out["restored"] == snap_id
    assert out["pre_restore_backup"]                 # today's state is recoverable
    contacts = memory_service.list_contacts()
    assert [c["name"] for c in contacts] == ["Sandy Alvarez"]
    # The deal (and its touch) came back with the snapshot too.
    deal = pipeline_service.list_deals()[0]
    assert deal["touches"][0]["note"] == "Kickoff call went well."


def test_declined_restore_leaves_state_byte_untouched(tmp_path):
    op = _seed(tmp_path)
    snap_id = _call("backup_now")["backup"]["id"]
    _call("add_contact", name="Casey Reed")
    before = _state_bytes()
    _call("restore_backup", timestamp=snap_id)       # stages the ask
    # v6.0 Phase 3: the ASK itself is recorded in the approvals ledger — the
    # only file the staging may touch. Every BUSINESS store stays identical.
    after_ask = _state_bytes()
    assert set(after_ask) - set(before) <= {"approvals.json"}
    assert {k: v for k, v in after_ask.items() if k != "approvals.json"} == before
    operator_service._apply_restore_answer(op, t.RESTORE_CANCEL)
    out = _call("restore_backup", timestamp=snap_id)
    assert out.get("reason") == "restore_declined"
    # The decline + re-call write NOTHING at all.
    assert _state_bytes() == after_ask               # byte-identical


def test_restore_approval_is_bound_to_one_snapshot(tmp_path):
    op = _seed(tmp_path)
    snap_a = _call("backup_now")["backup"]["id"]
    snap_b = _call("backup_now")["backup"]["id"]
    assert snap_a != snap_b
    _call("restore_backup", timestamp=snap_a)
    operator_service._apply_restore_answer(op, t.RESTORE_PROCEED)
    # The approval signed snap_a — snap_b re-asks instead of riding it.
    out = _call("restore_backup", timestamp=snap_b)
    assert out.get("reason") == "restore_pending"


def test_unknown_timestamp_errors_with_the_available_list(tmp_path):
    _seed(tmp_path)
    known = _call("backup_now")["backup"]["id"]
    out = _call("restore_backup", timestamp="20200101-000000-000000")
    assert "error" in out and known in out["available"]


# --------------------------------------------------------------------------
# CSV export round-trip
# --------------------------------------------------------------------------

def test_csv_export_round_trips(tmp_path):
    _seed(tmp_path)
    out = _call("export_crm_csv")
    assert out["contacts"] == 1 and out["deals"] == 1

    with open(out["contacts_csv"], newline="", encoding="utf-8") as f:
        c_rows = list(csv.DictReader(f))
    stored_c = memory_service.list_contacts()[0]
    assert len(c_rows) == 1
    for field in ("id", "name", "company", "email", "written_by"):
        assert c_rows[0][field] == str(stored_c[field])

    with open(out["deals_csv"], newline="", encoding="utf-8") as f:
        d_rows = list(csv.DictReader(f))
    stored_d = pipeline_service.list_deals()[0]
    assert len(d_rows) == 1
    for field in ("id", "contact_id", "title", "stage", "value_usd"):
        assert d_rows[0][field] == str(stored_d[field])
    # Touches ride along as JSON and parse back to the stored structure.
    touches = json.loads(d_rows[0]["touches"])
    assert touches[0]["note"] == "Kickoff call went well."
    assert touches == json.loads(json.dumps(stored_d["touches"], sort_keys=True))