"""/operations/load artifact verification — manifest-driven, not a global list.

The old code expected sources_packet.md / script.md / audiobook.mp3 on EVERY
run (an audiobook-era leftover), producing false "missing" warnings for
document/deck/sheet operations. Verification must check exactly what the
operation's own log declared: local files exist on disk; external artifacts
(Slides/Drive/Sheets/Gmail) carry an http(s) link and are never "missing on
disk".
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture()
def outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path))
    return tmp_path


def _make_run(outputs, artifacts, files=(), name="20260101-000000_test-run"):
    folder = outputs / name
    folder.mkdir()
    log = {
        "id": "op_test1234",
        "command": "test",
        "status": "completed",
        "artifacts": artifacts,
    }
    (folder / "operation_log.json").write_text(json.dumps(log), encoding="utf-8")
    for fname in files:
        (folder / fname).write_text("content\n", encoding="utf-8")
    return folder


def _load(folder):
    res = client.get("/operations/load", params={"artifact_folder": str(folder)})
    assert res.status_code == 200, res.text
    return res.json()


def test_generic_document_run_has_no_false_missing(outputs):
    """Acceptance 1: document.md + operation_log.json only — no phantom
    sources_packet.md / script.md warnings."""
    folder = _make_run(
        outputs,
        artifacts=[
            {"name": "document.md", "path": "x", "kind": "markdown"},
            {"name": "operation_log.json", "path": "x", "kind": "json"},
        ],
        files=("document.md",),
    )
    assert _load(folder)["missing"] == []


def test_cloud_artifacts_are_not_missing_local_files(outputs):
    """Acceptance 2: Slides deck + Drive folder are cloud-only — verified by
    link, never reported as missing on disk."""
    folder = _make_run(
        outputs,
        artifacts=[
            {"name": "document.md", "path": "x", "kind": "markdown"},
            {"name": "Open Gulf Deck", "path": "https://docs.google.com/presentation/d/abc", "kind": "slides"},
            {"name": "drive_folder (3 files)", "path": "https://drive.google.com/drive/folders/xyz", "kind": "drive_folder"},
            {"name": "operation_log.json", "path": "x", "kind": "json"},
        ],
        files=("document.md",),
    )
    assert _load(folder)["missing"] == []


def test_external_artifact_without_link_warns(outputs):
    folder = _make_run(
        outputs,
        artifacts=[{"name": "Broken Deck", "path": "", "kind": "slides"}],
    )
    missing = _load(folder)["missing"]
    assert len(missing) == 1
    assert "Broken Deck" in missing[0] and "no link" in missing[0]


def test_audiobook_run_still_warns_when_declared_files_absent(outputs):
    """Acceptance 3: a run that DECLARED sources/script/audio still warns for
    the ones that are gone — the warning system stays real."""
    folder = _make_run(
        outputs,
        artifacts=[
            {"name": "sources_packet.md", "path": "x", "kind": "markdown"},
            {"name": "script.md", "path": "x", "kind": "markdown"},
            {"name": "audiobook.mp3", "path": "x", "kind": "audio"},
        ],
        files=("sources_packet.md",),   # script + audio missing
    )
    missing = _load(folder)["missing"]
    assert any(m.startswith("script.md") for m in missing)
    assert any(m.startswith("audiobook.mp3") for m in missing)
    assert not any(m.startswith("sources_packet.md") for m in missing)


def test_audiobook_run_with_all_files_has_no_warnings(outputs):
    folder = _make_run(
        outputs,
        artifacts=[
            {"name": "sources_packet.md", "path": "x", "kind": "markdown"},
            {"name": "script.md", "path": "x", "kind": "markdown"},
        ],
        files=("sources_packet.md", "script.md"),
    )
    assert _load(folder)["missing"] == []


def test_missing_operation_log_is_reported(outputs):
    folder = outputs / "20260101-000000_no-log"
    folder.mkdir()
    missing = _load(folder)["missing"]
    assert len(missing) == 1
    assert missing[0].startswith("operation_log.json")
