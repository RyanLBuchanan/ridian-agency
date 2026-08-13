"""QuickBooks Online integration (v4.0) — reads + ONE write, by construction.

Scope, enforced by what exists in this module:
  READ:  customers, items, invoices (query API).
  WRITE: create_invoice — the ONLY write function. There is no send, email,
         delete, void, update, or mark-paid anywhere in this file; a test
         introspects the module to keep it that way. QBO has NO draft state:
         a created invoice is a real, numbered, UNSENT invoice
         (EmailStatus=NotSet) in the production company file — the operator
         reviews/sends/deletes it in QuickBooks itself.

Auth: OAuth2 authorization-code with a loopback redirect (same
installed-app pattern as Google). Client ID/Secret live in Settings
(secret never returned/logged); tokens in the git-ignored
quickbooks_token.json, refreshed automatically (rolling refresh persisted).

Environments (v4.6): quickbooks_environment selects sandbox (default —
test company, Development keys, cannot touch real books) or production
(explicit opt-in, Production keys). The environment is SNAPSHOT once per
operation and threaded through guard, API base, and token stamping, so a
concurrent Settings flip can never aim a token at the wrong host.
"""

from __future__ import annotations

import base64
import http.server
import json
import logging
import secrets
import ssl
import threading
import time
import urllib.parse
from pathlib import Path

import httpx

from . import dpapi
from .settings_service import load_settings

log = logging.getLogger("ridian.quickbooks")

from .runtime_paths import data_dir, guard_real_state_write

# v4.2: dev -> apps/api/ exactly as before; frozen -> %APPDATA%/Ridian
# Operator/. The QBO token is a runtime file, never in the binary.
TOKEN_PATH = data_dir() / "quickbooks_token.json"

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
# v4.6 environments. Intuit's OAuth endpoints (authorize + token above) are
# SHARED between sandbox and production — the environment is determined by
# the app keys and the company chosen at consent. What differs is the API
# base (and the QBO web-app links), so those resolve per call from the
# quickbooks_environment setting.
PROD_API_BASE = "https://quickbooks.api.intuit.com/v3/company"
SANDBOX_API_BASE = "https://sandbox-quickbooks.api.intuit.com/v3/company"
PROD_APP_URL = "https://qbo.intuit.com"
SANDBOX_APP_URL = "https://sandbox.qbo.intuit.com"
SCOPE = "com.intuit.quickbooks.accounting"
REDIRECT_PORT = 8123
# v6.3: httpS — must byte-match the URI registered in Intuit's Production
# redirect list (https://localhost:8123/callback). The loopback listener
# serves TLS with a locally-generated self-signed localhost certificate;
# no CA signs localhost, so the browser shows a one-time interstitial on
# the callback redirect (Advanced → continue to localhost).
REDIRECT_URI = f"https://localhost:{REDIRECT_PORT}/callback"

# Self-signed localhost cert for the callback listener, generated once per
# machine into the data dir (runtime files, never in the binary). 10-year
# validity; regenerated automatically if deleted.
TLS_CERT_PATH = data_dir() / "qbo_callback_cert.pem"
TLS_KEY_PATH = data_dir() / "qbo_callback_key.pem"
_MINOR_VERSION = "75"   # Intuit ignores <75 since 2025-08-01 (75 is the floor)


