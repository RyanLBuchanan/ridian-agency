"""Ridian's replies as sanitized Markdown in the operation thread (v7.4).

The morning_brief reply showed literal ** because the receipt was set as
plain text. It now renders through desktop/renderer/markdown.js: headings,
paragraphs, lists and bold, built from DOM nodes only; raw HTML, images and
links never become elements.

  - desktop/scripts/check_markdown.js runs the module in Node against a DOM
    that throws on innerHTML: parse output, inert hostile replies, and a
    source scan for any HTML-parsing API.
  - check_settings_layout.js ("Reply markdown (real DOM)") drives the app's
    own _opRenderReceipt in Chromium; test_settings_layout pins its result.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node is not available")
def test_the_markdown_module_parses_the_subset_and_renders_hostile_replies_inert():
    proc = subprocess.run([_NODE, str(_DESKTOP / "scripts" / "check_markdown.js")],
                          cwd=str(_DESKTOP), capture_output=True, text=True, timeout=120)
    output = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, output[-4000:]
    assert "MARKDOWN OK" in output, output[-4000:]


def test_the_receipt_renders_through_the_markdown_module():
    html = (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")
    md_at = html.index('<script src="markdown.js" defer></script>')
    app_at = html.index('<script src="app.js" defer></script>')
    assert md_at < app_at, "markdown.js loads first (defer keeps order)"
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    receipt = app_js.split("function _opRenderReceipt(", 1)[1].split("\n}\n", 1)[0]
    assert "window.RidianMarkdown.renderInto(body, text)" in receipt
    assert "innerHTML" not in receipt
    css = (_DESKTOP / "renderer" / "styles.css").read_text(encoding="utf-8")
    block = css.split(".operator-receipt-text {", 1)[1].split("}", 1)[0]
    assert "pre-wrap" not in block, "line breaks are <br> now, not pre-wrap"
    harness = (_DESKTOP / "scripts" / "check_settings_layout.js").read_text(encoding="utf-8")
    assert "Reply markdown (real DOM)" in harness and "_opRenderReceipt(text)" in harness
