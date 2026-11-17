"""CI-only Windows installer smoke test. Never run against an Owner install.

Only fresh runner scratch data is used for the baseline-to-candidate upgrade
and legacy preference migration. No integrations, provider calls, credential
reads, or release publishing are part of this test.
The Windows runner has Python/Node installed; restricting the child's PATH is
useful evidence of bundling, not proof of behavior on a clean physical PC.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fingerprint(path):
    require(path.is_file(), f"Missing artifact: {path.name}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": digest}


def check_pe(path, x64=False):
    require(fingerprint(path)["bytes"] > 0, f"Empty executable: {path.name}")
    with path.open("rb") as stream:
        require(stream.read(2) == b"MZ", f"Invalid executable: {path.name}")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        require(stream.read(4) == b"PE\0\0", f"Invalid PE: {path.name}")
        machine = struct.unpack("<H", stream.read(2))[0]
    if x64:
        require(machine == 0x8664, f"Not an x64 executable: {path.name}")


def tree_manifest(root):
    require(root.is_dir(), "Backend directory missing")
    return {p.relative_to(root).as_posix(): fingerprint(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def check_native_results(path):
    cases = ET.parse(path).findall(".//testcase")
    require(cases, "Native test report is empty")
    require(not any(c.find(tag) is not None for c in cases
                    for tag in ("failure", "error", "skipped")),
            "Native suite has failures, errors, or skips")
    names = {c.attrib["name"] for c in cases}
    required = {"test_protect_unprotect_round_trips",
                "test_corrupt_blob_fails_closed",
                "test_second_launch_quits_without_hotkey_and_first_is_raised"}
    require(required <= names, "Real DPAPI/Electron native tests were not collected")
    for case in ('first', 'legacy', 'existing', 'same', 'conflict', 'invalid', 'upgrade'):
        require(f"test_installer_preflight[{case}]" in names,
                f"Required preference preflight case missing: {case}")
    return {"status": "PASS", "passed": len(cases), "skipped": 0}


def app_window(pid):
    """Find a visible Electron window belonging to the process we launched."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found = []

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            cls, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 256)
            if cls.value.startswith("Chrome_WidgetWin") and title.value.startswith("Ridian"):
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def stop_tree(process):
    if process and process.poll() is None:
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                        "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=20, check=False)
        process.wait(timeout=10)


class ProcessJob:
    """Kill all assigned descendants on close, including an orphaned backend."""
    def __init__(self):
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        require(self.handle, "Could not create CI process cleanup job")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise RuntimeError("Could not configure CI process cleanup")

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            stop_tree(process)
            raise RuntimeError("Could not contain CI process tree")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def check_listener_owner(port, app_pid, backend_exe):
    """Attribute the listener to this installed backend and desktop process tree."""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # Only internally allocated integers are interpolated; no paths or user inputs.
    command = f"""
        $ErrorActionPreference = 'Stop'
        $connections = @(Get-NetTCPConnection -LocalPort {int(port)} -State Listen)
        if ($connections.Count -ne 1) {{ throw 'Expected one scratch listener' }}
        $ownerId = $connections[0].OwningProcess
        $chain = @()
        for ($i = 0; $i -lt 6 -and $ownerId -ne {int(app_pid)}; $i++) {{
            $item = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId"
            if (!$item) {{ throw 'Listener ancestry unavailable' }}
            $chain += @{{ pid = $item.ProcessId; path = $item.ExecutablePath }}
            $ownerId = $item.ParentProcessId
        }}
        if ($ownerId -ne {int(app_pid)}) {{ throw 'Listener is outside our desktop tree' }}
        ConvertTo-Json -InputObject $chain -Compress
    """
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=25, check=True)
    chain = json.loads(result.stdout)
    require(chain and Path(chain[0]["path"]).resolve() == backend_exe.resolve(),
            "Listener is not the installed backend executable")


