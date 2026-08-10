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
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_HARNESS = _DESKTOP / "scripts" / "check_settings_layout.js"
_NPX = shutil.which("npx")
_ELECTRON = _DESKTOP / "node_modules" / "electron"


def _electron_available() -> bool:
    return _NPX is not None and _ELECTRON.exists()


# --------------------------------------------------------------------------
# Structure: one rule for every row, enforced in the markup + CSS
# --------------------------------------------------------------------------

def test_every_settings_row_is_a_self_contained_block():
    """All six service rows (plus Voice) own their controls AND their
    status line — QuickBooks included, with no special case."""
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    form = html.split('id="settings-form"', 1)[1].split("</form>", 1)[0]
    assert form.count('class="settings-block"') == 7
    # Every status note lives INSIDE a block, never as a bare sibling of it.
    for label in ("Anthropic", "OpenAI", "QuickBooks", "Drive", "Gmail",
                  "Calendar"):
        idx = form.find(f">{label}<")
        assert idx != -1, label
        block_start = form.rfind('class="settings-block"', 0, idx)
        block_end = form.find('class="settings-block"', idx)
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
        assert f"{width}px: 7 blocks" in harness_output, harness_output[-3000:]


def test_morning_brief_claims_the_chat_pane_cell(harness_output):
    """v6.1: the brief renders ALL seven sections and takes the chat pane's
    grid cell at every width — the chat pane yields (display:none) instead
    of fighting it for the cell."""
    brief = harness_output.split("Morning brief view", 1)[1].split("View switching", 1)[0]
    for width in (1280, 1100, 1000, 940, 880):
        line = next(l for l in brief.splitlines() if l.startswith(f"{width}px:"))
        assert "7 sections" in line, line
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