class QuickBooksError(Exception):
    """``detail`` is operator-safe — never contains secrets or tokens."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def get_environment() -> str:
    """The active QuickBooks environment. 'production' ONLY when explicitly
    chosen in Settings; anything else — unset, blank, typo — resolves to
    'sandbox', the environment that cannot touch real books (safe default)."""
    env = (load_settings().get("quickbooks_environment") or "").strip().lower()
    return "production" if env == "production" else "sandbox"


def _api_base(env: str) -> str:
    """Base for an ALREADY-SNAPSHOT environment — never re-reads settings,
    so guard and URL can never disagree within one operation."""
    return PROD_API_BASE if env == "production" else SANDBOX_API_BASE


def _app_url(env: str) -> str:
    return PROD_APP_URL if env == "production" else SANDBOX_APP_URL


def _credentials() -> tuple[str, str]:
    s = load_settings()
    cid = (s.get("quickbooks_client_id") or "").strip()
    secret = (s.get("quickbooks_client_secret") or "").strip()
    if not cid or not secret:
        raise QuickBooksError(
            "QuickBooks Client ID/Secret are not set. Open Settings to add them.", 400)
    return cid, secret


# v6.2 encryption at rest: the token file is DPAPI-protected (user scope) —
# a magic prefix + CryptProtectData blob, deliberately NOT parseable JSON.
# The key lives with the Windows user's credentials, managed by the OS —
# stored separately from anything this app writes, by construction.
_DPAPI_MAGIC = b"RIDIAN-DPAPI-1\n"

# Honest failure state: set when a token FILE exists but cannot be
# decrypted (different Windows account, or corrupt). _access_token turns
# this into an actionable error instead of the misleading "not connected".
_token_load_error: list[str] = [""]


def _load_token() -> dict | None:
    if not TOKEN_PATH.exists():
        _token_load_error[0] = ""
        return None
    try:
        raw = TOKEN_PATH.read_bytes()
    except OSError:
        return None
    if raw.startswith(_DPAPI_MAGIC):
        try:
            tok = json.loads(dpapi.unprotect(raw[len(_DPAPI_MAGIC):]))
            _token_load_error[0] = ""
            return tok
        except (dpapi.DpapiError, json.JSONDecodeError, ValueError) as exc:
            log.warning("quickbooks.token_undecryptable type=%s", type(exc).__name__)
            _token_load_error[0] = (
                "The saved QuickBooks connection cannot be decrypted — it was "
                "encrypted by a different Windows account, or the file is "
                "corrupt. Open Settings and Connect QuickBooks again.")
            return None
    # Legacy plaintext token (pre-v6.2): migrate to encrypted ON FIRST LOAD,
    # preserving saved_at/environment exactly (no restamping).
    try:
        tok = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(tok, dict) and tok.get("refresh_token"):
        try:
            _write_token_file(tok)
            log.info("quickbooks.token_migrated_to_dpapi")
        except Exception as exc:  # noqa: BLE001 — keep serving plaintext
            log.warning("quickbooks.token_migration_failed type=%s",
                        type(exc).__name__)
    _token_load_error[0] = ""
    return tok if isinstance(tok, dict) else None


def _write_token_file(tok: dict) -> None:
    guard_real_state_write(TOKEN_PATH)   # v4.4: tests never write real tokens
    TOKEN_PATH.write_bytes(
        _DPAPI_MAGIC + dpapi.protect(json.dumps(tok).encode("utf-8")))


def _save_token(tok: dict) -> None:
    tok["saved_at"] = int(time.time())
    # v4.6: every token carries the environment it belongs to. Callers that
    # KNOW the environment (consent completion, refresh inheritance) set it
    # before calling; this fallback only covers a caller that forgot, and
    # never overwrites an existing stamp — a Settings flip between an
    # operation's start and this save must not relabel the token.
    tok.setdefault("environment", get_environment())
    _write_token_file(tok)


def _token_environment(tok: dict) -> str:
    """Tokens from before v4.6 carry no stamp — they were created when the
    integration was hardcoded to production, so that is what they are."""
    return tok.get("environment") or "production"


def get_status() -> dict:
    tok = _load_token()
    with _flow_lock:
        in_progress = _flow_state["in_progress"]
        flow_error = _flow_state["error"]
    env = get_environment()
    connected = bool(tok and tok.get("refresh_token"))
    return {"connected": connected,
            "realm_id": (tok or {}).get("realm_id", ""),
            "environment": env,
            # True when a saved connection belongs to the OTHER environment —
            # the UI tells the operator to reconnect rather than letting
            # calls fail with opaque auth errors.
            "environment_mismatch": bool(connected and _token_environment(tok) != env),
            "in_progress": in_progress,
            "flow_error": flow_error}


def disconnect() -> dict:
    if TOKEN_PATH.exists():
        try:
            TOKEN_PATH.unlink()
        except OSError:
            pass
    _token_load_error[0] = ""
    return {"connected": False}


def _tid(resp) -> str:
    """Intuit's per-request trace id from the response headers — the one
    thing Intuit support asks for on every ticket. Captured on every
    non-success response (v6.2)."""
    try:
        return str(resp.headers.get("intuit_tid") or "")
    except Exception:  # noqa: BLE001 — headers absent on some fakes/transports
        return ""


def _tid_suffix(resp) -> str:
    t = _tid(resp)
    return f" (intuit_tid {t})" if t else ""


# v4.3: the OAuth browser launch moved OUT of this backend process. The
# packaged backend is a hidden PyInstaller --noconsole process (spawned
# windowsHide by Electron) and webbrowser.open() there returns WITHOUT
# launching anything — and the old code ignored its return value and then
# blocked for 300s, so the Connect click looked like a silent no-op.
# Now: begin_oauth() binds the loopback listener, returns the consent URL,
# and finishes the token exchange on a background thread; the DESKTOP opens
# the URL (renderer window.open → Electron shell.openExternal). Progress and
# every failure are reported through get_status() — nothing is silent.
_FLOW_TIMEOUT_S = 300
_flow_lock = threading.Lock()
_flow_state: dict = {"in_progress": False, "error": ""}


def _ensure_tls_cert() -> tuple[Path, Path]:
    """Self-signed cert for CN=localhost (SANs: localhost, 127.0.0.1),
    generated once per machine, reused until deleted. Private key stays in
    the user's data dir next to the other runtime credentials — it protects
    only a loopback hop on this machine."""
    if TLS_CERT_PATH.exists() and TLS_KEY_PATH.exists():
        return TLS_CERT_PATH, TLS_KEY_PATH
    guard_real_state_write(TLS_KEY_PATH)   # tests never write real state
    import datetime as _dt
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(days=1))
            .not_valid_after(now + _dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]), critical=False)
            .sign(key, hashes.SHA256()))
    TLS_KEY_PATH.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    TLS_CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    log.info("quickbooks.tls_cert_generated path=%s", TLS_CERT_PATH)
    return TLS_CERT_PATH, TLS_KEY_PATH


def _wrap_tls(server: http.server.HTTPServer) -> None:
    """v6.3: the callback listener serves HTTPS so the redirect URI can
    byte-match the https://localhost URI registered with Intuit."""
    cert, key = _ensure_tls_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """v6.2 hardened callback.

    - ``state`` is validated against the per-flow random value bound to the
      listener; a mismatch is REFUSED — the code is never recorded, never
      exchanged, and the pending consent keeps waiting for the real
      callback (a forged request must not be able to finish OR abort it).
    - The callback answers 302 to a parameter-free local page, so
      code/realmId/state never persist in browser history the way a 200 on
      the parameterized URL did.
    """

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _page(self, html: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html)

    def do_GET(self):  # noqa: N802 — stdlib API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/connected":
            self._page(b"<h3>Ridian is connected to QuickBooks. "
                       b"Close this tab.</h3>")
            return
        if parsed.path == "/rejected":
            self._page(b"<h3>QuickBooks connection attempt rejected "
                       b"(security check failed). Close this tab and click "
                       b"Connect again in Ridian.</h3>")
            return
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(parsed.query)
        expected = getattr(self.server, "ridian_expected_state", None)
        got = (q.get("state") or [""])[0]
        if not expected or not secrets.compare_digest(got, expected):
            log.warning("quickbooks.oauth_state_mismatch")
            self._redirect("/rejected")
            return          # result untouched — nothing is exchanged
        result = getattr(self.server, "ridian_result", {})
        result["code"] = (q.get("code") or [""])[0]
        result["realm_id"] = (q.get("realmId") or [""])[0]
        self._redirect("/connected")

    def log_message(self, *a):  # silence stdlib request logging
        return


