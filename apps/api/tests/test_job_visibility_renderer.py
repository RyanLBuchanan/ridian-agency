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
    waiting badge on Approvals, and the Approvals page's "Waiting for your
    answer". v7.6: "Expired run (real DOM)" — a run that can no longer
    continue says so, offers "Send again", notifies once, clears the badge.
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
    # v7.6: the waiting count rides the Approvals nav item (whose page lists
    # the questions), beside the approvals count.
    approvals = html.split('id="rail-approvals-btn"', 1)[1].split("</button>", 1)[0]
    assert 'id="rail-approvals-count"' in approvals and 'id="rail-waiting-count"' in approvals
    assert 'id="rail-waiting-btn"' not in html
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


def test_an_expired_run_is_said_and_offers_send_again():
    """v7.6: the renderer half of durable parked runs — the 'expired' event
    (an answer to a run that cannot continue), the rehydrated record of one,
    and the 'expired' notice all end answer mode and show the card with
    "Send again", which sends the ORIGINAL command as a new run."""
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    handler = app_js.split("function _opHandleEvent(", 1)[1].split("\n}", 1)[0]
    case = handler.split("case 'expired':", 1)[1].split("break;", 1)[0]
    for call in ("_opSetAnswerMode(null)", "_opRenderExpired(evt.data)", "refreshApprovalsBadge()", "_railThreadsFill()"):
        assert call in case, call
    card = app_js.split("function _opRenderExpired(", 1)[1].split("\nfunction ", 1)[0]
    assert "'Send again'" in card and "_opSendAgain(command)" in card and "textContent" in card and "innerHTML" not in card
    again = app_js.split("function _opSendAgain(", 1)[1].split("\n}", 1)[0]
    assert "_opSetAnswerMode(null)" in again and "OPERATOR.command.value = command" in again and "_opSubmit()" in again
    rehydrate = app_js.split("async function loadOperatorRun(", 1)[1].split("\nfunction ", 1)[0]
    assert "if (expired) _opRenderExpired(" in rehydrate
    assert "m !== expired.message" in rehydrate, "the reason is not shown twice"
    notice = app_js.split("function _jobsHandleNotice(", 1)[1].split("\nfunction ", 1)[0]
    assert "notice.kind === 'expired'" in notice and "_opRenderExpired(" in notice
    assert "no longer active" not in app_js


def test_desktop_cross_device_operations_refresh():
    """Phone-submitted operations appear without restarting the desktop."""
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    sync = app_js.split("// Cross-device operations sync:", 1)[1].split("async function _railThreadsFill(", 1)[0]
    assert "setInterval(_railSyncRecent, 5000)" in sync
    assert "window.addEventListener('focus', _railSyncRecent)" in sync
    assert "visibilitychange" in sync
    assert "cache: 'no-store'" in sync
    assert "Array.isArray(data.operations)" in sync
    assert "_railOps = data.operations" in sync
    assert "_railRenderThreads()" in sync
    assert "_railRenderProjects()" in sync
    assert "_railSyncBusy" in sync



def test_cross_device_operations_refresh_and_null_safe_parked_runs():
    app_js = (_DESKTOP / 'renderer' / 'app.js').read_text(encoding='utf-8')
    assert 'setInterval(_railSyncRecent, 5000)' in app_js
    assert "window.addEventListener('focus', _railSyncRecent)" in app_js
    assert 'Array.isArray(data.operations)' in app_js
    companion = (_DESKTOP.parent / 'apps' / 'api' / 'app' / 'static' / 'companion.html').read_text(encoding='utf-8')
    assert 'Array.isArray(data.operations)' in companion
    assert 'o && o.status === "awaiting_input"' in companion
    assert 'needs.length ? needs[needs.length - 1] : null' in companion


def test_operations_search_keyboard_shortcut():
    html = (_DESKTOP / 'renderer' / 'index.html').read_text(encoding='utf-8')
    app_js = (_DESKTOP / 'renderer' / 'app.js').read_text(encoding='utf-8')
    assert 'Search operations… (Ctrl+K)' in html
    assert "event.key.toLowerCase() === 'k'" in app_js
    assert '_railSearch.focus()' in app_js
    assert '_railSearch.select()' in app_js
    assert "event.key === 'Escape'" in app_js


def test_header_settings_shortcut():
    html = (_DESKTOP / 'renderer' / 'index.html').read_text(encoding='utf-8')
    js = (_DESKTOP / 'renderer' / 'app.js').read_text(encoding='utf-8')
    assert 'id="topbar-settings-btn"' in html
    assert 'aria-label="Open Settings"' in html
    assert "getElementById('topbar-settings-btn')?.addEventListener('click', openSettings)" in js


def test_operator_workspace_redesign_contract():
    html = (_DESKTOP / 'renderer' / 'index.html').read_text(encoding='utf-8')
    css = (_DESKTOP / 'renderer' / 'styles.css').read_text(encoding='utf-8')
    for section in ('Operator workspace', 'Projects and recent operations', 'Manage integrations, models, and application settings'):
        assert section in html
    for token in ('--color-accent: #2854d8', '.rail-nav-btn.is-current', '.settings-view', '.operator-form', '[data-theme="dark"]', 'prefers-reduced-motion'):
        assert token in css
    for essential_id in ('rail-operations-btn', 'rail-approvals-btn', 'rail-obligations-btn', 'rail-brief-btn', 'rail-settings-btn', 'operator-command'):
        assert 'id="' + essential_id + '"' in html


def test_standard_profile_menu_and_blue_theme():
    html = (_DESKTOP / 'renderer' / 'index.html').read_text(encoding='utf-8')
    css = (_DESKTOP / 'renderer' / 'styles.css').read_text(encoding='utf-8')
    js = (_DESKTOP / 'renderer' / 'app.js').read_text(encoding='utf-8')
    assert 'id="rail-account-menu"' in html
    assert 'id="operator-context-memory"' in html
    assert 'id="rail-audit-btn"' in html
    assert 'id="rail-settings-btn"' in html
    for choice in ('light', 'dark', 'system'):
        assert 'data-rail-theme="' + choice + '"' in html
    assert '--color-accent: #2854d8' in css
    assert '.rail-account-panel' in css
    assert 'function _syncAccountAppearance()' in js
    assert 'applyTheme(pref)' in js
