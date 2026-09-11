"""Pytest bootstrap: put apps/api on sys.path so `import app...` works
regardless of where pytest is invoked from, and sandbox the WHOLE suite so
no test can touch the real dev state store (apps/api/state, local_settings,
OAuth tokens) or the real outputs/ tree."""
import base64
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# --------------------------------------------------------------------------
# Suite-wide state isolation (2026-09-10).
#
# The leak: every writable path constant is computed at IMPORT time from
# runtime_paths.data_dir() — state_store.STATE_DIR, settings_service.
# SETTINGS_PATH, quickbooks TOKEN_PATH, push VAPID_PATH, google_drive
# CREDENTIALS_PATH / TOKEN_PATH, main._LOG_DIR — so a test that forgot to
# monkeypatch one of them wrote straight into apps/api/state/ (observed:
# approvals.json rewritten and a pre-write backup left behind by a plain
# `pytest` run). guard_real_state_write() covers only the credential
# writers, not state_store.save.
#
# The fix uses the codebase's own sanctioned override: RIDIAN_SANDBOX=1 +
# RIDIAN_DATA_DIR (see runtime_paths.data_dir — it REFUSES to resolve to a
# real store under the sandbox flag). It must be armed here, at conftest
# import, because pytest imports conftest before collecting any test module,
# and collection is what imports app.* and freezes those constants.
# OUTPUTS_DIR is set for the same reason: artifact_service resolves it at
# call time, but its dev default is <repo>/outputs, which is real.
#
# Tests that pin the UN-sandboxed dev contract, or aim the state guard at a
# real store on purpose, delenv / re-target these explicitly (see
# test_frozen_paths.py and test_state_guard.py). Subprocess harnesses set
# their own per-child RIDIAN_SANDBOX / RIDIAN_DATA_DIR / RIDIAN_PORT.
# --------------------------------------------------------------------------
_SUITE_DATA_DIR = Path(tempfile.mkdtemp(prefix="ridian-pytest-data-"))
os.environ["RIDIAN_SANDBOX"] = "1"
os.environ["RIDIAN_DATA_DIR"] = str(_SUITE_DATA_DIR)
os.environ["OUTPUTS_DIR"] = str(_SUITE_DATA_DIR / "outputs")


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 — pytest hook
    shutil.rmtree(_SUITE_DATA_DIR, ignore_errors=True)



@pytest.fixture(autouse=True)
def _portable_dpapi_for_non_windows(monkeypatch):
    """Let cross-platform tests exercise encrypted-store control flow.

    Production deliberately fails closed without Windows DPAPI. Tests that
    validate real DPAPI are already Windows-only; the remaining settings/QBO
    tests need a reversible, non-plaintext stand-in rather than failing before
    they reach the behavior under test.
    """
    if sys.platform == "win32":
        return
    from app.services import dpapi

    prefix = b"RIDIAN-TEST-DPAPI\0"

    def protect(data: bytes) -> bytes:
        return prefix + base64.urlsafe_b64encode(bytes(data)[::-1])

    def unprotect(blob: bytes) -> bytes:
        if not bytes(blob).startswith(prefix):
            raise dpapi.DpapiError("Test DPAPI blob is invalid.")
        try:
            return base64.urlsafe_b64decode(bytes(blob)[len(prefix):])[::-1]
        except Exception as exc:
            raise dpapi.DpapiError("Test DPAPI blob is invalid.") from exc

    monkeypatch.setattr(dpapi, "protect", protect)
    monkeypatch.setattr(dpapi, "unprotect", unprotect)
