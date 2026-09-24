"""Settings row layout (v6.1) — no row's text may collide with another's.

The bug: rows and their status notes were SIBLING flex children of the
scrolling column, and .settings-row-note carried an explicit
`min-height: 1em`. An explicit min-height REPLACES a flex item's automatic
minimum content size, so the flex algorithm was free to compress a note to
one line's box; a note whose text wrapped to two lines (QuickBooks'
"Connected to sandbox (company 9341457602986271).") spilled its second
line onto the following row. The -4px top margin removed what little
clearance was left.

These pins are geometric, not textual: check_settings_layout.js renders
the REAL index.html in Chromium at five widths, fills every status line
with realistic long text, and measures bounding rectangles. It was
verified to FAIL on the pre-fix markup with exactly the reported
collision ("Connected to sandbox (company …)" over Drive's text, 20px of
vertical overlap at 1100/1000/940px) and to pass after.
"""
import json
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_HARNESS = _DESKTOP / "scripts" / "check_settings_layout.js"
_NPX = shutil.which("npx")
_ELECTRON = _DESKTOP / "node_modules" / "electron"


def _electron_available() -> bool:
    return sys.platform == "win32" and _NPX is not None and _ELECTRON.exists()


# --------------------------------------------------------------------------
# Structure: one rule for every row, enforced in the markup + CSS
# --------------------------------------------------------------------------

def test_every_settings_row_is_a_self_contained_block():
    """All service rows (plus Phone and Voice) own their controls AND their
    status line — QuickBooks included, with no special case."""
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    form = html.split('id="settings-form"', 1)[1].split("</form>", 1)[0]
    # + Owner snapshot (v1, under Advanced since v7.1), Text (SMS) (v7.0),
    # Owner Workspace (v7.1), Owner Workspace token (v7.2, under Advanced).
    # The count matches both the literal class and the Advanced blocks'
    # "settings-block settings-adv-block".
    assert form.count('class="settings-block') == 12
    # Every status note lives INSIDE a block, never as a bare sibling of it.
    for label in ("Anthropic", "OpenAI", "QuickBooks", "Text (SMS)", "Drive", "Gmail",
                  "Calendar", "Phone", "Owner Workspace", "Owner snapshot"):
        idx = form.find(f">{label}<")
        assert idx != -1, label
        block_start = form.rfind('class="settings-block', 0, idx)
        block_end = form.find('class="settings-block', idx)
        block = form[block_start:block_end if block_end != -1 else len(form)]
        assert "settings-row-note" in block, f"{label} has no status line in its block"


