"""Two-phase OAuth connect (v4.3) — the desktop opens the browser, never the
hidden backend, and no failure is ever silent.

Root cause of the 2026-07 "Connect buttons do nothing" bug: the packaged
backend (PyInstaller --noconsole, spawned windowsHide) called
webbrowser.open() / run_local_server(open_browser=True), which return
WITHOUT launching a browser there; the return value was ignored, nothing
was logged, and the request then blocked (300s for QuickBooks, forever for
Google). These tests pin the replacement contract:

  1. begin_oauth returns the consent URL immediately and BINDS the loopback
     callback port before returning (nothing to race);
  2. the background exchange completes on callback and persists the token;
  3. every failure (no consent, failed exchange, missing config, busy flow)
     lands in flow_error / an HTTP error detail — never silence.
"""
import json
import socket
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import google_drive_service as gds
from app.services import quickbooks_service as qbs
from app.services import settings_service

client = TestClient(app)


def _port_is_held(port: int) -> bool:
    """True when something already holds the port. Probed by BINDING, never
    by connecting — a bare TCP connect would be swallowed as a phantom
    callback request (the exact stray-connection case the service's
    wait-for-real-callback loop defends against)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _callback(port: int, query: str) -> None:
    # v6.6: both listeners are plain HTTP again — Intuit only ever sees the
    # public https bounce URI, so the loopback leg needs no TLS.
    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{query}", timeout=5)


def _google_callback(port: int, query: str) -> None:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{query}", timeout=5)


def _state_of(begun: dict) -> str:
    """v6.2: state is random per flow — callbacks must echo the real one."""
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(begun["auth_url"]).query)["state"][0]


def _wait(pred, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(autouse=True)
def _reset_flow_state(monkeypatch, tmp_path):
    """Fresh flow state + isolated settings/token files for every test."""
    monkeypatch.setattr(qbs, "_flow_state", {"in_progress": False, "error": ""})
    monkeypatch.setattr(gds, "_flow_state", {"in_progress": False, "error": ""})
    monkeypatch.setattr(qbs, "TOKEN_PATH", tmp_path / "qb_token.json")
    monkeypatch.setattr(gds, "TOKEN_PATH", tmp_path / "google_token.json")
    monkeypatch.setattr(gds, "CREDENTIALS_PATH", tmp_path / "google_credentials.json")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "settings.json")


def _seed_qbo_creds():
    settings_service.save_settings({
        "quickbooks_client_id": "TESTCLIENTID",
        "quickbooks_client_secret": "TESTSECRET",
    })


class _FakeHttpx:
    """Stands in for httpx inside quickbooks_service. Never touches Intuit.
    ``get`` serves the post-connect companyinfo validation (v4.6)."""
    def __init__(self, status_code=200, get_status_code=200):
        self.status_code = status_code
        self.get_status_code = get_status_code
        self.calls = []

    def _resp(self, code, payload):
        class _Resp:
            status_code = code

            @staticmethod
            def json():
                return payload
        return _Resp()

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._resp(self.status_code,
                          {"access_token": "at", "refresh_token": "rt"})

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._resp(self.get_status_code, {"CompanyInfo": {}})


# --------------------------------------------------------------------------
# QuickBooks
# --------------------------------------------------------------------------

def test_qbo_begin_binds_port_and_callback_completes(monkeypatch):
    _seed_qbo_creds()
    fake = _FakeHttpx(200)
    monkeypatch.setattr(qbs, "httpx", fake)

    begun = qbs.begin_oauth()
    assert "appcenter.intuit.com" in begun["auth_url"]
    assert "TESTCLIENTID" in begun["auth_url"]
    assert f"localhost%3A{qbs.REDIRECT_PORT}" in begun["auth_url"]
    # The listener must be bound BEFORE the URL is handed out.
    assert _port_is_held(qbs.REDIRECT_PORT)
    assert qbs.get_status()["in_progress"] is True

    _callback(qbs.REDIRECT_PORT, f"code=abc123&realmId=999&state={_state_of(begun)}")
    assert _wait(lambda: qbs.get_status()["connected"], timeout=10)
    st = qbs.get_status()
    assert st["realm_id"] == "999"
    assert st["flow_error"] == ""
    assert st["in_progress"] is False
    tok = qbs._load_token()          # v6.2: the file on disk is encrypted
    assert tok["realm_id"] == "999" and tok["refresh_token"] == "rt"


def test_qbo_missing_creds_is_immediate_400():
    r = client.post("/quickbooks/connect")
    assert r.status_code == 400
    assert "not set" in r.json()["detail"]


def test_qbo_second_begin_while_pending_is_409_then_timeout_error_surfaces(monkeypatch):
    _seed_qbo_creds()
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(200))
    begun = qbs.begin_oauth()
    with pytest.raises(qbs.QuickBooksError) as exc:
        qbs.begin_oauth()
    assert exc.value.status == 409
    # Valid-state callback with NO code: flow ends with a SURFACED error,
    # not silence. (A wrong-state callback would be refused and keep
    # waiting — pinned in test_qbo_oauth_hardening.)
    _callback(qbs.REDIRECT_PORT, f"state={_state_of(begun)}")
    assert _wait(lambda: not qbs.get_status()["in_progress"], timeout=10)
    assert "did not complete" in qbs.get_status()["flow_error"]
    assert not qbs.get_status()["connected"]


def test_qbo_failed_token_exchange_is_surfaced_not_silent(monkeypatch):
    _seed_qbo_creds()
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(400))
    begun = qbs.begin_oauth()
    _callback(qbs.REDIRECT_PORT, f"code=abc&realmId=42&state={_state_of(begun)}")
    assert _wait(lambda: not qbs.get_status()["in_progress"], timeout=10)
    assert "token exchange failed" in qbs.get_status()["flow_error"].lower()
    assert not qbs.get_status()["connected"]


def test_qbo_endpoint_returns_auth_url_fast(monkeypatch):
    _seed_qbo_creds()
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(200))
    t0 = time.time()
    r = client.post("/quickbooks/connect")
    assert r.status_code == 200
    assert time.time() - t0 < 5           # no 300s block — phase 1 returns now
    assert r.json()["auth_url"].startswith("https://appcenter.intuit.com")
    _callback(qbs.REDIRECT_PORT, f"state={_state_of(r.json())}")   # free the port
    _wait(lambda: not qbs.get_status()["in_progress"], timeout=10)


def test_connect_stamps_token_with_consent_time_environment(monkeypatch):
    """Env flipped between begin_oauth and the browser callback: the token
    is stamped with the environment the consent STARTED in."""
    _seed_qbo_creds()
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(200))
    begun = qbs.begin_oauth()                            # env snapshot: sandbox
    settings_service.save_settings({"quickbooks_environment": "production"})
    _callback(qbs.REDIRECT_PORT, f"code=abc&realmId=7&state={_state_of(begun)}")
    assert _wait(lambda: not qbs.get_status()["in_progress"], timeout=10)
    tok = qbs._load_token()          # v6.2: the file on disk is encrypted
    assert tok["environment"] == "sandbox"


def test_wrong_environment_keys_fail_at_connect_with_plain_message(monkeypatch):
    """Post-connect validation: when the environment's API rejects the fresh
    token (Development keys in production or vice versa), the flow errors
    with an actionable message and NO token is saved."""
    _seed_qbo_creds()
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(200, get_status_code=401))
    begun = qbs.begin_oauth()
    _callback(qbs.REDIRECT_PORT, f"code=abc&realmId=7&state={_state_of(begun)}")
    assert _wait(lambda: not qbs.get_status()["in_progress"], timeout=10)
    st = qbs.get_status()
    assert not st["connected"]
    assert "Client ID/Secret" in st["flow_error"]
    assert "Development" in st["flow_error"]             # sandbox -> Dev keys
    assert not qbs.TOKEN_PATH.exists()


# --------------------------------------------------------------------------
# One-action connect (v4.5 UX): paste creds -> Connect, no separate Save
# --------------------------------------------------------------------------

def test_paste_then_connect_is_one_action(monkeypatch):
    """The renderer's Connect button persists the fields (the Save write
    path) and immediately begins OAuth. Pin the backend sequence: a
    settings POST followed at once by connect must succeed using exactly
    the just-posted values — never 'Client ID/Secret are not set'."""
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(200))
    r1 = client.post("/settings", json={
        "quickbooks_client_id": "PASTEDID",
        "quickbooks_client_secret": "PASTEDSECRET",
    })
    assert r1.status_code == 200
    r2 = client.post("/quickbooks/connect")
    assert r2.status_code == 200
    assert "PASTEDID" in r2.json()["auth_url"]
    _callback(qbs.REDIRECT_PORT, f"state={_state_of(r2.json())}")   # free the port
    _wait(lambda: not qbs.get_status()["in_progress"], timeout=10)


def test_connect_uses_freshly_saved_credentials_not_stale_ones(monkeypatch):
    """Replacing the Client ID and clicking Connect must use the NEW id
    (blank secret = keep the stored one, per the saved-secret contract)."""
    settings_service.save_settings({
        "quickbooks_client_id": "OLDID",
        "quickbooks_client_secret": "KEEPSECRET",
    })
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(200))
    r1 = client.post("/settings", json={"quickbooks_client_id": "NEWID"})
    assert r1.status_code == 200
    r2 = client.post("/quickbooks/connect")
    assert r2.status_code == 200
    assert "NEWID" in r2.json()["auth_url"]
    assert "OLDID" not in r2.json()["auth_url"]
    assert settings_service.load_settings()["quickbooks_client_secret"] == "KEEPSECRET"
    _callback(qbs.REDIRECT_PORT, f"state={_state_of(r2.json())}")   # free the port
    _wait(lambda: not qbs.get_status()["in_progress"], timeout=10)


# --------------------------------------------------------------------------
# Google Drive
# --------------------------------------------------------------------------

class _FakeFlow:
    def __init__(self):
        self.redirect_uri = None
        self.fetched = None

    def authorization_url(self, **kw):
        return (f"https://accounts.google.com/o/oauth2/auth?redirect_uri={self.redirect_uri}",
                "state123")

    def fetch_token(self, **kw):
        self.fetched = kw

    @property
    def credentials(self):
        class _C:
            @staticmethod
            def to_json():
                return '{"token": "fake", "refresh_token": "fake"}'
        return _C()


@pytest.fixture()
def fake_google_flow(monkeypatch):
    flow = _FakeFlow()
    monkeypatch.setattr(gds, "credentials_present", lambda: True)
    monkeypatch.setattr(
        gds.InstalledAppFlow, "from_client_secrets_file",
        staticmethod(lambda *a, **k: flow))
    return flow


def test_google_begin_binds_port_and_callback_completes(fake_google_flow):
    begun = gds.begin_oauth()
    assert begun["auth_url"].startswith("https://accounts.google.com")
    assert f"localhost:{gds.GOOGLE_REDIRECT_PORT}" in begun["auth_url"]
    assert _port_is_held(gds.GOOGLE_REDIRECT_PORT)

    _google_callback(gds.GOOGLE_REDIRECT_PORT, "code=xyz789&state=state123")
    assert _wait(lambda: not gds._flow_state["in_progress"])
    assert gds._flow_state["error"] == ""
    assert fake_google_flow.fetched == {"code": "xyz789"}
    assert "fake" in gds.TOKEN_PATH.read_text(encoding="utf-8")


def test_google_empty_callback_surfaces_error(fake_google_flow):
    gds.begin_oauth()
    _google_callback(gds.GOOGLE_REDIRECT_PORT, "error=access_denied")
    assert _wait(lambda: not gds._flow_state["in_progress"])
    assert "did not complete" in gds._flow_state["error"]


def test_google_missing_credentials_file_is_immediate_400():
    r = client.post("/google/connect")
    assert r.status_code == 400
    assert "google_credentials.json" in r.json()["detail"]


def test_status_endpoints_report_flow_fields():
    qs = client.get("/quickbooks/status").json()
    assert "in_progress" in qs and "flow_error" in qs
    gs = client.get("/google/status").json()
    assert "in_progress" in gs and "flow_error" in gs
