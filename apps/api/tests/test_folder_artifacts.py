"""Read-only folder-artifact view (v3.5) — the folder → runs → files walk.

collect_project_artifacts gathers every artifact from every run filed under
a folder: parent rolls up its sub-folders (same one-hop rule as the sidebar
chat filter), sub-folder isolates, unfiled runs are excluded, and deleted
local files are flagged "missing" instead of becoming dead links. The walk
never writes and never touches Drive — its only filesystem access is the
existence check.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import operation_log_service as ols

client = TestClient(app)


@pytest.fixture()
def store(monkeypatch):
    """In-memory state_store so tests never touch state/*.json."""
    data = {}
    monkeypatch.setattr(ols.state_store, "load_list", lambda name: list(data.get(name, [])))
    monkeypatch.setattr(ols.state_store, "save", lambda name, items: data.__setitem__(name, items))
    return data


def _op(op_id, project_id, artifacts, command="run"):
    return {"id": op_id, "command": command, "status": "completed",
            "completed_at": "2026-07-18T10:00:00+00:00",
            "artifact_folder": f"outputs/{op_id}",
            "project_id": project_id, "artifacts": artifacts}


def _file_art(tmp_path, name, exists=True):
    p = tmp_path / name
    if exists:
        p.write_text("x", encoding="utf-8")
    return {"name": name, "path": str(p), "kind": "markdown"}


# --------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------

def test_parent_rolls_up_subfolder_artifacts(store, tmp_path):
    parent = ols.create_project("Chamber work")
    sub = ols.create_project("Gulf Shores", parent["id"])
    ols.upsert_operation(_op("op_sub", sub["id"],
                             [_file_art(tmp_path, "research_packet.md")], "sub run"))
    ols.upsert_operation(_op("op_top", parent["id"],
                             [_file_art(tmp_path, "document.md")], "top run"))
    data = ols.collect_project_artifacts(parent["id"])
    assert data["chats"] == 2
    assert data["artifacts"] == 2
    by_id = {r["id"]: r for r in data["runs"]}
    assert set(by_id) == {"op_top", "op_sub"}
    # Direct filing shows no sub-folder tag; rolled-up runs name their origin.
    assert by_id["op_top"]["sub_folder_name"] == ""
    assert by_id["op_sub"]["sub_folder_name"] == "Gulf Shores"


def test_subfolder_isolates_to_itself(store, tmp_path):
    parent = ols.create_project("Chamber work")
    sub = ols.create_project("Gulf Shores", parent["id"])
    ols.upsert_operation(_op("op_sub", sub["id"], [_file_art(tmp_path, "a.md")]))
    ols.upsert_operation(_op("op_top", parent["id"], [_file_art(tmp_path, "b.md")]))
    data = ols.collect_project_artifacts(sub["id"])
    assert [r["id"] for r in data["runs"]] == ["op_sub"]
    assert data["chats"] == 1


def test_unfiled_runs_excluded(store, tmp_path):
    parent = ols.create_project("Chamber work")
    ols.upsert_operation(_op("op_unfiled", "", [_file_art(tmp_path, "a.md")]))
    ols.upsert_operation(_op("op_other", "proj_elsewhere", [_file_art(tmp_path, "b.md")]))
    data = ols.collect_project_artifacts(parent["id"])
    assert data["runs"] == []
    assert data["chats"] == 0
    assert data["artifacts"] == 0


def test_missing_files_flagged_not_dropped(store, tmp_path):
    parent = ols.create_project("Chamber work")
    arts = [_file_art(tmp_path, "kept.md", exists=True),
            _file_art(tmp_path, "deleted.md", exists=False),
            {"name": "gmail_draft_abc", "path": "https://mail.google.com/x",
             "kind": "gmail_draft"}]
    ols.upsert_operation(_op("op_1", parent["id"], arts))
    data = ols.collect_project_artifacts(parent["id"])
    flags = {a["name"]: a["missing"] for a in data["runs"][0]["artifacts"]}
    assert flags == {"kept.md": False, "deleted.md": True,
                     "gmail_draft_abc": False}   # http paths never "missing"


def test_artifactless_run_counts_as_chat_but_no_group(store, tmp_path):
    parent = ols.create_project("Chamber work")
    ols.upsert_operation(_op("op_chatty", parent["id"], [], "receipt-only run"))
    ols.upsert_operation(_op("op_files", parent["id"], [_file_art(tmp_path, "a.md")]))
    data = ols.collect_project_artifacts(parent["id"])
    assert data["chats"] == 2
    assert [r["id"] for r in data["runs"]] == ["op_files"]


def test_unknown_project_returns_none(store):
    assert ols.collect_project_artifacts("proj_nope") is None
    assert ols.collect_project_artifacts("") is None


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------

def test_endpoint_returns_view(store, tmp_path):
    parent = ols.create_project("Chamber work")
    ols.upsert_operation(_op("op_1", parent["id"], [_file_art(tmp_path, "a.md")]))
    res = client.get(f"/operator/projects/{parent['id']}/artifacts")
    assert res.status_code == 200
    body = res.json()
    assert body["project_id"] == parent["id"]
    assert body["artifacts"] == 1
    assert body["runs"][0]["artifacts"][0]["name"] == "a.md"


def test_endpoint_404_on_unknown_id(store):
    res = client.get("/operator/projects/proj_nope/artifacts")
    assert res.status_code == 404