def begin_oauth() -> dict:
    """Phase 1: bind the callback listener, return {"auth_url": ...}.

    Raises immediately (with an operator-actionable message) when creds are
    missing, a flow is already pending, or the callback port can't bind."""
    cid, _secret = _credentials()
    with _flow_lock:
        if _flow_state["in_progress"]:
            raise QuickBooksError(
                "A QuickBooks connect attempt is already waiting for browser "
                "consent. Finish it or wait for it to time out.", 409)
        try:
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
            _wrap_tls(server)   # v6.3: https://localhost — matches Intuit's list
        except OSError as exc:
            log.warning("quickbooks.oauth_bind_failed port=%s type=%s",
                        REDIRECT_PORT, type(exc).__name__)
            raise QuickBooksError(
                f"Could not open the OAuth callback port {REDIRECT_PORT} — "
                "another app is using it. Close it and try again.", 500) from exc
        except Exception as exc:  # noqa: BLE001 — cert generation/load failed
            try:
                server.server_close()   # the bind succeeded — free the port
            except Exception:  # noqa: BLE001
                pass
            log.warning("quickbooks.oauth_tls_failed type=%s", type(exc).__name__)
            raise QuickBooksError(
                "Could not prepare the HTTPS callback listener "
                f"({type(exc).__name__}). Delete {TLS_CERT_PATH.name}/"
                f"{TLS_KEY_PATH.name} in the data folder and try again.", 500) from exc
        _flow_state.update(in_progress=True, error="")
    server.ridian_result = {}
    # v6.2: per-flow cryptographically random state, bound to THIS listener.
    # The callback validates it and refuses mismatches — the CSRF handling
    # the constant "ridian" only pretended to be.
    state = secrets.token_urlsafe(32)
    server.ridian_expected_state = state
    env = get_environment()   # SNAPSHOT: the consent belongs to this env,
    # even if Settings flips during the (up to 5-minute) browser wait.
    threading.Thread(target=_complete_oauth, args=(server, env), daemon=True).start()
    params = urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "scope": SCOPE,
        "redirect_uri": REDIRECT_URI, "state": state,
    })
    log.info("quickbooks.oauth_begun port=%s env=%s", REDIRECT_PORT, env)
    return {"auth_url": f"{AUTH_URL}?{params}"}