def smoke_test(report):
    require(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true",
            "Installer validation is restricted to the disposable Windows Actions runner")
    package = json.loads((REPO / "desktop/package.json").read_text(encoding="utf-8"))
    installers = list((REPO / "desktop/dist").glob("*Setup*.exe"))
    require(len(installers) == 1, "Expected exactly one NSIS installer")
    installer = installers[0]
    check_pe(installer)
    report["installer"] = {"name": installer.name, **fingerprint(installer)}
    report["native_tests"] = check_native_results(REPO / "work/windows-validation/native-tests.xml")
    built = tree_manifest(REPO / "apps/api/dist/ridian-backend")
    require(built, "Frozen backend is empty")
    scratch = Path(tempfile.mkdtemp(prefix="ridian-ci-", dir=os.environ["RUNNER_TEMP"]))
    installed, data = scratch / "install", scratch / "data"
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC",
           "TEMP", "TMP", "USERPROFILE", "PUBLIC", "ProgramData") if key in os.environ}
    # Explicit allowlist: no GitHub token, provider keys, migration, or Python env.
    env.update({"PATH": os.pathsep.join([str(Path(os.environ["SystemRoot"]) / "System32"),
                                        os.environ["SystemRoot"]]),
                "APPDATA": str(scratch / "roaming"),
                "LOCALAPPDATA": str(scratch / "local"),
                "RIDIAN_SANDBOX": "1", "RIDIAN_DATA_DIR": str(data),
                "RIDIAN_USERDATA": str(scratch / "electron-profile"),
                "OUTPUTS_DIR": str(scratch / "outputs"),
                "RIDIAN_HOST": "127.0.0.1", "RIDIAN_HOTKEY": "Control+Alt+Shift+F20"})
    for folder in (data, scratch / "roaming", scratch / "local"):
        folder.mkdir()
    require(not any(shutil.which(name, path=env["PATH"])
                    for name in ("python", "python3", "node", "npm")),
            "Child PATH unexpectedly exposes Python or Node")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    require(port != 8000, "Refusing application port")
    env["RIDIAN_PORT"] = str(port)
    installer_process = app = None
    job = ProcessJob()
    try:
        baseline = list((REPO / "work/baseline/desktop/dist").glob("*Setup*.exe"))
        require(len(baseline) == 1, "Pinned pre-fix installer is required")
        report["baseline_installer"] = {"commit": "f286d5ebd1dac9ac57c7a3f966e5df57ed390c72",
                                          **fingerprint(baseline[0])}
        installer_process = subprocess.Popen(f'"{baseline[0]}" /S /D={installed}', env=env, cwd=scratch)
        job.assign(installer_process)
        require(installer_process.wait(timeout=120) == 0, "Baseline installation failed")
        check_pe(installed / "Ridian Operator.exe", x64=True)
        legacy = installed / "resources/backend/_internal/hidden_projects.json"
        preference_bytes = b'{"hidden_folders":["synthetic-hidden"],"pinned_folders":["synthetic-pinned"]}'
        legacy.write_bytes(preference_bytes)
        settings = data / "local_settings.json"
        memory = data / "state/contacts.json"
        settings.write_text(json.dumps({"operator_name": "Synthetic upgrade owner"}), encoding="utf-8")
        memory.parent.mkdir(parents=True)
        memory.write_text(json.dumps([{"id": "synthetic-contact", "name": "Synthetic Contact",
                                      "written_by": "save_memory", "source_op": "synthetic-upgrade"}]), encoding="utf-8")
        for name in ("synthetic-hidden", "synthetic-pinned"):
            (scratch / "outputs" / name).mkdir(parents=True)
        preserved = {p: p.read_bytes() for p in (settings, memory)}
        report["synthetic_upgrade"] = "NOT VERIFIED"
        # /D must be LAST; NSIS expects its directory argument without quotes.
        command = f'"{installer}" /S /D={installed}'
        installer_process = subprocess.Popen(command, env=env, cwd=scratch)
        job.assign(installer_process)
        require(installer_process.wait(timeout=120) == 0, "NSIS installer failed")
        preferences = data / "hidden_projects.json"
        require(preferences.read_bytes() == preference_bytes, "Installer did not migrate legacy preferences")
        for file, original in preserved.items():
            require(file.read_bytes() == original, "Installer changed synthetic settings or memory")
        require(not legacy.exists(), "Old bundle preference file survived replacement unexpectedly")
        exe = installed / "Ridian Operator.exe"
        backend = installed / "resources/backend"
        check_pe(exe, x64=True)
        check_pe(backend / "ridian-backend.exe", x64=True)
        actual = tree_manifest(backend)
        require(actual == built, "Installed backend differs from frozen build")
        for resource in ("app.asar", "sunrise-waves.ico"):
            require(fingerprint(installed / "resources" / resource) ==
                    fingerprint(REPO / "desktop/dist/win-unpacked/resources" / resource),
                    f"Installed resource differs: {resource}")
        for resource in ("python312.dll", "certifi/cacert.pem", "app/prompts/planner_prompt.txt",
                         "app/static/index.html", "docx/templates/default.docx",
                         "pptx/templates/default.pptx"):
            require(fingerprint(backend / "_internal" / resource)["bytes"] > 0,
                    f"Empty bundled resource: {resource}")
        require(any((backend / "_internal/googleapiclient/discovery_cache/documents").glob("*.json")),
                "Google discovery documents are missing")
        forbidden = {".env", "google_credentials.json", "google_token.json",
                     "quickbooks_token.json", "local_settings.json"}
        require(not any(p.name in forbidden for p in installed.rglob("*")),
                "Unexpected runtime configuration in installed package")
        report["backend_manifest"] = actual
        report["artifact_checks"] = "PASS"
        # Refuse to reuse any pre-existing listener, even one from an auto-launch.
        with socket.socket() as probe:
            require(probe.connect_ex(("127.0.0.1", port)) != 0, "Scratch port already occupied")
        app = subprocess.Popen([str(exe)], cwd=installed, env=env)
        job.assign(app)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 65
        ready = False
        while time.monotonic() < deadline:
            require(app.poll() is None, "Installed desktop exited before readiness")
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    health = json.load(response)
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.25)
                continue
            require(health.get("status") == "ok" and health.get("service") == "ridian-agency"
                    and health.get("sandbox") is True
                    and health.get("app_version") == package["version"]
                    and Path(health.get("data_dir", "")).resolve() == data.resolve(),
                    "Installed backend identity/version does not match")
            require(not health.get("anthropic_key_loaded") and not health.get("openai_key_loaded"),
                    "Unexpected provider configuration")
            if app_window(app.pid):
                ready = True
                break
            time.sleep(0.25)
        require(ready, "Installed app did not show its window with a healthy bundled backend")
        check_listener_owner(port, app.pid, backend / "ridian-backend.exe")
        report["backend_process_ownership"] = "PASS"
        with opener.open(f"http://127.0.0.1:{port}/projects/recent", timeout=5) as response:
            recent = json.load(response)["projects"]
        require(not any(p["name"] == "synthetic-hidden" for p in recent), "Hidden preference not applied")
        require(any(p["name"] == "synthetic-pinned" and p["pinned"] for p in recent),
                "Pinned preference not applied")
        with opener.open(f"http://127.0.0.1:{port}/projects/hidden", timeout=5) as response:
            hidden = json.load(response)["projects"]
        require(any(p["name"] == "synthetic-hidden" for p in hidden), "Hidden preference missing")
        report["installed_app_smoke"] = "PASS"
        report["child_path_without_python_node"] = "PASS"
        # Close our own window; verify the desktop's normal drain/cleanup path.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        window = app_window(app.pid)
        require(window, "Installed app window disappeared before shutdown")
        require(user32.PostMessageW(window, 0x0010, 0, 0), "Could not close app window")
        require(app.wait(timeout=40) == 0, "Installed desktop did not exit cleanly")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.25)
        else:
            raise RuntimeError("Bundled backend still listening after desktop exit")
        report["owned_backend_shutdown"] = "PASS"
        for file, original in preserved.items():
            require(file.read_bytes() == original, "Startup/shutdown changed synthetic settings or memory")
        require(preferences.read_bytes() == preference_bytes, "Startup/shutdown lost migrated preferences")
        require(fingerprint(installer) == {k: report["installer"][k] for k in ("bytes", "sha256")},
                "Installer hash changed during validation")
        report["installer_sha256_rechecked"] = "PASS"
        report["synthetic_upgrade"] = "PASS"
        report["settings_memory_preservation"] = "PASS"
        report["hidden_pinned_preservation"] = "PASS"
    finally:
        try:
            stop_tree(app)
            stop_tree(installer_process)
        finally:
            job.close()
            # Scratch contains only synthetic CI state, never uploaded.
            shutil.rmtree(scratch, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-native-results", type=Path)
    args = parser.parse_args()
    if args.check_native_results:
        print(json.dumps(check_native_results(args.check_native_results)))
        return
    evidence = REPO / "work/windows-validation"
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"commit": os.environ.get("GITHUB_SHA"), "status": "FAIL",
              "limitations": ["Physical tower and clean-PC testing NOT RUN",
                              "Synthetic upgrade only; physical Owner data and downgrade behavior NOT RUN"]}
    try:
        smoke_test(report)
        report["status"] = "PASS"
    finally:
        (evidence / "installer-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        installer = report.get("installer", {})
        if installer:
            (evidence / "SHA256SUMS.txt").write_text(
                f'{installer["sha256"]}  {installer["name"]}\n', encoding="utf-8")
        summary = (f'Windows installer validation: **{report["status"]}**\n\n'
                   f'Commit: `{report["commit"]}`\n\n'
                   f'Installer bytes: {installer.get("bytes", "NOT BUILT")}\n\n'
                   f'SHA-256: `{installer.get("sha256", "NOT BUILT")}`\n\n'
                   f'Native tests: {json.dumps(report.get("native_tests", "NOT VERIFIED"))}\n\n'
                   + "\n".join(report["limitations"]) + "\n")
        (evidence / "summary.md").write_text(summary, encoding="utf-8")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(summary)


if __name__ == "__main__":
    main()
