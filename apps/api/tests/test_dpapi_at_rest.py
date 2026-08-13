"""Encryption at rest via Windows DPAPI (v6.2, Intuit compliance item 6).

Pins:
  - protect/unprotect round-trips for the current Windows user;
  - the QuickBooks token file on disk is NOT parseable JSON;
  - a legacy plaintext token migrates to encrypted ON FIRST LOAD, with
    saved_at/environment preserved exactly (no restamping);
  - a blob that cannot be decrypted (another user's, or corrupt) fails
    CLOSED with an honest, actionable error — never a crash, and never a
    silent "not connected" lie from _access_token;
  - the QuickBooks client secret is a DPAPI blob inside
    local_settings.json — the plaintext never appears on disk — and
    round-trips through load_settings; a plaintext secret migrates on
    first load; an undecryptable secret reads blank (fail closed).
  - the support contact (item 4) is present in the Settings footer.
"""
import json
import sys
import time
from pathlib import Path

import pytest

from app.services import dpapi, settings_service
from app.services import quickbooks_service as qb

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="DPAPI is Windows-only")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(qb, "TOKEN_PATH", tmp_path / "quickbooks_token.json")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "s.json")
    qb._token_load_error[0] = ""


# --------------------------------------------------------------------------
# The primitive
# --------------------------------------------------------------------------

def test_protect_unprotect_round_trips():
    secret = "refresh-token-¤-unicode-9341457602986271".encode("utf-8")
    blob = dpapi.protect(secret)
    assert blob != secret and len(blob) > len(secret)   # actually transformed
    assert dpapi.unprotect(blob) == secret


def test_corrupt_blob_fails_closed():
    with pytest.raises(dpapi.DpapiError) as exc:
        dpapi.unprotect(b"\x01\x02not-a-dpapi-blob\x03")
    assert "different Windows account" in str(exc.value)


# --------------------------------------------------------------------------
# The token file
# --------------------------------------------------------------------------

def test_token_file_on_disk_is_not_parseable_json():
    qb._save_token({"access_token": "at", "refresh_token": "rt",
                    "realm_id": "9341", "environment": "sandbox"})
    raw = qb.TOKEN_PATH.read_bytes()
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
        json.loads(raw.decode("utf-8"))
    # No JSON structure or field names survive into the ciphertext.
    payload = raw[len(qb._DPAPI_MAGIC):]
    for needle in (b'"refresh_token"', b'"access_token"', b'"realm_id"'):
        assert needle not in payload
    # Round trip through the loader.
    tok = qb._load_token()
    assert tok["refresh_token"] == "rt" and tok["realm_id"] == "9341"


def test_plaintext_token_migrates_on_first_load_preserving_stamps():
    legacy = {"access_token": "at-old", "refresh_token": "rt-old",
              "realm_id": "77", "environment": "production",
              "saved_at": 1723200000}
    qb.TOKEN_PATH.write_text(json.dumps(legacy), encoding="utf-8")
    tok = qb._load_token()
    assert tok == legacy                              # values intact
    raw = qb.TOKEN_PATH.read_bytes()
    assert raw.startswith(qb._DPAPI_MAGIC)            # file now encrypted
    again = qb._load_token()
    assert again["saved_at"] == 1723200000            # NOT restamped
    assert again["environment"] == "production"


def test_undecryptable_token_fails_closed_with_honest_error():
    qb.TOKEN_PATH.write_bytes(qb._DPAPI_MAGIC + b"\x00garbage-from-another-user")
    assert qb._load_token() is None                   # no crash
    status = qb.get_status()                          # no crash here either
    assert status["connected"] is False
    with pytest.raises(qb.QuickBooksError) as exc:
        qb._access_token("sandbox")
    assert "cannot be decrypted" in exc.value.detail
    assert "Connect QuickBooks again" in exc.value.detail
    # Reconnecting (disconnect → new consent) clears the error state.
    qb.disconnect()
    assert qb._token_load_error[0] == ""


# --------------------------------------------------------------------------
# The client secret in local_settings.json
# --------------------------------------------------------------------------

def test_client_secret_is_encrypted_on_disk_and_round_trips():
    settings_service.save_settings({
        "quickbooks_client_id": "cid-public",
        "quickbooks_client_secret": "SEKRET-9x8y7z"})
    disk = settings_service.SETTINGS_PATH.read_text(encoding="utf-8")
    assert "SEKRET-9x8y7z" not in disk                # never plaintext on disk
    assert '"dpapi1:' in disk
    assert "cid-public" in disk                       # the ID stays public
    loaded = settings_service.load_settings()
    assert loaded["quickbooks_client_secret"] == "SEKRET-9x8y7z"
    # Keep-on-blank still works across the encryption boundary.
    settings_service.save_settings({"quickbooks_client_secret": ""})
    assert settings_service.load_settings()["quickbooks_client_secret"] == "SEKRET-9x8y7z"


def test_plaintext_secret_migrates_on_first_load():
    settings_service.SETTINGS_PATH.write_text(json.dumps({
        "quickbooks_client_id": "cid",
        "quickbooks_client_secret": "LEGACY-PLAIN"}), encoding="utf-8")
    loaded = settings_service.load_settings()
    assert loaded["quickbooks_client_secret"] == "LEGACY-PLAIN"
    disk = settings_service.SETTINGS_PATH.read_text(encoding="utf-8")
    assert "LEGACY-PLAIN" not in disk                 # migrated on first load
    assert '"dpapi1:' in disk
    assert settings_service.load_settings()["quickbooks_client_secret"] == "LEGACY-PLAIN"


def test_undecryptable_secret_reads_blank_not_crash():
    settings_service.SETTINGS_PATH.write_text(json.dumps({
        "quickbooks_client_secret": "dpapi1:bm90LWEtcmVhbC1ibG9i"}),  # not a real blob
        encoding="utf-8")
    loaded = settings_service.load_settings()
    assert loaded["quickbooks_client_secret"] == ""   # fail closed, honest blank


# --------------------------------------------------------------------------
# Item 4: in-app support contact
# --------------------------------------------------------------------------

def test_support_contact_is_in_the_settings_footer():
    html = (Path(__file__).resolve().parents[3] / "desktop" / "renderer"
            / "index.html").read_text(encoding="utf-8")
    foot = html.split('class="settings-foot"', 1)[1].split("</footer>", 1)[0]
    assert "mailto:ryan@ridiantechnologies.com" in foot
    assert 'https://ridiantechnologies.com" target="_blank"' in foot
