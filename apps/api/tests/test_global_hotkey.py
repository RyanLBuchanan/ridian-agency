"""Global hotkey registration (v6.0 Phase 7).

The requirement is that registration FAILS LOUDLY when the combination is
taken — never a silent no-op. desktop/hotkey.js holds that logic with no
Electron import (globalShortcut is injected), so these tests execute the
REAL module under Node with fake globalShortcut implementations.

Pins: success; taken (register returns false); taken (already registered);
Electron throwing; a "registered" that doesn't verify; malformed
accelerators; and — the packaging trap — that the module actually ships.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_HOTKEY_JS = _DESKTOP / "hotkey.js"
_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node is not on PATH")


def _run(js_body: str) -> dict:
    """Execute a snippet against the real hotkey.js and return its JSON."""
    script = (
        f"const {{ registerGlobalHotkey, applyHotkey, validateAccelerator, "
        f"DEFAULT_ACCELERATOR }} = require({json.dumps(str(_HOTKEY_JS))});\n"
        f"{js_body}\n"
    )
    proc = subprocess.run([_NODE, "-e", script], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_registers_successfully_and_binds_the_callback():
    out = _run("""
      const calls = [];
      const gs = {
        register: (acc, cb) => { calls.push(['register', acc]); cb(); return true; },
        isRegistered: (acc) => calls.some(c => c[0] === 'register' && c[1] === acc),
      };
      let fired = 0;
      const res = registerGlobalHotkey({ globalShortcut: gs, onTrigger: () => { fired++; } });
      console.log(JSON.stringify({ res, calls, fired }));
    """)
    assert out["res"]["ok"] is True
    assert out["res"]["accelerator"] == "CommandOrControl+Alt+R"
    assert out["calls"][0] == ["register", "CommandOrControl+Alt+R"]
    assert out["fired"] == 1                      # the trigger really is wired


def test_taken_when_register_returns_false():
    """Windows refuses the combination — the exact silent-no-op case."""
    out = _run("""
      const gs = { register: () => false, isRegistered: () => false };
      const res = registerGlobalHotkey({ globalShortcut: gs });
      console.log(JSON.stringify(res));
    """)
    assert out["ok"] is False and out["reason"] == "taken"
    assert "another application" in out["detail"].lower()
    assert "CommandOrControl+Alt+R" in out["detail"]


def test_taken_when_already_registered_never_overwrites():
    out = _run("""
      const calls = [];
      const gs = { register: (a) => { calls.push(a); return true; },
                   isRegistered: () => true };
      const res = registerGlobalHotkey({ globalShortcut: gs });
      console.log(JSON.stringify({ res, calls }));
    """)
    assert out["res"]["ok"] is False and out["res"]["reason"] == "taken"
    assert out["calls"] == []                     # never silently overwrote it


def test_electron_throwing_is_reported_not_swallowed():
    out = _run("""
      const gs = { register: () => { throw new Error('boom'); },
                   isRegistered: () => false };
      const res = registerGlobalHotkey({ globalShortcut: gs });
      console.log(JSON.stringify(res));
    """)
    assert out["ok"] is False and out["reason"] == "threw"
    assert "boom" in out["detail"]


def test_success_that_does_not_verify_is_reported():
    """register() said true but the shortcut isn't actually held — that is
    the silent failure wearing a success mask."""
    out = _run("""
      const gs = { register: () => true, isRegistered: () => false };
      const res = registerGlobalHotkey({ globalShortcut: gs });
      console.log(JSON.stringify(res));
    """)
    assert out["ok"] is False and out["reason"] == "unverified"


def test_malformed_accelerators_refuse_before_registering():
    out = _run("""
      const attempted = [];
      const gs = { register: (a) => { attempted.push(a); return true; },
                   isRegistered: () => false };
      const results = ['', 'R', 'Ctrl', 'Ctrl+Shift', 'Ctrl+Shift+R+T'].map(
        (acc) => registerGlobalHotkey({ globalShortcut: gs, accelerator: acc }));
      console.log(JSON.stringify({ results, attempted }));
    """)
    assert [r["reason"] for r in out["results"]] == ["invalid"] * 5
    assert out["attempted"] == []                 # nothing was ever attempted
    assert all(r["detail"] for r in out["results"])


def test_custom_accelerator_is_honored():
    """The fake mirrors reality: nothing is held until register() succeeds."""
    out = _run("""
      const held = new Set();
      const gs = { register: (a) => { held.add(a); return true; },
                   isRegistered: (a) => held.has(a) };
      const res = registerGlobalHotkey({ globalShortcut: gs, accelerator: 'Control+Alt+R' });
      console.log(JSON.stringify({ res, held: [...held] }));
    """)
    assert out["res"]["ok"] is True
    assert out["res"]["accelerator"] == "Control+Alt+R"
    assert out["held"] == ["Control+Alt+R"]


def test_missing_globalshortcut_is_reported():
    out = _run("console.log(JSON.stringify(registerGlobalHotkey({})));")
    assert out["ok"] is False and out["reason"] == "invalid"


# --------------------------------------------------------------------------
# v6.5: default is Ctrl+ALT+R (never the browser's hard-refresh), the
# binding is a SETTING applied without restart, and a failed registration
# surfaces as unregistered — never a fake success.
# --------------------------------------------------------------------------

def test_default_is_ctrl_alt_r_never_the_browser_hard_refresh():
    out = _run("console.log(JSON.stringify({ d: DEFAULT_ACCELERATOR }));")
    assert out["d"] == "CommandOrControl+Alt+R"
    assert "Shift+R" not in out["d"]              # the stolen combination


def test_apply_switches_bindings_and_releases_the_old_one():
    out = _run("""
      const held = new Set();
      const gs = {
        register: (a) => { held.add(a); return true; },
        unregister: (a) => held.delete(a),
        isRegistered: (a) => held.has(a),
      };
      registerGlobalHotkey({ globalShortcut: gs });   // boot: default active
      const res = applyHotkey({ globalShortcut: gs,
                                currentAccelerator: DEFAULT_ACCELERATOR,
                                accelerator: 'Control+Alt+Space' });
      console.log(JSON.stringify({ res, held: [...held] }));
    """)
    assert out["res"]["ok"] is True
    assert out["res"]["active"] == "Control+Alt+Space"
    assert out["held"] == ["Control+Alt+Space"]   # old binding released


def test_failed_apply_surfaces_unregistered_and_restores_the_old_binding():
    """THE item-3 pin: register() returning false must NEVER read as
    success — the result says unregistered + why, and the previous binding
    is given back so the user isn't silently left with nothing."""
    out = _run("""
      const held = new Set([DEFAULT_ACCELERATOR]);
      const gs = {
        register: (a) => { if (a === 'Control+Alt+T') return false;
                           held.add(a); return true; },
        unregister: (a) => held.delete(a),
        isRegistered: (a) => held.has(a),
      };
      const res = applyHotkey({ globalShortcut: gs,
                                currentAccelerator: DEFAULT_ACCELERATOR,
                                accelerator: 'Control+Alt+T' });
      console.log(JSON.stringify({ res, held: [...held] }));
    """)
    assert out["res"]["ok"] is False and out["res"]["reason"] == "taken"
    assert "another application" in out["res"]["detail"].lower()
    assert out["res"]["active"] == "CommandOrControl+Alt+R"   # restored
    assert out["held"] == ["CommandOrControl+Alt+R"]


