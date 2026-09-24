"""Owner Workspace job runs are visible on the PC (v7.5), renderer side.

The 2026-09-24 incident: a job run parked on a request_missing_info question
at 10:34 and nothing on the PC showed it. The backend half is pinned in
test_owner_jobs.py (notices, live events, waiting questions, awaiting_input).

Here:
  - desktop/scripts/check_job_notices.js (Node): a notice notifies ONCE,
    however often the window polls or the backend re-sends; a restarted
    backend starts a new stream.
  - check_settings_layout.js "Job visibility (real DOM)" (Chromium, pinned in
    test_settings_layout.py): the window's own poll, two notifications over
    four polls, the run pinned and opened live, the question answerable, the
    "Waiting on you" badge, and the Approvals page's "Waiting for your answer".
  - source pins below for the wiring a harness cannot click.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node is not available")
def test_a_job_notice_notifies_once():
    proc = subprocess.run([_NODE, str(_DESKTOP / "scripts" / "check_job_notices.js")],
                          cwd=str(_DESKTOP), capture_output=True, text=True, timeout=120)
    output = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, output[-4000:]
    assert "JOB NOTICES OK" in output, output[-4000:]


def test_the_window_polls_notices_and_opens_job_runs():
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    assert html.index('<script src="job_notices.js" defer></script>') < html.index('<script src="app.js" defer></script>')
    footer = html.split('class="rail-footer"', 1)[1]
    assert 'id="rail-waiting-btn"' in footer and ">Waiting on you<" in footer and 'id="rail-waiting-count"' in footer
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    controller = app_js.split("v7.5: OWNER WORKSPACE JOBS ARE VISIBLE ON THIS PC", 1)[1]
    assert "/owner-workspace/jobs/notices?" in controller and "RidianJobNotices.fresh(" in controller
    assert "setInterval(_jobsNoticesTick, 3000)" in controller
    # Claimed: opened live only when nothing else is going on in the chat pane.
    assert "_activeWorkspaceView === null && !operatorState.running && !operatorState.answerMode" in controller
    assert "_opHandleEvent(evt)" in controller, "a job run replays through the typed-run event handler"
    # Clicking a notification raises the window and opens the run.
    assert "window.ridian.raiseWindow()" in controller and "_jobsOpenFromNotice(notice)" in controller
    preload = (_DESKTOP / "preload.js").read_text(encoding="utf-8")
    assert "raiseWindow: () => ipcRenderer.send('window:raise')" in preload
    main_js = (_DESKTOP / "main.js").read_text(encoding="utf-8")
    assert "ipcMain.on('window:raise', () => { raiseMainWindow(); });" in main_js
    # The Operations list shows a live or parked job run whatever project is selected.
    rail = app_js.split("function _railRenderThreads(", 1)[1][:3000]
    assert "_jobsPinned(op)" in rail and "_jobsActiveRun" in rail
    # The Approvals page lists parked questions and opens their run.
    approvals = app_js.split("async function loadApprovals(", 1)[1].split("async function _answerApproval(", 1)[0]
    assert "Waiting for your answer" in approvals and "approval-open-run-btn" in approvals
    assert "_briefEsc(q.question" in approvals, "question text is escaped"
