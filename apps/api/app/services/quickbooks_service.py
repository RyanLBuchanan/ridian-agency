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
installed-app pattern as Google). Production Client ID/Secret live in
Settings (secret never returned/logged); tokens in the git-ignored
quickbooks_token.json, refreshed automatically (rolling refresh persisted).
"""

from __future__ import annotations

import base64
import http.server
import json
import logging
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

from .settings_service import load_settings

log = logging.getLogger("ridian.quickbooks")

_API_DIR = Path(__file__).resolve().parent.parent.parent
TOKEN_PATH = _API_DIR / "quickbooks_token.json"

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API_BASE = "https://quickbooks.api.intuit.com/v3/company"
SCOPE = "com.intuit.quickbooks.accounting"
REDIRECT_PORT = 8123
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
_MINOR_VERSION = "73"


class QuickBooksError(Exception):
    """``detail`` is operator-safe — never contains secrets or tokens."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


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
    tok["saved_at"] = int(time.time())
    TOKEN_PATH.write_text(json.dumps(tok, indent=2), encoding="utf-8")


def get_status() -> dict:
    tok = _load_token()
    return {"connected": bool(tok and tok.get("refresh_token")),
            "realm_id": (tok or {}).get("realm_id", "")}


def disconnect() -> dict:
    if TOKEN_PATH.exists():
        try:
            TOKEN_PATH.unlink()
        except OSError:
            pass
    return {"connected": False}


def run_oauth_flow() -> dict:
    """Blocking browser consent → token exchange. Call via asyncio.to_thread."""
    cid, secret = _credentials()
    result: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — stdlib API
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = (q.get("code") or [""])[0]
            result["realm_id"] = (q.get("realmId") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h3>Ridian is connected to QuickBooks. Close this tab.</h3>")

        def log_message(self, *a):  # silence stdlib request logging
            return

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    params = urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "scope": SCOPE,
        "redirect_uri": REDIRECT_URI, "state": "ridian",
    })
    webbrowser.open(f"{AUTH_URL}?{params}")
    thread.join(timeout=300)
    server.server_close()
    if not result.get("code") or not result.get("realm_id"):
        raise QuickBooksError("QuickBooks consent did not complete — try again.", 500)

    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    resp = httpx.post(TOKEN_URL, headers={"Authorization": f"Basic {basic}"},
                      data={"grant_type": "authorization_code",
                            "code": result["code"], "redirect_uri": REDIRECT_URI},
                      timeout=30)
    if resp.status_code != 200:
        raise QuickBooksError(f"Token exchange failed (HTTP {resp.status_code}).", 502)
    tok = resp.json()
    tok["realm_id"] = result["realm_id"]
    _save_token(tok)
    log.info("quickbooks.connected realm=%s", result["realm_id"])
    return get_status()


def _access_token() -> tuple[str, str]:
    """(access_token, realm_id), refreshing when older than ~50 minutes."""
    tok = _load_token()
    if not tok or not tok.get("refresh_token"):
        raise QuickBooksError(
            "QuickBooks is not connected. Open Settings to connect first.", 400)
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
        _save_token(new)
        tok = new
    return tok["access_token"], tok["realm_id"]


def _query(sql: str) -> list[dict]:
    access, realm = _access_token()
    resp = httpx.get(
        f"{API_BASE}/{realm}/query",
        params={"query": sql, "minorversion": _MINOR_VERSION},
        headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise QuickBooksError(f"QuickBooks query failed (HTTP {resp.status_code}).", 502)
    return resp.json().get("QueryResponse", {})


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
    access, realm = _access_token()
    resp = httpx.post(
        f"{API_BASE}/{realm}/invoice", params={"minorversion": _MINOR_VERSION},
        headers={"Authorization": f"Bearer {access}",
                 "Accept": "application/json", "Content-Type": "application/json"},
        json=body, timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise QuickBooksError(f"Invoice create failed (HTTP {resp.status_code}).", 502)
    inv = resp.json().get("Invoice", {})
    out = {"id": inv.get("Id", ""), "doc_number": inv.get("DocNumber", ""),
           "customer": (inv.get("CustomerRef") or {}).get("name", ""),
           "total": inv.get("TotalAmt", 0),
           "email_status": inv.get("EmailStatus", "NotSet"),
           "link": f"https://qbo.intuit.com/app/invoice?txnId={inv.get('Id', '')}"}
    log.info("quickbooks.invoice_created id=%s total=%s", out["id"], out["total"])
    return out