def test_invalid_apply_keeps_the_current_binding_untouched():
    out = _run("""
      const held = new Set([DEFAULT_ACCELERATOR]);
      const gs = { register: (a) => { held.add(a); return true; },
                   unregister: (a) => held.delete(a),
                   isRegistered: (a) => held.has(a) };
      const res = applyHotkey({ globalShortcut: gs,
                                currentAccelerator: DEFAULT_ACCELERATOR,
                                accelerator: 'R' });
      console.log(JSON.stringify({ res, held: [...held] }));
    """)
    assert out["res"]["reason"] == "invalid"
    assert out["held"] == ["CommandOrControl+Alt+R"]  # never torn down


def test_blank_apply_falls_back_to_the_default():
    out = _run("""
      const held = new Set();
      const gs = { register: (a) => { held.add(a); return true; },
                   unregister: (a) => held.delete(a),
                   isRegistered: (a) => held.has(a) };
      const res = applyHotkey({ globalShortcut: gs, currentAccelerator: '',
                                accelerator: '   ' });
      console.log(JSON.stringify(res));
    """)
    assert out["ok"] is True and out["active"] == "CommandOrControl+Alt+R"


def test_hotkey_setting_round_trips_and_is_wired_everywhere(monkeypatch, tmp_path):
    """The binding is a first-class setting: settable, loadable, present in
    the Pydantic contract, the renderer form, and the main process."""
    from app.main import SettingsUpdate, SettingsView
    from app.services import settings_service
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "s.json")
    settings_service.save_settings({"operator_global_hotkey": "Control+Alt+Space"})
    assert settings_service.load_settings()["operator_global_hotkey"] == "Control+Alt+Space"
    assert "operator_global_hotkey" in SettingsUpdate.model_fields
    assert "operator_global_hotkey" in SettingsView.model_fields
    app_js = (_DESKTOP / "renderer" / "app.js").read_text(encoding="utf-8")
    assert "'operator_global_hotkey'" in app_js            # form field list
    assert "_applyHotkeyFromForm" in app_js                # applied on save
    main_js = (_DESKTOP / "main.js").read_text(encoding="utf-8")
    assert "operator_global_hotkey" in main_js             # startup source
    assert "hotkey:apply" in main_js                       # no-restart IPC


# --------------------------------------------------------------------------
# Wiring + packaging (the "it works in dev, breaks packaged" trap)
# --------------------------------------------------------------------------

def test_main_process_surfaces_failure_loudly():
    main_js = (_DESKTOP / "main.js").read_text(encoding="utf-8")
    assert "registerGlobalHotkey" in main_js
    assert "installGlobalHotkey" in main_js
    # A failed registration MUST reach the user, not just a log line.
    install = main_js.split("function installGlobalHotkey", 1)[1]
    assert "showErrorBox" in install.split("\n}", 1)[0]
    # And the combination is released on quit.
    assert "globalShortcut.unregisterAll()" in main_js


def test_new_files_are_packaged():
    """hotkey.js and the command bar preload must be in electron-builder's
    files list, or the packaged app crashes on require()."""
    pkg = json.loads((_DESKTOP / "package.json").read_text(encoding="utf-8"))
    files = pkg["build"]["files"]
    assert "hotkey.js" in files
    assert "preload-commandbar.js" in files
    # The bar's HTML/CSS/JS live under renderer/, already globbed.
    assert "renderer/**/*" in files
    for name in ("commandbar.html", "commandbar.css", "commandbar.js"):
        assert (_DESKTOP / "renderer" / name).exists(), name
