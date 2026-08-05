"""Pins for three 2026-07-27 batch items:

1. Self-description grounding — the planner prompt carries verified APP UI
   FACTS (the app once fabricated a "Settings -> Integrations" path) and an
   explicit no-fabrication instruction for uncovered UI questions.
2. Taskbar icon — sunrise-waves.ico stays a proper multi-resolution icon
   (256/48/32/16, classic BMP frames; all-PNG sub-256 frames were half of
   the blank-taskbar bug) and electron-builder keeps using it for the exe,
   the installer, and as a real extraResources file.
3. QuickBooks minorversion — pinned at 75 (Intuit ignores <75 since
   2025-08-01) and actually sent on every QBO API call.
"""
import json
import struct
import time
from pathlib import Path

from app.services import quickbooks_service as qbs
from app.services import settings_service
from app.services.runtime_paths import resource_base

_REPO = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------
# 1. Planner self-description grounding
# --------------------------------------------------------------------------

def _planner_prompt() -> str:
    return (resource_base() / "app" / "prompts" / "planner_prompt.txt").read_text(
        encoding="utf-8")


def test_planner_prompt_carries_verified_ui_facts():
    text = _planner_prompt()
    assert "APP UI FACTS" in text
    # v4.9 reality: Settings is a full-page view, single entry bottom-left.
    assert "FULL-PAGE view" in text
    assert "BOTTOM-LEFT" in text
    # v5.0 Phase 2: Drive + Gmail are first-class rows sharing one sign-in;
    # the inline point-of-failure Connect stays too.
    assert "six rows" in text
    assert "Drive (Test + Connect/Disconnect)" in text
    assert "Gmail (Test + Connect/Disconnect)" in text
    assert "ONE Google sign-in covers Drive" in text
    assert "Connect Google Drive" in text          # inline error-row fix
    # SMTP lives under Advanced now.
    assert "SMTP" in text and "Advanced" in text
    # The fabricated paths are explicitly denied:
    assert 'NO "Integrations" page' in text
    assert 'NO "Connections" page' in text
    # QuickBooks row facts, including the environment dropdown:
    assert "QuickBooks" in text and "Environment" in text
    # And the facts must match the ACTUAL rendered UI (self-consistency):
    html = (_REPO / "desktop" / "renderer" / "index.html").read_text(encoding="utf-8")
    assert 'id="settings-view"' in html            # full-page view exists
    assert 'id="settings-modal"' not in html       # the modal stays gone
    assert 'name="smtp_host"' in html              # SMTP restored (Advanced)
    assert 'id="settings-drive-conn"' in html      # Drive row restored
    assert 'id="settings-gmail-conn"' in html      # Gmail row restored
    assert 'id="settings-test-drive"' in html and 'id="settings-test-gmail"' in html
    assert 'id="google-pill"' not in html          # persistent badge stays gone


def test_planner_prompt_forbids_inventing_ui():
    text = _planner_prompt()
    assert "Never invent UI" in text
    assert "not certain" in text


def test_planner_prompt_carries_operator_profile_from_settings(monkeypatch, tmp_path):
    """v4.8: operator_name/operator_email/company_name were saved by the UI
    and read by NOTHING while the prompt hardcoded 'Ryan Buchanan'. The
    profile now splices live from Settings and the file carries no name."""
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "s.json")
    settings_service.save_settings({
        "operator_name": "Test Operator",
        "operator_email": "op@example.test",
        "company_name": "Testco LLC",
    })
    from app.agents.planner_agent import build_planner_system
    system = build_planner_system()
    assert "Test Operator" in system
    assert "op@example.test" in system
    assert "Testco LLC" in system
    assert "Ryan Buchanan" not in system
    assert "Ryan" not in _planner_prompt()          # the FILE is identity-free
    # Unset name degrades honestly instead of guessing.
    settings_service.save_settings({"operator_name": ""})
    assert "name not set" in build_planner_system()


