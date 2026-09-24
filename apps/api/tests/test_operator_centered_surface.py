"""Product-surface pins for the Operator-centered information architecture."""
from pathlib import Path

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"


def _html() -> str:
    return (_DESKTOP / "renderer" / "index.html").read_text(encoding="utf-8")


def test_operator_is_the_default_and_only_primary_work_surface():
    html = _html()
    welcome = html.split('id="view-welcome"', 1)[1].split(
        'id="obligations-view"', 1)[0]
    assert 'id="operator-form"' in welcome
    assert 'placeholder="Tell Ridian what to do' in welcome
    assert "Today's focus" not in welcome
    assert "Quick launch" not in welcome
    assert 'class="dashboard"' not in welcome
    assert '<summary>Legacy Tools</summary>' in welcome


def test_legacy_tools_are_one_disclosure_and_all_verticals_remain_reachable():
    html = _html()
    legacy = html.split('id="operator-templates"', 1)[1].split("</details>", 1)[0]
    assert html.count('id="operator-templates"') == 1
    for mode in ("business", "social", "agentic", "notebooklm"):
        assert f'data-legacy-mode="{mode}"' in legacy
        assert f'id="view-input-{mode}"' in html


def test_primary_history_uses_operations_language():
    html = _html()
    primary = html.split('id="view-welcome"', 1)[1].split(
        'id="operator-templates"', 1)[0]
    # v7.6: the Operations list is reached from the "Operations" nav item.
    ops_nav = primary.split('id="rail-operations-btn"', 1)[1].split("</button>", 1)[0]
    assert "<span>Operations</span>" in ops_nav
    assert 'placeholder="Search operations' in primary
    assert ">Recent runs<" not in primary
    assert ">+ New workflow<" not in primary


def test_legacy_launchers_keep_existing_routes_and_forms_untouched():
    js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    for route in ("/workflows/run", "/workflows/social-media/run",
                  "/workflows/agentic-advances/run", "/workflows/notebooklm/run"):
        assert route in js
    assert "document.querySelectorAll('[data-legacy-mode]')" in js
