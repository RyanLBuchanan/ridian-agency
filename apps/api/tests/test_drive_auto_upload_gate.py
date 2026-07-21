"""Drive auto-upload OFF is a CODE gate (v4.1) — the last prompt-only
setting, converted. Before this, the OFF state was enforced solely by the
upload-state line in the planner prompt; the auto_upload_drive tool never
read the setting. Now the tool refuses in code before any Drive write —
hard-refuse (quiet skip), not park, because OFF is a standing instruction
the operator already gave. The manual Upload button (a separate endpoint,
explicit human click) is deliberately NOT gated by this setting.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.services import settings_service
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


def _set_setting(monkeypatch, value):
    """Drive the REAL get_bool_setting chain — only the file read is faked."""
    monkeypatch.setattr(settings_service, "load_settings",
                        lambda: {"operator_auto_upload_drive": value})


def _bomb_uploads(monkeypatch):
    def _bomb(_folder):
        raise AssertionError("Drive write occurred despite auto-upload OFF")
    monkeypatch.setattr(t.google_drive_service, "upload_artifact_folder", _bomb)


def test_setting_off_refuses_in_code_no_drive_write(tmp_path, monkeypatch):
    _set_setting(monkeypatch, "false")
    _bomb_uploads(monkeypatch)
    op = _ctx(tmp_path, {"artifacts": [{"name": "document.md", "path": "x", "kind": "markdown"}]})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("auto_upload_drive").call({})))
    assert payload["reason"] == "auto_upload_disabled"
    assert payload["skipped"] is True
    step = next(s for s in op.record["steps"] if s["name"] == "drive_upload")
    assert step["status"] == "skipped"          # quiet skip, not a red failure
    assert "OFF in Settings" in step["detail"]


def test_setting_on_upload_proceeds(tmp_path, monkeypatch):
    _set_setting(monkeypatch, "true")
    calls = []

    def fake_upload(folder):
        calls.append(folder)
        return {"status": "success", "drive_folder_name": "run", "drive_path": "Ridian Operator / Operations / run",
                "drive_folder_url": "https://drive.google.com/x", "uploaded_files": ["document.md"]}

    monkeypatch.setattr(t.google_drive_service, "upload_artifact_folder", fake_upload)
    op = _ctx(tmp_path, {"artifacts": [{"name": "document.md", "path": "x", "kind": "markdown"}]})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("auto_upload_drive").call({})))
    assert payload["uploaded_files"] == ["document.md"]
    assert payload["drive_path"].startswith("Ridian Operator")
    assert len(calls) == 1


def test_setting_absent_defaults_on(tmp_path, monkeypatch):
    """Backward compatibility: no stored value = ON (the historical default)."""
    monkeypatch.setattr(settings_service, "load_settings", lambda: {})
    calls = []
    monkeypatch.setattr(t.google_drive_service, "upload_artifact_folder",
                        lambda f: calls.append(f) or {"status": "skipped", "reason": "no_files", "uploaded_files": []})
    op = _ctx(tmp_path, {"artifacts": [{"name": "document.md", "path": "x", "kind": "markdown"}]})
    set_current_operator(op)
    asyncio.run(_tool("auto_upload_drive").call({}))
    assert len(calls) == 1


def test_off_gate_precedes_local_only_skip(tmp_path, monkeypatch):
    """Gate ordering: the Settings gate fires before the NotebookLM
    local-only skip, so OFF always reports its own honest reason."""
    _set_setting(monkeypatch, "false")
    _bomb_uploads(monkeypatch)
    op = _ctx(tmp_path, {"skip_drive_upload": True})
    set_current_operator(op)
    payload = json.loads(asyncio.run(_tool("auto_upload_drive").call({})))
    assert payload["reason"] == "auto_upload_disabled"


def test_gate_reads_the_real_setting_not_a_prompt(tmp_path, monkeypatch):
    """Introspection-style: flip ONLY the stored setting between two calls —
    behavior flips with it, proving enforcement is the code path reading
    settings, not any prompt text."""
    live = {"operator_auto_upload_drive": "false"}
    monkeypatch.setattr(settings_service, "load_settings", lambda: dict(live))
    calls = []
    monkeypatch.setattr(t.google_drive_service, "upload_artifact_folder",
                        lambda f: calls.append(f) or {"status": "success", "drive_folder_name": "r",
                                                      "drive_path": "p", "drive_folder_url": "u",
                                                      "uploaded_files": ["document.md"]})
    op = _ctx(tmp_path, {"artifacts": [{"name": "document.md", "path": "x", "kind": "markdown"}]})
    set_current_operator(op)
    first = json.loads(asyncio.run(_tool("auto_upload_drive").call({})))
    assert first["reason"] == "auto_upload_disabled" and calls == []
    live["operator_auto_upload_drive"] = "true"
    second = json.loads(asyncio.run(_tool("auto_upload_drive").call({})))
    assert second["uploaded_files"] == ["document.md"] and len(calls) == 1
