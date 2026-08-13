"""QuickBooks environment switch (v4.6) — sandbox by default, production
only by explicit choice.

Intuit's OAuth endpoints are shared between environments; what differs is
the API base (sandbox-quickbooks.api.intuit.com vs quickbooks.api.intuit.com)
and the QBO web-app links. Pins:

  1. unset/blank/garbage settings resolve to SANDBOX — the environment that
     cannot touch real books is the default, production is opt-in only;
  2. every API call (query + the single invoice write) targets the base of
     the ACTIVE environment, and invoice links point at the matching web app;
  3. tokens are stamped with their environment at save; using a token in the
     other environment refuses with a reconnect message (409), never opaque
     auth failures — legacy unstamped tokens count as production (what they
     were when the integration was hardcoded).
"""
import json
import time

import pytest

from app.services import quickbooks_service as qbs
from app.services import settings_service


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(qbs, "TOKEN_PATH", tmp_path / "qb_token.json")


def _seed_token(env="sandbox", stamped=True):
    tok = {"access_token": "at", "refresh_token": "rt", "realm_id": "555",
           "saved_at": int(time.time())}
    if stamped:
        tok["environment"] = env
    qbs.TOKEN_PATH.write_text(json.dumps(tok), encoding="utf-8")


class _FakeHttpx:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def _resp(self):
        fake = self

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return fake.payload
        return _R()

    def get(self, url, **kw):
        self.urls.append(url)
        return self._resp()

    def post(self, url, **kw):
        self.urls.append(url)
        return self._resp()


# --------------------------------------------------------------------------
# Default resolution: sandbox unless production is explicitly chosen
# --------------------------------------------------------------------------

def test_unset_blank_and_garbage_default_to_sandbox():
    assert qbs.get_environment() == "sandbox"                       # unset
    settings_service.save_settings({"quickbooks_environment": ""})
    assert qbs.get_environment() == "sandbox"                       # blank
    settings_service.save_settings({"quickbooks_environment": "PROD?!"})
    assert qbs.get_environment() == "sandbox"                       # garbage
    assert qbs._api_base(qbs.get_environment()) == qbs.SANDBOX_API_BASE
    assert qbs._app_url(qbs.get_environment()) == qbs.SANDBOX_APP_URL


def test_production_is_explicit_opt_in():
    settings_service.save_settings({"quickbooks_environment": "production"})
    assert qbs.get_environment() == "production"
    assert qbs._api_base(qbs.get_environment()) == qbs.PROD_API_BASE
    assert qbs._app_url(qbs.get_environment()) == qbs.PROD_APP_URL


# --------------------------------------------------------------------------
# API calls target the active environment
# --------------------------------------------------------------------------

def test_queries_hit_the_sandbox_host_in_sandbox(monkeypatch):
    _seed_token(env="sandbox")
    fake = _FakeHttpx({"QueryResponse": {"Customer": []}})
    monkeypatch.setattr(qbs, "httpx", fake)
    qbs.list_customers()
    assert fake.urls and fake.urls[0].startswith(
        "https://sandbox-quickbooks.api.intuit.com/v3/company/555/")


def test_queries_hit_the_production_host_in_production(monkeypatch):
    settings_service.save_settings({"quickbooks_environment": "production"})
    _seed_token(env="production")
    fake = _FakeHttpx({"QueryResponse": {"Customer": []}})
    monkeypatch.setattr(qbs, "httpx", fake)
    qbs.list_customers()
    assert fake.urls and fake.urls[0].startswith(
        "https://quickbooks.api.intuit.com/v3/company/555/")


def test_invoice_create_and_link_follow_the_environment(monkeypatch):
    _seed_token(env="sandbox")
    fake = _FakeHttpx({"Invoice": {"Id": "77", "DocNumber": "1001",
                                   "CustomerRef": {"name": "Coastal"},
                                   "TotalAmt": 500.0, "EmailStatus": "NotSet"}})
    monkeypatch.setattr(qbs, "httpx", fake)
    out = qbs.create_invoice("42", [{"description": "d", "amount": 500}])
    assert fake.urls[0].startswith(
        "https://sandbox-quickbooks.api.intuit.com/v3/company/555/invoice")
    assert out["link"].startswith("https://sandbox.qbo.intuit.com/app/invoice")


# --------------------------------------------------------------------------
# Token/environment mismatch refuses with a reconnect message
# --------------------------------------------------------------------------

def test_sandbox_token_refuses_in_production(monkeypatch):
    _seed_token(env="sandbox")
    settings_service.save_settings({"quickbooks_environment": "production"})
    with pytest.raises(qbs.QuickBooksError) as exc:
        qbs._access_token(qbs.get_environment())
    assert exc.value.status == 409
    assert "reconnect" in exc.value.detail.lower()
    assert qbs.get_status()["environment_mismatch"] is True


def test_legacy_unstamped_token_counts_as_production():
    _seed_token(stamped=False)
    # Sandbox setting (the default) + legacy production token -> mismatch.
    with pytest.raises(qbs.QuickBooksError):
        qbs._access_token(qbs.get_environment())
    # Explicit production -> the legacy token is exactly what it claims.
    settings_service.save_settings({"quickbooks_environment": "production"})
    access, realm = qbs._access_token(qbs.get_environment())
    assert (access, realm) == ("at", "555")
    assert qbs.get_status()["environment_mismatch"] is False