def test_run_context_merges_settings_identity_no_false_empty(monkeypatch, tmp_path):
    """v5.1 profile bug: Settings identity and Memory → Profile are two
    stores; the run context read only the memory one, so a filled Settings
    profile still produced 'Operator profile: EMPTY' in every receipt.
    Pin: identity present -> no EMPTY notice, identity in the block; both
    stores blank -> the EMPTY notice (honestly) remains."""
    from app.services import operator_service, state_store
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "s.json")
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")

    settings_service.save_settings({
        "operator_name": "Ryan Buchanan",
        "operator_email": "ryan@ridiantechnologies.com",
        "company_name": "Ridian Technologies",
    })
    ctx = operator_service._memory_context_snippet()
    assert "Operator profile: EMPTY" not in ctx
    assert "Ryan Buchanan" in ctx
    assert "Ridian Technologies" in ctx

    # Both stores genuinely blank -> the honest EMPTY notice stays.
    settings_service.save_settings({
        "operator_name": "", "operator_email": "", "company_name": ""})
    ctx2 = operator_service._memory_context_snippet()
    assert "Operator profile: EMPTY" in ctx2


# --------------------------------------------------------------------------
# 2. Taskbar icon asset + packaging config
# --------------------------------------------------------------------------

def test_sunrise_waves_ico_is_proper_multires_bmp():
    data = (_REPO / "desktop" / "assets" / "sunrise-waves.ico").read_bytes()
    _rsv, typ, count = struct.unpack("<HHH", data[:6])
    assert typ == 1 and count == 6   # v5.1: 64 + 128 added for Explorer scaling
    sizes = set()
    for i in range(count):
        off = 6 + i * 16
        w, h, _c, _r, _planes, bpp, size, dataoff = struct.unpack(
            "<BBBBHHII", data[off:off + 16])
        sizes.add(w or 256)
        assert bpp == 32
        # Classic DIB frames only — Windows guarantees PNG decoding solely
        # for the 256px frame, and all-PNG icos rendered a blank taskbar.
        assert data[dataoff:dataoff + 8] != b"\x89PNG\r\n\x1a\n"
    assert sizes == {256, 128, 64, 48, 32, 16}


def test_electron_builder_ships_the_ico_everywhere():
    pkg = json.loads((_REPO / "desktop" / "package.json").read_text(encoding="utf-8"))
    build = pkg["build"]
    assert build["win"]["icon"] == "assets/sunrise-waves.ico"
    extra = build["extraResources"]
    assert any(e.get("from") == "assets/sunrise-waves.ico"
               and e.get("to") == "sunrise-waves.ico" for e in extra), (
        "sunrise-waves.ico must ship as a REAL packaged resource — asar "
        "paths are invisible to win32 icon APIs")


# --------------------------------------------------------------------------
# 3. QuickBooks minorversion 75 on every call
# --------------------------------------------------------------------------

class _KwCapture:
    def __init__(self):
        self.calls = []

    def _resp(self, payload):
        class _R:
            status_code = 200

            @staticmethod
            def json():
                return payload
        return _R()

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._resp({"QueryResponse": {"Customer": []}})

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._resp({"Invoice": {"Id": "1", "CustomerRef": {"name": "x"}}})


def test_minorversion_75_sent_on_query_and_invoice(monkeypatch, tmp_path):
    assert qbs._MINOR_VERSION == "75"
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "s.json")
    monkeypatch.setattr(qbs, "TOKEN_PATH", tmp_path / "t.json")
    qbs.TOKEN_PATH.write_text(json.dumps({
        "access_token": "at", "refresh_token": "rt", "realm_id": "9",
        "saved_at": int(time.time()), "environment": "sandbox"}), encoding="utf-8")
    fake = _KwCapture()
    monkeypatch.setattr(qbs, "httpx", fake)

    qbs.list_customers()
    qbs.create_invoice("9", [{"description": "d", "amount": 1}])

    assert len(fake.calls) == 2
    for _method, _url, kw in fake.calls:
        assert kw["params"]["minorversion"] == "75"
