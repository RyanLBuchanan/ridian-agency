"""QuickBooks OAuth + error-handling hardening (v6.2, Intuit compliance
items 1, 2, 3, 5).

Pins:
  1. state is cryptographically random PER FLOW, validated in the
     callback; a mismatch is refused (no code recorded, nothing
     exchanged); callbacks answer 302 to a parameter-free page;
  2. intuit_tid from response headers lands in BOTH the log record and
     the user-facing error on failures;
  3. the raw fault body never reaches any log record — only the parsed
     Fault detail does;
  5. a 401 triggers exactly one refresh + one retry; a second 401 clears
     the token and asks for reconnect; invalid_grant on refresh clears
     the token state specifically.
"""
import base64
import json
import threading
import time
import urllib.request

import pytest

from app.services import quickbooks_service as qb


@pytest.fixture(autouse=True)
def _isolated_token(monkeypatch, tmp_path):
    monkeypatch.setattr(qb, "TOKEN_PATH", tmp_path / "quickbooks_token.json")
    monkeypatch.setattr(qb, "load_settings", lambda: {
        "quickbooks_client_id": "cid-test",
        "quickbooks_client_secret": "secret-test",
        "quickbooks_environment": "sandbox"})
    qb._token_load_error[0] = ""
    with qb._flow_lock:
        qb._flow_state.update(in_progress=False, error="")


class _Resp:
    def __init__(self, status_code, body=None, tid=""):
        self.status_code = status_code
        self._body = body or {}
        self.headers = {"intuit_tid": tid} if tid else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def _seed_token(env="sandbox", fresh=True):
    tok = {"access_token": "at-1", "refresh_token": "rt-1", "realm_id": "9341",
           "environment": env,
           "saved_at": int(time.time()) - (0 if fresh else 4000)}
    qb.TOKEN_PATH.write_bytes(
        qb._DPAPI_MAGIC + qb.dpapi.protect(json.dumps(tok).encode()))
    return tok


# ==========================================================================
# 1. OAuth state validation + 302 (driven through the REAL loopback server)
# ==========================================================================

def _get(url):
    req = urllib.request.Request(url)
    # Never follow redirects — the tests assert on the redirect itself.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=5)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.request.HTTPError as exc:   # 302 arrives as an "error"
        return exc.code, dict(exc.headers), exc.read()


def _wait_flow_done(timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with qb._flow_lock:
            if not qb._flow_state["in_progress"]:
                return
        time.sleep(0.1)
    raise AssertionError("OAuth flow did not finish")


def test_state_is_random_per_flow_and_validated(monkeypatch):
    monkeypatch.setattr(qb, "REDIRECT_PORT", 18123)
    exchanged = []
    monkeypatch.setattr(qb.httpx, "post",
                        lambda *a, **kw: exchanged.append(1) or _Resp(500))

    begun1 = qb.begin_oauth()
    state1 = urllib.parse_qs = None  # noqa: F841 — clarity below
    from urllib.parse import parse_qs, urlparse
    q1 = parse_qs(urlparse(begun1["auth_url"]).query)
    state = q1["state"][0]
    assert len(state) >= 32                      # cryptographically sized
    assert state != "ridian"                     # the old constant is gone

    # Forged callback with the WRONG state: 302 to /rejected, code ignored,
    # NOTHING exchanged — and the pending flow keeps waiting.
    status, headers, body_ = _get(
        f"http://127.0.0.1:18123/callback?code=EVIL&realmId=1&state=forged")
    assert status == 302 and headers.get("Location") == "/rejected"
    assert exchanged == []
    with qb._flow_lock:
        assert qb._flow_state["in_progress"] is True   # not aborted by forgery

    # The REAL callback with the right state completes the flow.
    status, headers, body_ = _get(
        f"http://127.0.0.1:18123/callback?code=good&realmId=9341&state={state}")
    assert status == 302 and headers.get("Location") == "/connected"
    _wait_flow_done()
    assert exchanged, "valid state must reach the token exchange"

    # A SECOND flow gets a DIFFERENT random state.
    exchanged.clear()
    begun2 = qb.begin_oauth()
    q2 = parse_qs(urlparse(begun2["auth_url"]).query)
    state2 = q2["state"][0]
    assert state2 != state
    _get(f"http://127.0.0.1:18123/callback?code=x&realmId=1&state={state2}")
    _wait_flow_done()


def test_success_page_carries_no_oauth_params(monkeypatch):
    monkeypatch.setattr(qb, "REDIRECT_PORT", 18124)
    monkeypatch.setattr(qb.httpx, "post", lambda *a, **kw: _Resp(500))
    begun = qb.begin_oauth()
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(begun["auth_url"]).query)["state"][0]
    _get(f"http://127.0.0.1:18124/callback?code=abc123&realmId=77&state={state}")
    # The clean page the 302 points at: static HTML, no code/state/realmId.
    status, _h, body = _get("http://127.0.0.1:18124/connected")
    assert status == 200
    text = body.decode()
    for leaked in ("abc123", state, "realmId", "code="):
        assert leaked not in text
    _wait_flow_done()


