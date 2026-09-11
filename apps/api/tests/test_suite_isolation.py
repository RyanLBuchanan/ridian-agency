"""Suite-wide state isolation (2026-09-10) — the pin for conftest.py.

The leak this guards against: a plain `pytest` run rewrote the REAL dev
store (apps/api/state/approvals.json) because every writable path constant
is frozen at import from runtime_paths.data_dir(), and the state guard only
covers the credential writers. conftest arms RIDIAN_SANDBOX + RIDIAN_DATA_DIR
before any app module is imported; this file proves the constants agree.
"""
import os
from pathlib import Path

from app.services import (google_drive_service, push_service,
                          quickbooks_service, runtime_paths, settings_service,
                          state_store)
from app.services import artifact_service


def _scratch() -> Path:
    return Path(os.environ["RIDIAN_DATA_DIR"]).resolve()


def test_process_data_dir_is_the_scratch_dir_not_a_real_store():
    assert os.environ.get("RIDIAN_SANDBOX") == "1"
    resolved = runtime_paths.data_dir()
    assert resolved == _scratch()
    for real in runtime_paths._real_store_dirs():
        assert resolved != real.resolve()


def test_every_import_time_path_constant_lives_under_the_scratch_dir():
    scratch = _scratch()
    for constant in (state_store.STATE_DIR,
                     settings_service.SETTINGS_PATH,
                     quickbooks_service.TOKEN_PATH,
                     push_service.VAPID_PATH,
                     google_drive_service.CREDENTIALS_PATH,
                     google_drive_service.TOKEN_PATH):
        assert Path(constant).resolve().is_relative_to(scratch), constant


def test_outputs_dir_is_redirected_off_the_repo():
    assert artifact_service.outputs_dir().resolve().is_relative_to(_scratch())
