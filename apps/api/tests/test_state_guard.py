"""State-guard gate (v4.4) — test/diagnostic contexts can never write real
state. Refuse-before-the-artifact, same pattern as the other gates.

Born of a 2026-07-24 incident: a diagnostic probe POSTed to port 8000 while
the REAL backend owned it and overwrote the operator's real QuickBooks
credentials. The gate makes that class of accident structurally impossible:

  1. every settings/credential writer refuses (SandboxViolation) when a
     test context targets a real store — BEFORE any byte lands;
  2. RIDIAN_SANDBOX=1 processes must name a non-real RIDIAN_DATA_DIR and
     must not bind the real backend port 8000.

The refusal tests here aim at the REAL paths on purpose (that is the pin);
_preserve() snapshots and restores them so even a broken gate cannot leave
damage behind.
"""
import contextlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.services import google_drive_service as gds
from app.services import quickbooks_service as qbs
from app.services import settings_service
from app.services import runtime_paths
from app.services.runtime_paths import (
    SandboxViolation,
    guard_real_state_write,
    in_test_context,
    resolve_backend_port,
)


@contextlib.contextmanager
def _preserve(path: Path):
    """Snapshot a real file and restore it no matter what the test does."""
    existed = path.exists()
    data = path.read_bytes() if existed else None
    try:
        yield
    finally:
        if existed:
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
        elif path.exists():
            path.unlink()


def test_pytest_context_is_detected_deterministically():
    """pytest stamps PYTEST_CURRENT_TEST — the gate is armed in every test."""
    assert in_test_context()


# --------------------------------------------------------------------------
# THE pin: writers aimed at the real store refuse, and leave no artifact
# --------------------------------------------------------------------------

def test_settings_write_to_real_store_refuses_and_leaves_no_artifact():
    real = settings_service.SETTINGS_PATH          # unpatched = the real store
    before = real.read_bytes() if real.exists() else None
    with _preserve(real):
        with pytest.raises(SandboxViolation):
            settings_service.save_settings({"operator_name": "GUARD-MUST-REFUSE"})
        # Refuse-before-the-artifact: byte-identical (or still absent).
        after = real.read_bytes() if real.exists() else None
        assert after == before


def test_quickbooks_token_write_to_real_store_refuses():
    real = qbs.TOKEN_PATH                          # unpatched = the real store
    with _preserve(real):
        with pytest.raises(SandboxViolation):
            qbs._save_token({"refresh_token": "GUARD-MUST-REFUSE"})
        assert not real.exists() or b"GUARD-MUST-REFUSE" not in real.read_bytes()


def test_google_token_real_path_is_covered_by_the_guard():
    with pytest.raises(SandboxViolation):
        guard_real_state_write(gds.TOKEN_PATH)     # unpatched = the real store


def test_real_roaming_store_is_refused_even_with_appdata_redirected(monkeypatch, tmp_path):
    """An APPDATA env redirect must not disguise the real roaming store —
    the guard derives it from HOME, not from the (overridable) env."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    roaming = Path.home() / "AppData" / "Roaming" / runtime_paths.APP_DIR_NAME
    with pytest.raises(SandboxViolation):
        guard_real_state_write(roaming / "local_settings.json")


def test_sandboxed_writes_are_allowed(monkeypatch, tmp_path):
    """The gate blocks only REAL stores — properly sandboxed tests still work."""
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "s.json")
    saved = settings_service.save_settings({"operator_name": "sandboxed-ok"})
    assert saved["operator_name"] == "sandboxed-ok"
    monkeypatch.setattr(qbs, "TOKEN_PATH", tmp_path / "t.json")
    qbs._save_token({"refresh_token": "rt"})
    assert (tmp_path / "t.json").exists()


# --------------------------------------------------------------------------
# Sandboxed processes: non-real data dir REQUIRED, real port REFUSED
# --------------------------------------------------------------------------

def test_sandbox_mode_requires_a_data_dir(monkeypatch):
    monkeypatch.setenv("RIDIAN_SANDBOX", "1")
    monkeypatch.delenv("RIDIAN_DATA_DIR", raising=False)
    with pytest.raises(SandboxViolation):
        runtime_paths.data_dir()


def test_sandbox_data_dir_must_not_be_a_real_store(monkeypatch, tmp_path):
    monkeypatch.setenv("RIDIAN_SANDBOX", "1")
    real_dev = Path(runtime_paths.__file__).resolve().parent.parent.parent
    monkeypatch.setenv("RIDIAN_DATA_DIR", str(real_dev))
    with pytest.raises(SandboxViolation):
        runtime_paths.data_dir()
    # A scratch dir resolves fine (and is created).
    monkeypatch.setenv("RIDIAN_DATA_DIR", str(tmp_path / "sbx"))
    d = runtime_paths.data_dir()
    assert d == tmp_path / "sbx" and d.is_dir()


def test_sandbox_backend_refuses_the_real_port(monkeypatch):
    monkeypatch.setenv("RIDIAN_SANDBOX", "1")
    monkeypatch.delenv("RIDIAN_PORT", raising=False)
    with pytest.raises(SandboxViolation):
        resolve_backend_port()                     # default 8000 → refuse
    monkeypatch.setenv("RIDIAN_PORT", "8000")
    with pytest.raises(SandboxViolation):
        resolve_backend_port()                     # explicit 8000 → refuse
    monkeypatch.setenv("RIDIAN_PORT", "8765")
    assert resolve_backend_port() == 8765          # scratch port → allowed


def test_real_app_port_resolution_is_unchanged(monkeypatch):
    monkeypatch.delenv("RIDIAN_SANDBOX", raising=False)
    monkeypatch.delenv("RIDIAN_PORT", raising=False)
    assert resolve_backend_port() == 8000


def test_health_reports_backend_identity(monkeypatch, tmp_path):
    """v4.7 identity handshake: /health names the backend's state home and
    sandbox flag, so the desktop supervisor can refuse to adopt strangers.
    (The 2026-07-27 'wiped state' incidents were the real app ADOPTING an
    orphaned sandbox backend on port 8000 and rendering its empty state —
    no real file was ever written, which is why the write-gate never fired.)"""
    c = TestClient(app)
    j = c.get("/health").json()
    assert j["service"] == "ridian-agency"
    assert "data_dir" in j
    assert j["sandbox"] is False

    monkeypatch.setenv("RIDIAN_SANDBOX", "1")
    monkeypatch.setenv("RIDIAN_DATA_DIR", str(tmp_path / "sbx"))
    j2 = c.get("/health").json()
    assert j2["sandbox"] is True
    assert Path(j2["data_dir"]).resolve() == (tmp_path / "sbx").resolve()


def test_frozen_entrypoint_refuses_sandbox_misconfig_cleanly(monkeypatch):
    """The refusal must be SystemExit(2), never an uncaught exception — the
    PyInstaller --noconsole bootloader turns uncaught exceptions into a
    hidden modal error dialog and the process hangs instead of exiting."""
    import backend_main
    monkeypatch.setenv("RIDIAN_SANDBOX", "1")
    monkeypatch.delenv("RIDIAN_PORT", raising=False)
    with pytest.raises(SystemExit) as exc:
        backend_main.main()
    assert exc.value.code == 2
