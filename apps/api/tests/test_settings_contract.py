"""Settings API contract (v4.3) — no field may silently vanish.

Root cause of the 2026-07 "QuickBooks credentials do not persist" bug:
SettingsUpdate / SettingsView (main.py) were never extended for the v4.0
QuickBooks keys, so FastAPI/Pydantic silently DROPPED them on POST (never
saved) and omitted them on GET (never displayed) — while returning 200
"Settings saved." These tests pin the models against settings_service's
SETTABLE_KEYS so that class of bug is structurally impossible, and pin the
two behavioral rules the GUI depends on:

  1. an untouched (blank/omitted) secret field NEVER clears a stored secret;
  2. save -> reload round-trips both QuickBooks Client ID and Secret,
     including under the frozen (%APPDATA%) path layout.
"""
import importlib
import json
import sys

from fastapi.testclient import TestClient

from app.main import SettingsUpdate, SettingsView, app
from app.services import settings_service

client = TestClient(app)


def _patch_settings_file(monkeypatch, tmp_path, initial: dict | None = None):
    path = tmp_path / "local_settings.json"
    if initial is not None:
        path.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", path)
    return path


# --------------------------------------------------------------------------
# THE drift pin: every settable key must exist on both API models
# --------------------------------------------------------------------------

def test_settings_update_accepts_every_settable_key():
    """A key in SETTABLE_KEYS but missing from SettingsUpdate is silently
    dropped by Pydantic on POST — the exact bug that ate the QBO creds."""
    missing = [k for k in settings_service.SETTABLE_KEYS
               if k not in SettingsUpdate.model_fields]
    assert missing == [], f"SettingsUpdate silently drops: {missing}"


def test_settings_view_exposes_every_public_key_and_secret_flag():
    missing = [k for k in settings_service.PUBLIC_KEYS
               if k not in SettingsView.model_fields]
    assert missing == [], f"SettingsView silently omits: {missing}"
    flags = [f"{k}_configured" for k in sorted(settings_service.SECRET_KEYS)]
    missing_flags = [f for f in flags if f not in SettingsView.model_fields]
    assert missing_flags == [], f"SettingsView missing secret flags: {missing_flags}"


def test_secrets_never_appear_in_the_view_model():
    leaked = [k for k in settings_service.SECRET_KEYS
              if k in SettingsView.model_fields]
    assert leaked == [], f"SettingsView would leak secrets: {leaked}"


# --------------------------------------------------------------------------
# Untouched masked secret must NOT clear the stored secret
# --------------------------------------------------------------------------

def test_omitted_secret_field_keeps_stored_secret(monkeypatch, tmp_path):
    """The GUI omits untouched secret fields entirely. That must mean
    'leave unchanged', never 'erase'."""
    path = _patch_settings_file(monkeypatch, tmp_path, {
        "quickbooks_client_id": "OLD_ID",
        "quickbooks_client_secret": "OLD_SECRET",
    })
    r = client.post("/settings", json={"quickbooks_client_id": "NEW_ID"})
    assert r.status_code == 200
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["quickbooks_client_id"] == "NEW_ID"
    assert on_disk["quickbooks_client_secret"] == "OLD_SECRET"


def test_blank_secret_field_keeps_stored_secret(monkeypatch, tmp_path):
    """Even if a client ever POSTs the secret as an empty string (a masked
    field submitted verbatim), blank means keep — never erase."""
    path = _patch_settings_file(monkeypatch, tmp_path, {
        "quickbooks_client_secret": "OLD_SECRET",
        "smtp_password": "OLD_PW",
    })
    r = client.post("/settings", json={
        "quickbooks_client_secret": "",
        "smtp_password": "",
    })
    assert r.status_code == 200
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["quickbooks_client_secret"] == "OLD_SECRET"
    assert on_disk["smtp_password"] == "OLD_PW"


# --------------------------------------------------------------------------
# Save -> reload round-trip (the repro that failed)
# --------------------------------------------------------------------------

def test_qbo_credentials_round_trip_through_the_api(monkeypatch, tmp_path):
    """POST both creds, then GET (a fresh Settings open): the real Client ID
    comes back, the secret is reported saved (flag) but never echoed."""
    _patch_settings_file(monkeypatch, tmp_path)
    r = client.post("/settings", json={
        "quickbooks_client_id": "ABQBOCLIENTID123",
        "quickbooks_client_secret": "s3cr3tVALUE",
    })
    assert r.status_code == 200
    saved = r.json()
    assert saved["quickbooks_client_id"] == "ABQBOCLIENTID123"
    assert saved["quickbooks_client_secret_configured"] is True

    reopened = client.get("/settings").json()
    assert reopened["quickbooks_client_id"] == "ABQBOCLIENTID123"
    assert reopened["quickbooks_client_secret_configured"] is True
    # The secret VALUE never rides any response.
    assert "s3cr3tVALUE" not in json.dumps(saved) + json.dumps(reopened)

    # And the QuickBooks service (which reads the file directly) sees both.
    s = settings_service.load_settings()
    assert s["quickbooks_client_id"] == "ABQBOCLIENTID123"
    assert s["quickbooks_client_secret"] == "s3cr3tVALUE"


def test_frozen_mode_writes_and_reads_the_same_appdata_file(monkeypatch, tmp_path):
    """Frozen build: SETTINGS_PATH must resolve to
    %APPDATA%/Ridian Operator/local_settings.json for BOTH save and load
    (one module-level constant — this pins that it stays that way)."""
    with monkeypatch.context() as m:
        m.setattr(sys, "frozen", True, raising=False)
        m.setenv("APPDATA", str(tmp_path))
        importlib.reload(settings_service)
        expected = tmp_path / "Ridian Operator" / "local_settings.json"
        assert settings_service.SETTINGS_PATH == expected

        settings_service.save_settings({
            "quickbooks_client_id": "FROZEN_ID",
            "quickbooks_client_secret": "FROZEN_SECRET",
        })
        assert expected.exists()

        s = settings_service.load_settings()
        assert s["quickbooks_client_id"] == "FROZEN_ID"
        assert s["quickbooks_client_secret"] == "FROZEN_SECRET"

        # Untouched masked secret on a later save: still preserved.
        settings_service.save_settings({"quickbooks_client_id": "FROZEN_ID2"})
        s2 = settings_service.load_settings()
        assert s2["quickbooks_client_id"] == "FROZEN_ID2"
        assert s2["quickbooks_client_secret"] == "FROZEN_SECRET"
    # Monkeypatch context is closed (dev env restored) — re-resolve the
    # module-level path back to the dev location for the rest of the suite.
    importlib.reload(settings_service)
    assert settings_service.SETTINGS_PATH.name == "local_settings.json"
    assert "Ridian Operator" not in str(settings_service.SETTINGS_PATH)
