"""Operator projects — lightweight grouping for runs (v2.8)."""
import pytest

from app.services import operation_log_service as ols
from app.services import operator_service as osvc


@pytest.fixture()
def store(monkeypatch):
    """In-memory state_store so tests never touch state/*.json."""
    data = {}
    monkeypatch.setattr(ols.state_store, "load_list", lambda name: list(data.get(name, [])))
    monkeypatch.setattr(ols.state_store, "save", lambda name, items: data.__setitem__(name, items))
    return data


def test_create_project_and_list(store):
    p = ols.create_project("Chamber work")
    assert p["id"].startswith("proj_")
    assert p["name"] == "Chamber work"
    assert p["created_at"]
    assert [x["name"] for x in ols.list_projects()] == ["Chamber work"]


def test_create_project_dedupes_case_insensitively(store):
    a = ols.create_project("Chamber Work")
    b = ols.create_project("chamber work")
    assert a["id"] == b["id"]
    assert len(ols.list_projects()) == 1


def test_create_project_rejects_empty(store):
    with pytest.raises(ValueError):
        ols.create_project("   ")


def test_create_project_caps_name_length(store):
    p = ols.create_project("x" * 200)
    assert len(p["name"]) <= ols._PROJECT_NAME_MAX


def test_project_exists(store):
    p = ols.create_project("Gulf Coast")
    assert ols.project_exists(p["id"]) is True
    assert ols.project_exists("proj_nope") is False
    assert ols.project_exists("") is False


def test_assign_operation_project(store):
    ols.upsert_operation({"id": "op_1", "status": "completed"})
    p = ols.create_project("Chamber")
    updated = ols.assign_operation_project("op_1", p["id"])
    assert updated["project_id"] == p["id"]
    assert ols.get_operation("op_1")["project_id"] == p["id"]
    # Clearing unfiles the run.
    ols.assign_operation_project("op_1", "")
    assert ols.get_operation("op_1")["project_id"] == ""


def test_assign_unknown_operation_returns_none(store):
    assert ols.assign_operation_project("op_missing", "proj_x") is None


def test_finalized_view_carries_project_id():
    record = ols.build_record(command="build a deck", intent="planner", artifact_folder="x")
    record["project_id"] = "proj_abc123"
    view = osvc._finalized_view(record)
    assert view["project_id"] == "proj_abc123"


def test_finalized_view_defaults_blank_project():
    record = ols.build_record(command="build a deck", intent="planner", artifact_folder="x")
    view = osvc._finalized_view(record)
    assert view["project_id"] == ""