def test_the_shrink_and_negative_margin_traps_are_gone():
    css = (_DESKTOP / "renderer" / "styles.css").read_text(encoding="utf-8")
    rule = css.split(".settings-row-note {", 1)[1].split("}", 1)[0]
    # An explicit min-height would re-license flex compression.
    assert "min-height" not in rule
    # A negative margin reaches into a neighbour's space.
    assert "margin: -" not in rule and "margin-top: -" not in rule
    # Nothing in the settings column may be compressed below its content.
    assert ".settings-scroll > *" in css
    guard = css.split(".settings-scroll > * {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 auto" in guard


# --------------------------------------------------------------------------
# Geometry: measured in a real browser engine at real widths
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def harness_output() -> str:
    """Run the Chromium layout harness ONCE for every geometric pin."""
    if not _electron_available():
        pytest.skip("electron/npx not available for layout measurement")
    proc = subprocess.run(
        [_NPX, "electron", str(_HARNESS)], cwd=str(_DESKTOP),
        capture_output=True, text=True, timeout=600, env=_sandbox_env())
    output = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, output[-4000:]
    assert "LAYOUT OK" in output, output[-4000:]
    return output


def test_no_text_collides_at_any_width(harness_output):
    """Renders index.html at 1280/1100/1000/940/880 px and asserts no two
    painted elements from different rows share pixels."""
    for width in (1280, 1100, 1000, 940, 880):
        assert f"{width}px: 12 blocks" in harness_output, harness_output[-3000:]  # + Owner snapshot (v1), Text (SMS) (v7.0), Owner Workspace (v7.1), Owner Workspace token (v7.2)


def test_morning_brief_claims_the_chat_pane_cell(harness_output):
    """v6.1: the brief renders ALL its sections and takes the chat pane's
    grid cell at every width — the chat pane yields (display:none) instead
    of fighting it for the cell. Count: 9 since v6.9.8 (Ridian noticed)."""
    brief = harness_output.split("Morning brief view", 1)[1].split("View switching", 1)[0]
    for width in (1280, 1100, 1000, 940, 880):
        line = next(l for l in brief.splitlines() if l.startswith(f"{width}px:"))
        assert "9 sections" in line, line
        assert "main display=none" in line, line


def test_switching_views_never_leaves_two_owners_of_the_cell(harness_output):
    """THE reported bug: open Brief -> open Settings -> close Settings used
    to leave the Brief open AND restore the chat pane, auto-placing the
    composer into an implicit 240px column under the rail."""
    seq = harness_output.split("View switching", 1)[1].split("--- Settings", 1)[0]
    assert "afterBrief: views=[brief-view] main=none" in seq, seq
    # Opening Settings over the Brief closes the Brief — never both.
    assert "afterSettings: views=[settings-view] main=none" in seq, seq
    # Closing the last view restores a FULL-WIDTH chat pane in its own cell.
    after_close = next(l for l in seq.splitlines() if "afterClose" in l)
    assert "views=[]" in after_close, after_close
    assert "main=flex" in after_close, after_close
    width = int(after_close.split("main=flex", 1)[1].split("px", 1)[0].strip())
    assert width > 400, f"chat pane collapsed: {after_close}"


def test_nav_from_inside_views_lands_where_clicked(harness_output):
    """v6.9.5, THE reported bug: "New chat" inside Settings did nothing —
    the reset ran against a display:none chat pane. Measured in Chromium:
    New chat from Settings reaches the chat pane; a dirty settings form
    refuses navigation exactly once via confirm and holds the view; consent
    releases it; and every view opens from inside every other (20 ordered
    pairs through the rail buttons)."""
    nav = harness_output.split("Nav from inside views", 1)[1]
    assert "newChatFromSettings: views=[] main=flex" in nav, nav[:600]
    assert "dirtyRefused: views=[settings-view] confirmAsked=1" in nav, nav[:600]
    assert "dirtyConfirmed: views=[] main=flex" in nav, nav[:600]
    assert "crossNav: 20/20 pairs ok" in nav, nav[:600]


def test_chat_pane_navigation_routes_through_the_manager():
    """Structural: the three chat-pane navigations consult the view manager
    (and therefore the unsaved-settings guard) BEFORE touching the pane."""
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    for fn in ("_opNewChat", "loadOperatorRun", "_railSelectProject"):
        head = app_js.split(f"function {fn}(", 1)[1][:1200]
        assert "if (!_showWorkspaceView(null)) return;" in head, \
            f"{fn} does not route through the view manager"
    # The guard lives in the manager itself — the one unbypassable spot.
    mgr = app_js.split("function _showWorkspaceView(", 1)[1][:1600]
    assert "_settingsDirty" in mgr and "confirm(" in mgr
    # Dirty is armed by form edits and cleared by load and by save-success
    # (applySettingsToForm runs in both paths).
    assert "els.settingsForm.addEventListener('input', () => { _settingsDirty = true; })" in app_js
    assert app_js.count("_settingsDirty = false") >= 2


def test_all_four_views_route_through_one_manager():
    """Structural pin: no view may hide/show .operator-main on its own."""
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    assert "function _showWorkspaceView" in app_js
    assert "WORKSPACE_VIEW_IDS" in app_js
    for fn in ("openSettings", "openMorningBrief", "openApprovals", "openAuditLog",
               "closeSettings", "closeMorningBrief", "closeApprovals", "closeAuditLog"):
        body = app_js.split(f"function {fn}(", 1)[1].split("\n}", 1)[0]
        assert "_showWorkspaceView" in body, f"{fn} does not use the view manager"
        assert "operator-main" not in body, f"{fn} still touches .operator-main directly"


def _sandbox_env() -> dict:
    """Never let a layout probe touch real state: scratch data dir and a
    non-default port, per-child only."""
    import os
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="ridian_layout_"))
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.update({"RIDIAN_SANDBOX": "1", "RIDIAN_DATA_DIR": str(scratch),
                "RIDIAN_PORT": "8766", "APPDATA": str(scratch / "profile")})
    return env


