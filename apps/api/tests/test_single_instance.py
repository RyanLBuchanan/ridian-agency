"""Single-instance lock (v6.7).

Clicking the pinned shortcut while Ridian is already running used to
launch a SECOND instance, which lost the global hotkey to the first and
blamed "another application." Pins, driven through TWO REAL Electron
launches of the actual main.js (sandboxed: scratch data dir + scratch
APPDATA profile, non-default port, an obscure test hotkey so the probe
never steals the operator's real combination):

  1. the second launch quits promptly WITHOUT attempting hotkey
     registration (its stdout carries the quit line and never the
     "[ridian] hotkey" line);
  2. the first instance receives the second-instance signal and raises
     its existing window;
  3. the first instance is still alive and still owns the hotkey after
     the second launch dies.

Plus source pins for the lock's placement guarantees.
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_NPX = shutil.which("npx")
_TEST_HOTKEY = "Control+Alt+Shift+F20"   # obscure: never collides with real use


def _electron_available() -> bool:
    return _NPX is not None and (_DESKTOP / "node_modules" / "electron").exists()


def _env(scratch: Path) -> dict:
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.update({
        "RIDIAN_SANDBOX": "1",
        "RIDIAN_DATA_DIR": str(scratch / "data"),
        "RIDIAN_PORT": "8767",
        "APPDATA": str(scratch / "profile"),
        # v6.8: APPDATA does NOT move Electron's userData on Windows (the OS
        # API ignores the env var), so it never isolated the instance lock —
        # with the real app running, the test instance correctly lost the
        # lock and "failed". RIDIAN_USERDATA is the real isolation channel.
        "RIDIAN_USERDATA": str(scratch / "profile" / "userdata"),
        "RIDIAN_HOTKEY": _TEST_HOTKEY,
    })
    return env


# --------------------------------------------------------------------------
# Source pins: the lock exists and sits BEFORE any work
# --------------------------------------------------------------------------

def test_lock_guards_everything_in_source():
    src = (_DESKTOP / "main.js").read_text(encoding="utf-8")
    assert "requestSingleInstanceLock" in src
    # The lock is taken before whenReady work, and ready-path work is gated.
    assert src.index("requestSingleInstanceLock") < src.index("app.whenReady")
    ready_body = src.split("app.whenReady().then", 1)[1]
    gate = ready_body.split("sandboxEnvPreflight", 1)[0]
    assert "gotTheLock" in gate, "whenReady must bail before ANY work"
    # The first instance raises its window on the signal.
    assert "second-instance" in src
    handler = src.split("app.on('second-instance'", 1)[1].split("});", 1)[0]
    assert "raiseMainWindow" in handler


# --------------------------------------------------------------------------
# Behavior: two real launches
# --------------------------------------------------------------------------

@pytest.mark.skipif(not _electron_available(),
                    reason="electron/npx not available")
def test_second_launch_quits_without_hotkey_and_first_is_raised():
    scratch = Path(tempfile.mkdtemp(prefix="ridian_single_"))
    (scratch / "data").mkdir()
    (scratch / "profile").mkdir()
    env = _env(scratch)

    first = subprocess.Popen(
        [_NPX, "electron", "."], cwd=str(_DESKTOP), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace")
    try:
        # Wait until the FIRST instance owns the hotkey (fully booted).
        first_out: list[str] = []
        deadline = time.time() + 40
        booted = False
        while time.time() < deadline:
            line = first.stdout.readline()
            if not line:
                if first.poll() is not None:
                    break
                continue
            first_out.append(line)
            if "[ridian] hotkey registered" in line:
                booted = True
                break
        assert booted, "first instance never registered the hotkey:\n" + "".join(first_out)
        assert first.poll() is None                  # still running

        # SECOND launch, same lock scope: must quit fast, register nothing.
        second = subprocess.run(
            [_NPX, "electron", "."], cwd=str(_DESKTOP), env=env,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        out2 = f"{second.stdout}\n{second.stderr}"
        assert "second launch detected" in out2, out2[-2000:]
        assert "[ridian] hotkey" not in out2, (
            "second launch attempted hotkey registration:\n" + out2[-2000:])
        assert second.returncode == 0

        # The FIRST instance got the signal and raised its window.
        raised = False
        deadline = time.time() + 15
        while time.time() < deadline and not raised:
            line = first.stdout.readline()
            if not line:
                if first.poll() is not None:
                    break
                continue
            first_out.append(line)
            if "second-instance signal" in line:
                raised = True
        assert raised, ("first instance never received second-instance:\n"
                        + "".join(first_out[-30:]))
        assert first.poll() is None                  # survived the whole dance
    finally:
        # PID-scoped: terminate exactly the process tree we started.
        subprocess.run(["taskkill", "/PID", str(first.pid), "/T", "/F"],
                       capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)