def test_mismatch_only_flow_times_out_without_exchanging(monkeypatch):
    monkeypatch.setattr(qb, "REDIRECT_PORT", 18125)
    monkeypatch.setattr(qb, "_FLOW_TIMEOUT_S", 1)
    exchanged = []
    monkeypatch.setattr(qb.httpx, "post",
                        lambda *a, **kw: exchanged.append(1) or _Resp(500))
    qb.begin_oauth()
    _get("http://127.0.0.1:18125/callback?code=EVIL&realmId=1&state=wrong")
    _wait_flow_done(timeout=20)
    assert exchanged == []                        # refused: never exchanged
    with qb._flow_lock:
        assert "did not complete" in qb._flow_state["error"]


def test_redirect_uri_matches_the_registered_intuit_strings_exactly():
    """v6.6: Intuit PRODUCTION rejects localhost/IP URIs, so production
    consents use the registered public bounce URI EXACTLY —
    https://ridiantechnologies.com/qbo/callback (the site 302s it to the
    local listener). Intuit never sees the loopback leg, so the local
    listener is plain HTTP and sandbox uses the direct http://localhost
    URI. Both byte-pinned; the port stays fixed, never dynamic."""
    assert qb.PROD_REDIRECT_URI == "https://ridiantechnologies.com/qbo/callback"
    assert qb.LOCAL_REDIRECT_URI == "http://localhost:8123/callback"
    assert qb._redirect_uri("production") == "https://ridiantechnologies.com/qbo/callback"
    assert qb._redirect_uri("sandbox") == "http://localhost:8123/callback"
    assert qb.REDIRECT_PORT == 8123               # fixed, never dynamic


def test_production_consent_and_exchange_carry_the_same_public_uri(monkeypatch):
    """THE Intuit contract: the token exchange must repeat the authorize
    request's redirect_uri. In production BOTH must be the registered
    public URI — sending the localhost URI at exchange would fail every
    production consent."""
    monkeypatch.setattr(qb, "REDIRECT_PORT", 18127)
    monkeypatch.setattr(qb, "load_settings", lambda: {
        "quickbooks_client_id": "cid-test",
        "quickbooks_client_secret": "secret-test",
        "quickbooks_environment": "production"})
    exchanges = []

    def fake_post(url, headers=None, data=None, timeout=None):
        exchanges.append(dict(data or {}))
        return _Resp(500)   # fail the exchange — the captured data is the pin

    monkeypatch.setattr(qb.httpx, "post", fake_post)
    begun = qb.begin_oauth()
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(begun["auth_url"]).query)
    assert q["redirect_uri"][0] == "https://ridiantechnologies.com/qbo/callback"
    state = q["state"][0]
    # The browser still ARRIVES at the local plain-HTTP listener via the bounce.
    _get(f"http://127.0.0.1:18127/callback?code=abc&realmId=7&state={state}")
    _wait_flow_done()
    assert len(exchanges) == 1
    assert exchanges[0]["redirect_uri"] == "https://ridiantechnologies.com/qbo/callback"


def test_sandbox_consent_and_exchange_keep_the_localhost_uri(monkeypatch):
    monkeypatch.setattr(qb, "REDIRECT_PORT", 18128)
    exchanges = []

    def fake_post(url, headers=None, data=None, timeout=None):
        exchanges.append(dict(data or {}))
        return _Resp(500)

    monkeypatch.setattr(qb.httpx, "post", fake_post)
    begun = qb.begin_oauth()                       # fixture env: sandbox
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(begun["auth_url"]).query)
    assert q["redirect_uri"][0] == "http://localhost:8123/callback"
    _get(f"http://127.0.0.1:18128/callback?code=x&realmId=1&state={q['state'][0]}")
    _wait_flow_done()
    assert exchanges[0]["redirect_uri"] == "http://localhost:8123/callback"


# ==========================================================================
# 2 + 3. intuit_tid capture + redacted failure logging
# ==========================================================================

_RAW_SENTINEL = "RAW-BODY-MUST-NEVER-BE-LOGGED-1f2e3d"


def _fault_resp(status=400, tid="tid-abc-123"):
    return _Resp(status, body={
        "Fault": {"Error": [{"code": "6240",
                             "Message": "Duplicate Document Number Error",
                             "Detail": "Duplicate DocNumber 1042 exists."}]},
        "raw_padding": _RAW_SENTINEL}, tid=tid)