def _complete_oauth(server: http.server.HTTPServer, env: str) -> None:
    """Background: wait for ONE callback (or timeout), exchange the code.
    ``env`` is the environment snapshot from begin_oauth — the token is
    stamped with it and validated against its API base before we report
    success, so Development/Production keys pasted into the wrong
    environment fail HERE with a plain message, not later with opaque 401s."""
    result = server.ridian_result
    err = ""
    try:
        # Wait for a REAL callback (do_GET sets the "code" key, possibly
        # empty), not merely a TCP connection — stray connects (port scans,
        # health probes) must not abort a pending consent.
        deadline = time.time() + _FLOW_TIMEOUT_S
        server.timeout = 5
        while time.time() < deadline and "code" not in result:
            server.handle_request()
        # v6.2: the callback answered 302 — serve the browser's follow-up
        # GET of the clean /connected page before closing (best-effort;
        # short timeout so a browser that never follows costs ~2s, not 300).
        server.timeout = 1
        for _ in range(2):
            try:
                server.handle_request()
            except Exception:  # noqa: BLE001
                break
        server.server_close()
        if not result.get("code") or not result.get("realm_id"):
            err = ("Browser consent did not complete within 5 minutes. "
                   "If no browser window opened, try Connect again.")
        else:
            cid, secret = _credentials()
            basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
            resp = httpx.post(TOKEN_URL, headers={"Authorization": f"Basic {basic}"},
                              data={"grant_type": "authorization_code",
                                    "code": result["code"],
                                    "redirect_uri": REDIRECT_URI},
                              timeout=30)
            if resp.status_code != 200:
                err = (f"QuickBooks token exchange failed "
                       f"(HTTP {resp.status_code}){_tid_suffix(resp)}.")
            else:
                tok = resp.json()
                tok["realm_id"] = result["realm_id"]
                tok["environment"] = env
                # Validate against THIS environment's API before declaring
                # success: a consent driven by keys from the other
                # environment yields a token its API base rejects.
                realm = result["realm_id"]
                check = httpx.get(
                    f"{_api_base(env)}/{realm}/companyinfo/{realm}",
                    params={"minorversion": _MINOR_VERSION},
                    headers={"Authorization": f"Bearer {tok.get('access_token', '')}",
                             "Accept": "application/json"},
                    timeout=30)
                if check.status_code != 200:
                    err = (f"Consent completed, but the {env} API rejected the "
                           f"connection (HTTP {check.status_code})"
                           f"{_tid_suffix(check)}. Check that "
                           f"your Client ID/Secret are "
                           f"{'Development' if env == 'sandbox' else 'Production'} "
                           f"keys and the company you picked is a {env} company.")
                else:
                    _save_token(tok)
                    log.info("quickbooks.connected realm=%s env=%s",
                             result["realm_id"], env)
    except Exception as exc:  # noqa: BLE001 — surfaced via flow_error, never silent
        err = f"QuickBooks connect failed ({type(exc).__name__})."
    finally:
        with _flow_lock:
            _flow_state.update(in_progress=False, error=err)
        if err:
            log.warning("quickbooks.oauth_failed detail=%s", err)