def test_saved_tokens_are_stamped_with_the_active_environment():
    qbs._save_token({"access_token": "at", "refresh_token": "rt", "realm_id": "9"})
    tok = qbs._load_token()          # v6.2: the file on disk is encrypted
    assert tok["environment"] == "sandbox"


def test_status_reports_the_active_environment():
    st = qbs.get_status()
    assert st["environment"] == "sandbox"
    settings_service.save_settings({"quickbooks_environment": "production"})
    assert qbs.get_status()["environment"] == "production"


def test_invoice_deep_link_shape_survives_auth_redirect(monkeypatch):
    """Pin the EXACT link shape per environment: txnId AND deeplinkcompanyid.
    Without deeplinkcompanyid, QBO's sign-in redirect drops the whole
    path+query (verified live: the sign-in Location carries no continuation)
    and the user lands on a BLANK new-invoice form; with it, the auth layer
    parses the company (surfacing as account_id_hint) and restores the link."""
    inv = {"Invoice": {"Id": "145", "DocNumber": "1042",
                       "CustomerRef": {"name": "Coastal"},
                       "TotalAmt": 500.0, "EmailStatus": "NotSet"}}
    _seed_token(env="sandbox")
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(inv))
    out = qbs.create_invoice("42", [{"description": "d", "amount": 500}])
    assert out["link"] == ("https://sandbox.qbo.intuit.com/app/invoice"
                           "?txnId=145&deeplinkcompanyid=555")

    settings_service.save_settings({"quickbooks_environment": "production"})
    _seed_token(env="production")
    monkeypatch.setattr(qbs, "httpx", _FakeHttpx(inv))
    out2 = qbs.create_invoice("42", [{"description": "d", "amount": 500}])
    assert out2["link"] == ("https://qbo.intuit.com/app/invoice"
                            "?txnId=145&deeplinkcompanyid=555")


def test_invoice_create_fault_detail_reaches_the_operator(monkeypatch):
    """A QuickBooks Fault must surface its Detail string to the user —
    'Invoice create failed (HTTP 400).' alone taught nobody anything."""
    _seed_token(env="sandbox")

    class _FaultHttpx:
        def post(self, url, **kw):
            class _R:
                status_code = 400

                @staticmethod
                def json():
                    return {"Fault": {"Error": [{
                        "Message": "Invalid Reference Id",
                        "Detail": "Invalid Reference Id : Customer is required for this transaction.",
                        "code": "2050"}], "type": "ValidationFault"}}
            return _R()

    monkeypatch.setattr(qbs, "httpx", _FaultHttpx())
    with pytest.raises(qbs.QuickBooksError) as exc:
        qbs.create_invoice("42", [{"description": "d", "amount": 500}])
    assert exc.value.status == 502
    assert "Customer is required for this transaction." in exc.value.detail
    assert "code 2050" in exc.value.detail


# --------------------------------------------------------------------------
# Adversarial-review pins: refresh inheritance + mid-operation flips
# --------------------------------------------------------------------------

class _FakeRefreshHttpx:
    def post(self, url, **kw):
        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"access_token": "at2", "refresh_token": "rt2"}
        return _R()


def test_refresh_inherits_the_tokens_environment_not_current_settings(monkeypatch):
    """A Settings flip during the refresh window must NEVER relabel the
    token lineage — the refreshed token keeps the environment it came from."""
    tok = {"access_token": "at", "refresh_token": "rt", "realm_id": "555",
           "saved_at": int(time.time()) - 4000,          # stale -> refresh
           "environment": "sandbox"}
    qbs.TOKEN_PATH.write_text(json.dumps(tok), encoding="utf-8")
    settings_service.save_settings({"quickbooks_client_id": "id",
                                    "quickbooks_client_secret": "sec"})
    monkeypatch.setattr(qbs, "httpx", _FakeRefreshHttpx())
    # Simulate the mid-refresh flip: settings say production the moment the
    # refreshed token is being SAVED. The guard ran against the caller's
    # snapshot ("sandbox"), and the inherited stamp must stay sandbox.
    monkeypatch.setattr(qbs, "get_environment", lambda: "production")
    access, realm = qbs._access_token("sandbox")
    assert access == "at2"
    saved = qbs._load_token()        # v6.2: the file on disk is encrypted
    assert saved["environment"] == "sandbox"             # inherited, not re-read
    assert saved["refresh_token"] == "rt2"


def test_save_token_never_overwrites_an_existing_stamp():
    qbs._save_token({"access_token": "a", "refresh_token": "r",
                     "realm_id": "1", "environment": "production"})
    saved = qbs._load_token()        # v6.2: the file on disk is encrypted
    assert saved["environment"] == "production"          # kept, not re-stamped


def test_guard_and_base_share_one_snapshot(monkeypatch):
    """_query resolves guard and URL from the SAME snapshot: with a sandbox
    token and a mid-call flip to production, the call either refuses (409)
    or goes to sandbox — never token-checked-sandbox-but-sent-to-production."""
    _seed_token(env="sandbox")
    fake = _FakeHttpx({"QueryResponse": {"Customer": []}})
    monkeypatch.setattr(qbs, "httpx", fake)
    envs = iter(["sandbox", "production"])   # first read: snapshot; any
    monkeypatch.setattr(qbs, "get_environment",
                        lambda: next(envs, "production"))
    qbs.list_customers()
    assert fake.urls[0].startswith("https://sandbox-quickbooks.api.intuit.com/")