def test_a_hostile_reply_renders_inert_in_the_real_renderer(harness_output):
    """v7.4: the harness renders a reply holding <script>, <img onerror>, a
    Markdown image and a javascript: link through the app's own
    _opRenderReceipt in Chromium: only fixed elements, nothing executed."""
    section = harness_output.split("--- Reply markdown (real DOM) ---", 1)[1]
    assert "markdown inert: no script, img or link element" in section
    assert "dangerous=0 pwned=0" in section
    assert "tags=li,p,strong,ul" in section


def test_a_job_run_is_visible_in_the_real_renderer(harness_output):
    """v7.5: the harness drives the window's own job-notice poll against a
    stub that re-sends every notice: two notifications over four polls, the
    run pinned and opened live, the question answerable, the waiting badge
    (on the Approvals nav item since v7.6), and the Approvals page's
    "Waiting for your answer"."""
    section = harness_output.split("--- Job visibility (real DOM) ---", 1)[1]
    assert "notifications=2 pinned=true echo=From Owner Workspace question=true armed=true waiting=true:1" in section
    assert "job runs visible: notified once each" in section




def test_an_expired_run_offers_send_again_in_the_real_renderer(harness_output):
    """v7.6: answering a parked run that can no longer continue, in Chromium:
    answer mode disarms, the card says it expired (no bare error), "Send
    again" posts the ORIGINAL command as a new run, the startup sweep's
    notice notifies once, and the waiting badge clears."""
    section = harness_output.split("--- Expired run (real DOM) ---", 1)[1].split("--- Sidebar ---", 1)[0]
    assert ("armed=true->false waiting=true->false card=true sendAgain=Send again bareError=no "
            "posted=1 notifications=1") in section, section
    assert "expired run: said so, Send again re-sent the command, notified once, badge cleared" in section


SIDEBAR_SIZES = ("1280px", "1100px", "1000px", "940px", "880px", "1024x700 content", "1024x700 window")


def test_the_sidebar_fits_without_scrolling_and_the_list_takes_the_rest(harness_output):
    """v7.6 (0.9.16) sidebar, measured in Chromium with 40 operations, six
    projects and every badge: at the five widths and at 1024x700 (as the
    content size and as the outer window size) the sidebar itself never
    scrolls, nothing in it is cut off, New operation / search / the four nav
    items / the list / the utility links / the Owner Workspace line are in
    that order, the list takes the remaining height (at least three rows)
    and scrolls on its own, and what needs attention is first. With the
    backend-down banner or the project filter open, it still never scrolls;
    the Operations nav item brings the chat pane back from a view."""
    section = harness_output.split("--- Sidebar ---", 1)[1]
    for size in SIDEBAR_SIZES:
        line = next(l for l in section.splitlines() if l.startswith(f"{size}:"))
        assert "scrolls=no" in line and "attention first=yes" in line and line.endswith("| ok"), line
        rows = int(line.split(" rows visible", 1)[0].rsplit(", ", 1)[1])
        assert rows >= 3, line
    assert "backend-down banner at 1024x700 window:" in section
    assert "scrolls=no, nav and footer visible=true" in section
    assert "project filter open at 1024x700 window: 7 project rows, hide-failed toggle=true, sidebar scrolls=no, footer visible=true" in section
    assert "nav: approvals current=rail-approvals-btn; Operations -> main=flex current=rail-operations-btn" in section