def _refresh_access(tok: dict) -> dict:
    """Exchange the refresh token for a new access token (rolling refresh
    persisted). v6.2: ``invalid_grant`` — Intuit's explicit signal that the
    refresh token itself is revoked/expired — CLEARS the stored token (that
    lineage is dead) and asks for reconnect. Any other failure keeps the
    file (it may be transient) but still asks for reconnect, with the
    intuit_tid attached for support."""
    cid, secret = _credentials()
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    resp = httpx.post(TOKEN_URL, headers={"Authorization": f"Basic {basic}"},
                      data={"grant_type": "refresh_token",
                            "refresh_token": tok["refresh_token"]},
                      timeout=30)
    if resp.status_code != 200:
        try:
            oauth_error = str(resp.json().get("error") or "")
        except Exception:  # noqa: BLE001 — non-JSON body
            oauth_error = ""
        if oauth_error == "invalid_grant":
            disconnect()
            log.warning("quickbooks.refresh_invalid_grant tid=%s", _tid(resp))
            raise QuickBooksError(
                "The QuickBooks session has expired (invalid_grant) — the "
                f"saved connection was cleared{_tid_suffix(resp)}. Open "
                "Settings and Connect QuickBooks again.", 401)
        log.warning("quickbooks.refresh_failed status=%s tid=%s",
                    resp.status_code, _tid(resp))
        raise QuickBooksError(
            f"QuickBooks token refresh failed (HTTP {resp.status_code})"
            f"{_tid_suffix(resp)} — reconnect in Settings.", 502)
    new = resp.json()
    new["realm_id"] = tok["realm_id"]
    # INHERIT the lineage's environment — never re-read settings here,
    # or a flip during the refresh window would relabel the token and
    # permanently defeat the mismatch guard.
    new["environment"] = _token_environment(tok)
    _save_token(new)
    return new


def _access_token(env: str, force_refresh: bool = False) -> tuple[str, str]:
    """(access_token, realm_id) for the SNAPSHOT environment ``env``,
    refreshing when older than ~50 minutes (or on demand — the 401 retry
    path). The caller snapshots the environment ONCE and uses it for this
    guard and for the API base, so a concurrent Settings flip cannot aim
    the token at the wrong host."""
    tok = _load_token()
    if not tok or not tok.get("refresh_token"):
        if _token_load_error[0]:
            raise QuickBooksError(_token_load_error[0], 401)
        raise QuickBooksError(
            "QuickBooks is not connected. Open Settings to connect first.", 400)
    if _token_environment(tok) != env:
        raise QuickBooksError(
            f"QuickBooks is connected to the {_token_environment(tok)} "
            f"environment, but Settings now selects {env}. Open Settings and "
            "click Connect QuickBooks to reconnect (or switch the environment "
            "back).", 409)
    if force_refresh or time.time() - tok.get("saved_at", 0) > 3000:
        tok = _refresh_access(tok)
    return tok["access_token"], tok["realm_id"]


