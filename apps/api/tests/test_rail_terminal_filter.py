"""Sidebar terminal-state filter (v6.9.4) — one click hides the stragglers.

Structural pins on the renderer source: the toggle exists, it filters
EXACTLY failed+cancelled (a parked awaiting_input run is work to do and
completed runs are the point of a history list — neither may ever match),
the choice persists per machine, and the label counts what is actually
being hidden right now.
"""
import re
from pathlib import Path

_RENDERER = Path(__file__).resolve().parents[3] / "desktop" / "renderer"


def _js() -> str:
    return (_RENDERER / "app.js").read_text(encoding="utf-8")


def test_toggle_exists_in_the_rail():
    html = (_RENDERER / "index.html").read_text(encoding="utf-8")
    assert 'id="rail-hide-terminal"' in html
    assert 'id="rail-hide-terminal-label"' in html
    # It sits in the chat rail, before the Projects head.
    assert html.index('id="rail-hide-terminal"') < html.index("rail-projects-head")


def test_filter_hides_exactly_failed_and_cancelled():
    js = _js()
    body = js.split("function _railRenderThreads", 1)[1].split("\n}", 1)[0]
    # The terminal set is failed + cancelled and NOTHING else.
    assert "op.status === 'failed' || op.status === 'cancelled'" in body
    filt = body.split("if (_railHideTerminal", 1)[1].split("}", 1)[0]
    assert "op.status !== 'failed' && op.status !== 'cancelled'" in filt
    # awaiting_input / completed never appear in the filter expressions.
    for line in body.splitlines():
        if "_railHideTerminal" in line or "terminal" in line:
            assert "awaiting_input" not in line
            assert "'completed'" not in line


def test_choice_persists_and_label_counts():
    js = _js()
    assert "ridian.hideTerminalRuns" in js
    assert re.search(r"localStorage\.setItem\(_HIDE_TERMINAL_KEY", js)
    assert re.search(r"localStorage\.getItem\(_HIDE_TERMINAL_KEY", js)
    # Label shows the live hidden-count, computed AFTER search/project
    # narrowing so the number is what the toggle actually removes.
    body = js.split("function _railRenderThreads", 1)[1].split("\n}", 1)[0]
    assert "terminal.length" in body
    assert body.index("includes(q))") < body.index("terminal.length")


def test_toggle_is_harmless_without_storage_or_backend():
    """The layout harness loads index.html with no backend and storage can
    throw — the wiring must be guarded, like every other rail control."""
    js = _js()
    wiring = js.split("_HIDE_TERMINAL_KEY", 1)[1].split("function _railRenderThreads", 1)[0]
    assert "try {" in wiring and "catch" in wiring        # storage guarded
    assert "if (_railHideTerminalChk)" in wiring          # element guarded
