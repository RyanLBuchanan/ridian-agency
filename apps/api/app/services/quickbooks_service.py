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
import threading
import time
import urllib.parse
from pathlib import Path

import httpx

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
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
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


def _load_token() -> dict | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_token(tok: dict) -> None:
    guard_real_state_write(TOKEN_PATH)   # v4.4: tests never write real tokens
    tok["saved_at"] = int(time.time())
    # v4.6: every token carries the environment it belongs to. Callers that
    # KNOW the environment (consent completion, refresh inheritance) set it
    # before calling; this fallback only covers a caller that forgot, and
    # never overwrites an existing stamp — a Settings flip between an
    # operation's start and this save must not relabel the token.
    tok.setdefault("environment", get_environment())
    TOKEN_PATH.write_text(json.dumps(tok, indent=2), encoding="utf-8")


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
    return {"connected": False}


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


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — stdlib API
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        result = getattr(self.server, "ridian_result", {})
        result["code"] = (q.get("code") or [""])[0]
        result["realm_id"] = (q.get("realmId") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h3>Ridian is connected to QuickBooks. Close this tab.</h3>")

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
        except OSError as exc:
            log.warning("quickbooks.oauth_bind_failed port=%s type=%s",
                        REDIRECT_PORT, type(exc).__name__)
            raise QuickBooksError(
                f"Could not open the OAuth callback port {REDIRECT_PORT} — "
                "another app is using it. Close it and try again.", 500) from exc
        _flow_state.update(in_progress=True, error="")
    server.ridian_result = {}
    env = get_environment()   # SNAPSHOT: the consent belongs to this env,
    # even if Settings flips during the (up to 5-minute) browser wait.
    threading.Thread(target=_complete_oauth, args=(server, env), daemon=True).start()
    params = urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "scope": SCOPE,
        "redirect_uri": REDIRECT_URI, "state": "ridian",
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
                err = f"QuickBooks token exchange failed (HTTP {resp.status_code})."
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
                           f"connection (HTTP {check.status_code}). Check that "
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


def _access_token(env: str) -> tuple[str, str]:
    """(access_token, realm_id) for the SNAPSHOT environment ``env``,
    refreshing when older than ~50 minutes. The caller snapshots the
    environment ONCE and uses it for this guard and for the API base, so a
    concurrent Settings flip cannot aim the token at the wrong host."""
    tok = _load_token()
    if not tok or not tok.get("refresh_token"):
        raise QuickBooksError(
            "QuickBooks is not connected. Open Settings to connect first.", 400)
    if _token_environment(tok) != env:
        raise QuickBooksError(
            f"QuickBooks is connected to the {_token_environment(tok)} "
            f"environment, but Settings now selects {env}. Open Settings and "
            "click Connect QuickBooks to reconnect (or switch the environment "
            "back).", 409)
    if time.time() - tok.get("saved_at", 0) > 3000:
        cid, secret = _credentials()
        basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        resp = httpx.post(TOKEN_URL, headers={"Authorization": f"Basic {basic}"},
                          data={"grant_type": "refresh_token",
                                "refresh_token": tok["refresh_token"]},
                          timeout=30)
        if resp.status_code != 200:
            raise QuickBooksError(
                f"QuickBooks token refresh failed (HTTP {resp.status_code}) — "
                "reconnect in Settings.", 502)
        new = resp.json()
        new["realm_id"] = tok["realm_id"]
        # INHERIT the lineage's environment — never re-read settings here,
        # or a flip during the refresh window would relabel the token and
        # permanently defeat the mismatch guard.
        new["environment"] = _token_environment(tok)
        _save_token(new)
        tok = new
    return tok["access_token"], tok["realm_id"]


def _query(sql: str) -> list[dict]:
    env = get_environment()          # ONE snapshot: guard + base agree
    access, realm = _access_token(env)
    resp = httpx.get(
        f"{_api_base(env)}/{realm}/query",
        params={"query": sql, "minorversion": _MINOR_VERSION},
        headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise QuickBooksError(f"QuickBooks query failed (HTTP {resp.status_code}).", 502)
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


def _body_for_log(resp) -> str:
    """Full response body for backend.log, capped so a rogue payload can't
    flood the log. Never contains our credentials — it is QBO's reply."""
    try:
        body = getattr(resp, "text", "") or json.dumps(resp.json())
    except Exception:  # noqa: BLE001
        body = "<unreadable>"
    return body[:2000]


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
    access, realm = _access_token(env)
    resp = httpx.post(
        f"{_api_base(env)}/{realm}/invoice", params={"minorversion": _MINOR_VERSION},
        headers={"Authorization": f"Bearer {access}",
                 "Accept": "application/json", "Content-Type": "application/json"},
        json=body, timeout=30,
    )
    if resp.status_code not in (200, 201):
        detail = _fault_detail(resp)
        log.warning("quickbooks.invoice_create_failed status=%s body=%s",
                    resp.status_code, _body_for_log(resp))
        raise QuickBooksError(
            f"Invoice create failed (HTTP {resp.status_code})"
            + (f": {detail}" if detail else "."), 502)
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
                    f"&deeplinkcompanyid={realm}")}
    log.info("quickbooks.invoice_created id=%s total=%s", out["id"], out["total"])
    return out