def _authed(env: str, send) -> "httpx.Response":
    """v6.2: run ONE QBO request with auth; on 401 refresh ONCE and retry
    ONCE. A second 401 — or a refresh that fails on this path — means the
    stored connection is dead: clear it and ask for reconnect. Exactly two
    attempts, never a loop."""
    access, realm = _access_token(env)
    resp = send(access, realm)
    if resp.status_code != 401:
        return resp
    log.info("quickbooks.retry_after_401 tid=%s", _tid(resp))
    try:
        access, realm = _access_token(env, force_refresh=True)
    except QuickBooksError:
        disconnect()      # the 401 proved the lineage bad; keep nothing stale
        raise
    resp = send(access, realm)
    if resp.status_code == 401:
        disconnect()
        log.warning("quickbooks.auth_rejected_twice tid=%s", _tid(resp))
        raise QuickBooksError(
            "QuickBooks rejected the connection twice (HTTP 401)"
            f"{_tid_suffix(resp)} — the saved connection was cleared. Open "
            "Settings and Connect QuickBooks again.", 401)
    return resp


def _query(sql: str) -> list[dict]:
    env = get_environment()          # ONE snapshot: guard + base agree
    resp = _authed(env, lambda access, realm: httpx.get(
        f"{_api_base(env)}/{realm}/query",
        params={"query": sql, "minorversion": _MINOR_VERSION},
        headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
        timeout=30,
    ))
    if resp.status_code != 200:
        detail = _fault_detail(resp)
        log.warning("quickbooks.query_failed status=%s tid=%s detail=%s",
                    resp.status_code, _tid(resp), detail)
        raise QuickBooksError(
            f"QuickBooks query failed (HTTP {resp.status_code})"
            + (f": {detail}" if detail else "") + _tid_suffix(resp), 502)
    return resp.json().get("QueryResponse", {})


def _fault_detail(resp) -> str:
    """Human-readable detail from a QuickBooks Fault response body.

    QBO errors carry Fault.Error[].{code, Message, Detail}; the Detail
    string ("Required param missing...", duplicate DocNumber, etc.) is the
    part the operator can act on — 'HTTP 400' alone taught nobody anything."""
    try:
        errors = (resp.json().get("Fault") or {}).get("Error") or []
    except Exception:  # noqa: BLE001 — non-JSON body
        return ""
    parts = []
    for e in errors:
        frag = (e.get("Detail") or "").strip() or (e.get("Message") or "").strip()
        code = str(e.get("code") or "").strip()
        if frag:
            parts.append(frag + (f" (code {code})" if code else ""))
    return "; ".join(parts)


def list_customers() -> list[dict]:
    rows = _query("select Id, DisplayName, PrimaryEmailAddr from Customer "
                  "where Active = true maxresults 1000").get("Customer", [])
    return [{"id": c.get("Id", ""), "name": c.get("DisplayName", ""),
             "email": (c.get("PrimaryEmailAddr") or {}).get("Address", "")}
            for c in rows]


def list_items() -> list[dict]:
    rows = _query("select Id, Name, UnitPrice, Type from Item "
                  "where Active = true maxresults 1000").get("Item", [])
    return [{"id": i.get("Id", ""), "name": i.get("Name", ""),
             "unit_price": i.get("UnitPrice", 0), "type": i.get("Type", "")}
            for i in rows]