def test_tid_in_log_and_error_on_create_failure(monkeypatch, caplog):
    _seed_token()
    monkeypatch.setattr(qb.httpx, "post", lambda *a, **kw: _fault_resp())
    with caplog.at_level("INFO"):
        with pytest.raises(qb.QuickBooksError) as exc:
            qb.create_invoice("42", [{"description": "x", "amount": 100}])
    assert "intuit_tid tid-abc-123" in exc.value.detail      # user-facing
    assert "Duplicate DocNumber 1042" in exc.value.detail    # actionable
    assert any("tid-abc-123" in r.getMessage() for r in caplog.records)


def test_tid_in_log_and_error_on_query_failure(monkeypatch, caplog):
    _seed_token()
    monkeypatch.setattr(qb.httpx, "get", lambda *a, **kw: _fault_resp(status=403))
    with caplog.at_level("INFO"):
        with pytest.raises(qb.QuickBooksError) as exc:
            qb.list_customers()
    assert "intuit_tid tid-abc-123" in exc.value.detail
    assert any("tid-abc-123" in r.getMessage() for r in caplog.records)


def test_raw_fault_body_never_reaches_any_log_record(monkeypatch, caplog):
    _seed_token()
    monkeypatch.setattr(qb.httpx, "post", lambda *a, **kw: _fault_resp())
    with caplog.at_level("DEBUG"):
        with pytest.raises(qb.QuickBooksError):
            qb.create_invoice("42", [{"description": "x", "amount": 100}])
    for record in caplog.records:
        assert _RAW_SENTINEL not in record.getMessage()
    # The PARSED detail is what gets logged instead.
    assert any("Duplicate DocNumber 1042" in r.getMessage()
               for r in caplog.records)


def test_body_for_log_is_gone():
    assert not hasattr(qb, "_body_for_log")


# ==========================================================================
# 5. 401 → refresh once → retry once; invalid_grant clears state
# ==========================================================================

def _ok_query_resp():
    return _Resp(200, body={"QueryResponse": {"Customer": []}})


def test_401_triggers_exactly_one_refresh_and_retry(monkeypatch):
    _seed_token(fresh=True)                       # clock says NO refresh due
    gets, posts = [], []

    def fake_get(*a, **kw):
        gets.append(kw.get("headers", {}).get("Authorization", ""))
        return _Resp(401, tid="tid-401") if len(gets) == 1 else _ok_query_resp()

    def fake_post(*a, **kw):
        posts.append(1)
        return _Resp(200, body={"access_token": "at-2", "refresh_token": "rt-2"})

    monkeypatch.setattr(qb.httpx, "get", fake_get)
    monkeypatch.setattr(qb.httpx, "post", fake_post)
    out = qb.list_customers()
    assert out == []
    assert len(gets) == 2 and len(posts) == 1     # one refresh, one retry
    assert gets[0] == "Bearer at-1" and gets[1] == "Bearer at-2"
    # The rolling refresh was persisted.
    tok = qb._load_token()
    assert tok["refresh_token"] == "rt-2" and tok["realm_id"] == "9341"


def test_second_401_clears_token_and_asks_reconnect(monkeypatch):
    _seed_token(fresh=True)
    gets = []
    monkeypatch.setattr(qb.httpx, "get",
                        lambda *a, **kw: gets.append(1) or _Resp(401, tid="tid-2x"))
    monkeypatch.setattr(qb.httpx, "post", lambda *a, **kw: _Resp(
        200, body={"access_token": "at-2", "refresh_token": "rt-2"}))
    with pytest.raises(qb.QuickBooksError) as exc:
        qb.list_customers()
    assert len(gets) == 2                         # retried once, never looped
    assert "Connect QuickBooks again" in exc.value.detail
    assert "intuit_tid tid-2x" in exc.value.detail
    assert not qb.TOKEN_PATH.exists()             # cleared


def test_invalid_grant_on_refresh_clears_token_state(monkeypatch):
    _seed_token(fresh=False)                      # clock forces a refresh
    monkeypatch.setattr(qb.httpx, "post", lambda *a, **kw: _Resp(
        400, body={"error": "invalid_grant"}, tid="tid-ig"))
    with pytest.raises(qb.QuickBooksError) as exc:
        qb.list_customers()
    assert "invalid_grant" in exc.value.detail
    assert "Connect QuickBooks again" in exc.value.detail
    assert "intuit_tid tid-ig" in exc.value.detail
    assert not qb.TOKEN_PATH.exists()             # lineage dead → cleared


def test_transient_refresh_failure_keeps_the_token_file(monkeypatch):
    """A 5xx on the CLOCK-driven refresh path is not proof the lineage is
    dead — the file survives so a transient outage doesn't force reconsent."""
    _seed_token(fresh=False)
    monkeypatch.setattr(qb.httpx, "post", lambda *a, **kw: _Resp(503, tid="t"))
    with pytest.raises(qb.QuickBooksError) as exc:
        qb.list_customers()
    assert "reconnect in Settings" in exc.value.detail
    assert qb.TOKEN_PATH.exists()                 # kept
