"""One-level sub-folders (v3.4) — parent_id, the depth cap, per-parent dedup.

A project may contain sub-folders; a sub-folder may contain only CHATS.
The depth cap is an invariant enforced at create_project — the only
folder-creation path — so depth <= 2 holds by construction everywhere else
(moving a chat can never nest folders). No migration: pre-v3.4 records
without parent_id read as top-level.
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


# --------------------------------------------------------------------------
# Creation + the depth cap
# --------------------------------------------------------------------------

def test_create_subfolder_under_project(store):
    parent = ols.create_project("Chamber work")
    sub = ols.create_project("Gulf Shores", parent["id"])
    assert sub["parent_id"] == parent["id"]
    assert sub["id"] != parent["id"]
    names = {p["name"]: p.get("parent_id", "") for p in ols.list_projects()}
    assert names == {"Chamber work": "", "Gulf Shores": parent["id"]}


def test_depth_cap_rejects_subfolder_under_subfolder(store):
    """THE invariant, stated adversarially: the only folder-creation path
    refuses a parent that itself has a parent — depth can never exceed 2."""
    parent = ols.create_project("Chamber work")
    sub = ols.create_project("Gulf Shores", parent["id"])
    with pytest.raises(ValueError, match="one level only"):
        ols.create_project("Deeper", sub["id"])


def test_depth_cap_enforced_through_the_api(store):
    """Same guarantee through the real endpoint: a 400 with the cap message,
    and nothing created."""
    parent = ols.create_project("Chamber work")
    sub = ols.create_project("Gulf Shores", parent["id"])
    res = client.post("/operator/projects",
                      json={"name": "Deeper", "parent_id": sub["id"]})
    assert res.status_code == 400
    assert "one level only" in res.json()["detail"]
    assert len(ols.list_projects()) == 2


def test_unknown_parent_rejected(store):
    with pytest.raises(ValueError, match="Unknown parent"):
        ols.create_project("Orphan", "proj_nope")


def test_create_subfolder_through_the_api(store):
    parent = ols.create_project("Chamber work")
    res = client.post("/operator/projects",
                      json={"name": "Orange Beach", "parent_id": parent["id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["parent_id"] == parent["id"]
    assert ols.project_exists(body["id"])


# --------------------------------------------------------------------------
# Name dedup — scoped to (parent, name)
# --------------------------------------------------------------------------

def test_same_name_under_different_parents_allowed(store):
    a = ols.create_project("Chamber work")
    b = ols.create_project("Open Gulf")
    sub_a = ols.create_project("Gulf Shores", a["id"])
    sub_b = ols.create_project("Gulf Shores", b["id"])
    assert sub_a["id"] != sub_b["id"]


def test_same_name_same_parent_dedupes(store):
    a = ols.create_project("Chamber work")
    s1 = ols.create_project("Gulf Shores", a["id"])
    s2 = ols.create_project("gulf shores", a["id"])
    assert s1["id"] == s2["id"]


def test_top_level_dedup_preserved(store):
    """Pre-v3.4 behavior intact: top-level names still dedup among
    top-levels, and a sub-folder does NOT collide with a top-level name."""
    a = ols.create_project("Chamber Work")
    b = ols.create_project("chamber work")
    assert a["id"] == b["id"]
    other = ols.create_project("Open Gulf")
    sub = ols.create_project("Chamber Work", other["id"])   # same name, nested
    assert sub["id"] != a["id"]


# --------------------------------------------------------------------------
# Legacy records + chat assignment
# --------------------------------------------------------------------------

def test_legacy_project_without_parent_id_is_top_level(store):
    """Pre-v3.4 stored projects lack the field entirely — they must accept
    sub-folders (they read as top-level) with no migration."""
    store["projects"] = [{"id": "proj_legacy0001", "name": "Old project",
                          "created_at": "2026-01-01T00:00:00+00:00"}]
    sub = ols.create_project("New sub", "proj_legacy0001")
    assert sub["parent_id"] == "proj_legacy0001"


def test_assign_chat_to_subfolder(store):
    ols.upsert_operation({"id": "op_1", "status": "completed"})
    parent = ols.create_project("Chamber work")
    sub = ols.create_project("Mobile", parent["id"])
    assert ols.project_exists(sub["id"])          # run_operation's intake check
    updated = ols.assign_operation_project("op_1", sub["id"])
    assert updated["project_id"] == sub["id"]
    # Moving back out to the parent (— "directly in the project") works too.
    assert ols.assign_operation_project("op_1", parent["id"])["project_id"] == parent["id"]


def test_assign_subfolder_through_the_api(store):
    ols.upsert_operation({"id": "op_9", "status": "completed"})
    parent = ols.create_project("Chamber work")
    sub = ols.create_project("Gulf Shores", parent["id"])
    res = client.post("/operations/op_9/project", json={"project_id": sub["id"]})
    assert res.status_code == 200
    assert ols.get_operation("op_9")["project_id"] == sub["id"]