def list_invoices(limit: int = 20) -> list[dict]:
    rows = _query("select Id, DocNumber, TotalAmt, Balance, EmailStatus, "
                  f"CustomerRef, TxnDate from Invoice orderby TxnDate desc "
                  f"maxresults {max(1, min(int(limit), 100))}").get("Invoice", [])
    return [{"id": v.get("Id", ""), "doc_number": v.get("DocNumber", ""),
             "customer": (v.get("CustomerRef") or {}).get("name", ""),
             "date": v.get("TxnDate", ""), "total": v.get("TotalAmt", 0),
             "balance": v.get("Balance", 0),
             "email_status": v.get("EmailStatus", "NotSet")} for v in rows]


def create_invoice(customer_id: str, lines: list[dict], txn_date: str = "",
                   due_date: str = "") -> dict:
    """THE single write: create a real, UNSENT invoice. Lines are
    [{"description", "amount", optional "item_id", "qty", "unit_price"}].
    Never sets EmailStatus, never calls send — review happens in QBO."""
    if not customer_id or not lines:
        raise QuickBooksError("customer_id and at least one line are required.", 400)
    qb_lines = []
    for ln in lines:
        amount = float(ln.get("amount", 0) or 0)
        detail: dict = {}
        if ln.get("item_id"):
            detail["ItemRef"] = {"value": str(ln["item_id"])}
        if ln.get("qty") is not None and ln.get("unit_price") is not None:
            detail["Qty"] = float(ln["qty"])
            detail["UnitPrice"] = float(ln["unit_price"])
            amount = round(detail["Qty"] * detail["UnitPrice"], 2)
        if amount <= 0:
            raise QuickBooksError("Every line needs a positive amount.", 400)
        qb_lines.append({"DetailType": "SalesItemLineDetail",
                         "Amount": amount,
                         "Description": str(ln.get("description", "") or ""),
                         "SalesItemLineDetail": detail})
    body: dict = {"CustomerRef": {"value": str(customer_id)}, "Line": qb_lines}
    if txn_date:
        body["TxnDate"] = txn_date
    if due_date:
        body["DueDate"] = due_date
    env = get_environment()          # ONE snapshot: guard + base + link agree
    realm_used = [""]                # captured for the deep link below

    def _send(access: str, realm: str):
        realm_used[0] = realm
        return httpx.post(
            f"{_api_base(env)}/{realm}/invoice",
            params={"minorversion": _MINOR_VERSION},
            headers={"Authorization": f"Bearer {access}",
                     "Accept": "application/json",
                     "Content-Type": "application/json"},
            json=body, timeout=30,
        )

    resp = _authed(env, _send)
    if resp.status_code not in (200, 201):
        # v6.2 REDACTED failure logging: the parsed Fault detail + intuit_tid,
        # never the raw response body (capped was not redacted).
        detail = _fault_detail(resp)
        log.warning("quickbooks.invoice_create_failed status=%s tid=%s detail=%s",
                    resp.status_code, _tid(resp), detail)
        raise QuickBooksError(
            f"Invoice create failed (HTTP {resp.status_code})"
            + (f": {detail}" if detail else ".") + _tid_suffix(resp), 502)
    inv = resp.json().get("Invoice", {})
    out = {"id": inv.get("Id", ""), "doc_number": inv.get("DocNumber", ""),
           "customer": (inv.get("CustomerRef") or {}).get("name", ""),
           "total": inv.get("TotalAmt", 0),
           "email_status": inv.get("EmailStatus", "NotSet"),
           # deeplinkcompanyid makes the link survive QBO's sign-in redirect:
           # without it the auth hop drops the whole path+query (verified —
           # the sign-in Location carries no continuation) and the user lands
           # on a BLANK new-invoice form; with it, the auth layer parses the
           # company (it surfaces as account_id_hint in the sign-in URL) and
           # restores the deep link after login.
           "link": (f"{_app_url(env)}/app/invoice?txnId={inv.get('Id', '')}"
                    f"&deeplinkcompanyid={realm_used[0]}")}
    log.info("quickbooks.invoice_created id=%s total=%s", out["id"], out["total"])
    return out
