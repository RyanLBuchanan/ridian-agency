"""Rail cold start (v6.1) — loading is not empty, unknown is not zero.

The bug: on a fresh launch the renderer painted before its just-spawned
backend was listening; the first fetches threw, the catch swallowed them
with a comment claiming "the poll below retries" (no such poll existed),
and the markup's hardcoded "No chats yet." / "—" stayed on screen until a
run happened to refresh the rail. The rail was asserting emptiness the
backend never confirmed.

check_rail_coldstart.js boots the REAL renderer against a real HTTP
server on a scratch port and pins four behaviors: a PENDING backend
renders the loading state and never the empty state; the rail refreshes
when the held responses ARRIVE (no command needed); a CONFIRMED-zero
backend renders the honest empty state; and a backend with data renders
the rows on cold start. It was verified to FAIL on the pre-fix renderer
with exactly the reported symptoms.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_HARNESS = _DESKTOP / "scripts" / "check_rail_coldstart.js"
_NPX = shutil.which("npx")


def _electron_available() -> bool:
    return _NPX is not None and (_DESKTOP / "node_modules" / "electron").exists()


def _sandbox_env() -> dict:
    import os
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="ridian_rail_"))
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.update({"RIDIAN_SANDBOX": "1", "RIDIAN_DATA_DIR": str(scratch),
                "RIDIAN_PORT": "8767", "APPDATA": str(scratch / "profile")})
    return env


# --------------------------------------------------------------------------
# Structure: the markup and code can no longer assert emptiness up front
# --------------------------------------------------------------------------

def test_initial_markup_never_asserts_emptiness():
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    rail = html.split('id="rail-projects"', 1)[1].split("rail-footer", 1)[0]
    assert "No chats yet" not in rail
    assert "No projects yet" not in rail
    assert "rail-threads-loading" in rail


def test_renderers_gate_empty_states_on_backend_confirmation():
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    assert "RAIL_STATE" in app_js
    assert "_railFetchWithRetry" in app_js
    # Both renderers check readiness before any content/empty rendering.
    threads = app_js.split("function _railRenderThreads(", 1)[1]
    assert threads.find("RAIL_STATE.READY") != -1, \
        "_railRenderThreads never checks the loading state"
    assert threads.find("RAIL_STATE.READY") < threads.find("rail-threads-empty"), \
        "_railRenderThreads can reach an empty state before the backend answered"
    projects = app_js.split("function _railRenderProjects(", 1)[1]
    gate_at = projects.find("RAIL_STATE.READY")
    rows_at = projects.find("rail-thread-cmd")       # first real row built
    assert gate_at != -1, "_railRenderProjects never checks the loading state"
    assert rows_at != -1 and gate_at < rows_at, \
        "_railRenderProjects renders rows before the backend answered"
    # The old lie is gone: no swallowed-catch claiming a poll that never existed.
    assert "the poll below retries" not in app_js


# --------------------------------------------------------------------------
# Behavior: measured in the real renderer against a real (held) backend
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _electron_available(),
                    reason="electron/npx not available")
def test_pending_renders_loading_and_confirmed_empty_renders_empty():
    proc = subprocess.run(
        [_NPX, "electron", str(_HARNESS)], cwd=str(_DESKTOP),
        capture_output=True, text=True, timeout=600, env=_sandbox_env())
    output = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, output[-3000:]
    assert "RAIL OK" in output, output[-3000:]
    # The four scenario lines really ran, with the states the spec names.
    assert 'pending : threads="Loading' in output, output[-3000:]
    assert "arrival : rows=2" in output, output[-3000:]
    assert 'empty   : threads="No chats yet."' in output, output[-3000:]
    assert "data    : rows=2" in output, output[-3000:]