def test_the_sidebar_is_actions_nav_list_then_utilities():
    """Structural: the rail's order in the markup, and the list is the only
    part that scrolls (the top and the footer never shrink)."""
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    rail = html.split('<aside class="operator-rail"', 1)[1].split("</aside>", 1)[0]
    order = ['id="rail-new-chat"', 'id="rail-search"', 'class="rail-nav"', 'id="rail-operations-btn"',
             'id="rail-approvals-btn"', 'id="rail-obligations-btn"', 'id="rail-brief-btn"', 'class="rail-ops"',
             'id="rail-projects-panel"', 'id="rail-threads"', 'class="rail-footer"', 'id="operator-context-memory"',
             'id="rail-audit-btn"', 'id="rail-settings-btn"', 'id="rail-ows-status"']
    at = [rail.index(marker) for marker in order]
    assert at == sorted(at), [m for m, _ in sorted(zip(order, at), key=lambda x: x[1])]
    nav = rail.split('class="rail-nav"', 1)[1].split("</nav>", 1)[0]
    assert nav.count('class="rail-nav-btn') == 4
    assert 'id="rail-approvals-count"' in nav and 'id="rail-waiting-count"' in nav and 'id="rail-obligations-count"' in nav
    footer = rail.split('class="rail-footer"', 1)[1]
    assert footer.count('class="rail-utility-link"') == 3 and "<svg" not in footer
    css = (_DESKTOP / "renderer" / "styles.css").read_text(encoding="utf-8")
    ops = css.split(".rail-ops > .rail-threads {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 auto" in ops and "overflow-y: auto" in ops and "min-height: 0" in ops
    for rule in (".rail-top {", ".rail-footer {"):
        assert "flex-shrink: 0" in css.split(rule, 1)[1].split("}", 1)[0], rule
    assert ".rail-scroll" not in css and 'class="rail-scroll"' not in html
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    threads = app_js.split("function _railRenderThreads(", 1)[1].split("\nfunction ", 1)[0]
    assert "ops = [...ops.filter(_railNeedsAttention), ...ops.filter((op) => !_railNeedsAttention(op))];" in threads
    attention = app_js.split("function _railNeedsAttention(", 1)[1].split("\n}", 1)[0]
    assert "op.status === 'awaiting_input'" in attention and "_bgRuns[op.id] === 'attn'" in attention
    mgr = app_js.split("function _showWorkspaceView(", 1)[1][:1600]
    assert "_railMarkCurrent(id);" in mgr



def test_a_run_opened_right_after_claim_is_never_failed_while_alive(harness_output):
    """v7.7, in Chromium: a job run opened within 100 ms of its claim, before
    it has written operation_log.json — by the auto-open with the previous
    job run still in the pane (2026-09-24), its pinned rail row, its
    notification, and its folder alone — shows the live run from memory, and
    the pane (sampled on every DOM change and every 5 ms) never shows Failed
    while the run is alive. It then parks and waits for the answer; a typed
    background run's folder loads when written; a run that failed shows
    Failed, and an unknown run with no log shows "Could not load run"."""
    section = harness_output.split("--- Open right after claim (real DOM) ---", 1)[1].split("--- Sidebar ---", 1)[0]
    head = section.strip().splitlines()[0]
    assert head.endswith("| Failed while alive: 0"), head
    for opener, bound in (("auto", 100), ("row", 100), ("note", 100), ("fold", 100)):
        ms = int(head.split(f"{opener}=+", 1)[1].split()[0])
        assert 0 <= ms <= bound, head
        # "Running…" — matched without the ellipsis, which the harness output
        # decodes in the console code page.
        assert f"{opener}: shows op_claim_{opener} echo=From Owner Workspace step=true status=Running" in section
    assert "parked: question=true armed=true status=Waiting for your answer" in section
    typed = next(l for l in section.splitlines() if "typed background run:" in l)
    assert "Running" in typed and "-> Completed (folder loaded when written: true)" in typed, typed
    assert ("controls: failed run=Failed | unknown run with no log=Could not load run (failed dot=false) "
            "| dismissed run whose folder says waiting=Cancelled (armed=false)") in section
    assert "never Failed while alive" in section
