"""Outbound SMS via Twilio (v7.0) — allowlist-only recipients, approval-gated.

The ONLY outbound messaging channel the Operator has. Three rules, each
enforced here or in operator_tools.send_sms, never by the planner:

  1. RECIPIENTS come from the SMS recipient allowlist the operator keeps in
     Settings (``sms_recipient_allowlist``: one ``Label = +E164`` per line).
     A tool call names a LABEL; the number is looked up here. Command text,
     contact records and memory are never a source of a phone number.
  2. NOTHING SENDS WITHOUT APPROVAL — the tool stages ``sms_send_pending``
     with the label, the E.164 number and the full body in the preview.
  3. CREDENTIALS (Account SID, Auth Token, From number) are DPAPI-wrapped at
     rest in local_settings.json (settings_service._DPAPI_ENCRYPTED_KEYS),
     read at call time, and never logged or returned by any endpoint. Twilio
     is called with HTTP basic auth; only message SIDs and statuses are logged.

``_transport`` is the test seam (httpx.MockTransport) — the suite never
reaches the network.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from . import settings_service

log = logging.getLogger("ridian.sms")

# httpx logs every request line at INFO ("HTTP Request: POST <url> ..."), and
# Twilio's URLs embed the Account SID. That logger is capped at WARNING so a
# credential never reaches backend.log by way of a URL; this module logs the
# message SID and status itself.
logging.getLogger("httpx").setLevel(logging.WARNING)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
TIMEOUT = 20.0
BODY_MAX_CHARS = 320
LABEL_MAX_CHARS = 40
# E.164: leading +, country code 1-9, 7-15 digits total.
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_LINE_SPLIT_RE = re.compile(r"\s*(?:=|:|,|\|)\s*")
_transport: Optional[httpx.BaseTransport] = None   # test seam; production = None


class SmsError(Exception):
    """A renderer-safe failure. ``detail`` never carries credentials."""

    def __init__(self, detail: str, *, code: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def credentials() -> tuple[str, str, str]:
    """(account_sid, auth_token, from_number) — plaintext in memory only."""
    s = settings_service.load_settings()
    return ((s.get("twilio_account_sid") or "").strip(),
            (s.get("twilio_auth_token") or "").strip(),
            (s.get("twilio_from_number") or "").strip())


def missing_credential_keys() -> list[str]:
    sid, token, sender = credentials()
    return [k for k, v in (("twilio_account_sid", sid),
                           ("twilio_auth_token", token),
                           ("twilio_from_number", sender)) if not v]


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

def normalize_e164(raw: str) -> str:
    """Strip spaces, dashes, dots and parentheses; return the E.164 form or ''."""
    text = re.sub(r"[\s().-]", "", str(raw or ""))
    return text if E164_RE.match(text) else ""


def parse_allowlist(text: str) -> list[dict]:
    """``Label = +15550100123`` per line → [{label, e164}]. Blank lines and
    ``#`` comments are ignored. Raises SmsError naming the offending line so
    the Settings save can refuse it plainly."""
    out: list[dict] = []
    seen: set[str] = set()
    for n, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = _LINE_SPLIT_RE.split(line, maxsplit=1)
        if len(parts) != 2:
            raise SmsError(f"Line {n}: expected 'Label = +15550100123' (a label, then the number).")
        label, number = parts[0].strip(), parts[1].strip()
        if not label or len(label) > LABEL_MAX_CHARS:
            raise SmsError(f"Line {n}: the label must be 1-{LABEL_MAX_CHARS} characters.")
        if normalize_e164(label) or re.fullmatch(r"[\d\s().+-]+", label):
            raise SmsError(f"Line {n}: the label must be a name, not a phone number.")
        e164 = normalize_e164(number)
        if not e164:
            raise SmsError(f"Line {n}: '{number}' is not an E.164 number "
                           "(+ country code then digits, e.g. +15550100123).")
        key = label.lower()
        if key in seen:
            raise SmsError(f"Line {n}: duplicate label '{label}'.")
        seen.add(key)
        out.append({"label": label, "e164": e164})
    return out


def format_allowlist(entries: list[dict]) -> str:
    return "\n".join(f"{e['label']} = {e['e164']}" for e in entries)


def allowlist() -> list[dict]:
    """The saved allowlist. An unparseable stored value reads as EMPTY —
    fail closed: nobody is a recipient until the operator fixes it."""
    text = settings_service.load_settings().get("sms_recipient_allowlist") or ""
    try:
        return parse_allowlist(text)
    except SmsError as exc:
        log.warning("sms.allowlist_invalid %s", exc.detail)
        return []


def resolve_recipient(label: str) -> Optional[dict]:
    """Exact (case-insensitive) label match against the allowlist, or None.
    This is the ONLY way a phone number enters the send path."""
    want = str(label or "").strip().lower()
    if not want:
        return None
    for entry in allowlist():
        if entry["label"].lower() == want:
            return dict(entry)
    return None


def recipient_is_allowlisted(label: str, e164: str) -> bool:
    """Independent second check used by the tool right before the gate: the
    label AND the number must belong to the SAME allowlist entry. A mutated
    resolver that hands back any number is caught here."""
    want = str(label or "").strip().lower()
    return any(e["label"].lower() == want and e["e164"] == e164 for e in allowlist())


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------

def _client(sid: str, token: str) -> httpx.Client:
    return httpx.Client(auth=(sid, token), timeout=TIMEOUT, transport=_transport,
                        headers={"User-Agent": "RidianOperator/1.0"})


def _safe_message(resp: httpx.Response) -> str:
    """Twilio's own error text (code + message), never our credentials."""
    try:
        data = resp.json()
        msg = str(data.get("message") or "").strip()
        code = data.get("code")
        return f"{msg} (Twilio error {code})" if code else (msg or f"HTTP {resp.status_code}")
    except Exception:  # noqa: BLE001
        return f"HTTP {resp.status_code}"


def test_connection() -> dict:
    """One read-only call to Twilio's Account resource. Sends NOTHING."""
    sid, token, sender = credentials()
    missing = missing_credential_keys()
    if missing:
        return {"ok": False, "source": "none",
                "detail": ("Twilio is not configured: missing " + ", ".join(missing)
                           + ". Fill them in under Settings → Text (SMS) and Save.")}
    try:
        with _client(sid, token) as client:
            resp = client.get(f"{TWILIO_API_BASE}/Accounts/{sid}.json")
    except Exception as exc:  # noqa: BLE001 — network trouble is a result
        return {"ok": False, "source": "settings",
                "detail": f"Could not reach Twilio ({type(exc).__name__}) — check your connection and try again."}
    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        name = str(data.get("friendly_name") or "account")
        status = str(data.get("status") or "unknown")
        return {"ok": True, "source": "settings",
                "detail": (f"Twilio credentials work — account “{name}” is {status}. "
                           f"Texts would send from {sender}. Nothing was sent.")}
    if resp.status_code in (401, 403):
        return {"ok": False, "source": "settings",
                "detail": (f"Twilio rejected the Account SID / Auth Token (HTTP {resp.status_code}). "
                           "Paste fresh values and Save.")}
    return {"ok": False, "source": "settings",
            "detail": f"Twilio responded HTTP {resp.status_code} — could not verify; try again."}


def send_message(to_e164: str, body: str) -> dict:
    """Send ONE message through Twilio's Messages API. Raises SmsError on
    every failure shape; a silent success is impossible (no SID → error).
    Returns {sid, status, price, price_unit, cost_usd, to}."""
    sid, token, sender = credentials()
    missing = missing_credential_keys()
    if missing:
        raise SmsError("Twilio is not configured: missing " + ", ".join(missing) + ".",
                       code="not_configured")
    if not E164_RE.match(str(to_e164 or "")):
        raise SmsError("The recipient is not an E.164 number.", code="bad_recipient")
    text = str(body or "").strip()
    if not text:
        raise SmsError("The message body is empty.", code="bad_body")
    if len(text) > BODY_MAX_CHARS:
        raise SmsError(f"The message body is {len(text)} characters; the limit is {BODY_MAX_CHARS}.",
                       code="bad_body")
    try:
        with _client(sid, token) as client:
            resp = client.post(f"{TWILIO_API_BASE}/Accounts/{sid}/Messages.json",
                               data={"From": sender, "To": to_e164, "Body": text})
    except Exception as exc:  # noqa: BLE001
        raise SmsError(f"Could not reach Twilio ({type(exc).__name__}).", code="network") from exc
    if resp.status_code not in (200, 201):
        raise SmsError(f"Twilio refused the message (HTTP {resp.status_code}): {_safe_message(resp)}",
                       code=f"http_{resp.status_code}")
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise SmsError("Twilio returned an unreadable response — treat the send as failed.",
                       code="bad_response") from exc
    message_sid = str(data.get("sid") or "").strip()
    if not message_sid:
        raise SmsError("Twilio returned no message SID — treat the send as failed.", code="no_sid")
    price = data.get("price")
    price_unit = str(data.get("price_unit") or "USD")
    cost: Optional[float] = None
    if price not in (None, ""):
        try:
            cost = abs(float(price))
        except (TypeError, ValueError):
            cost = None
    # SID and status only — never the number, the body, or credentials.
    log.info("sms.sent sid=%s status=%s", message_sid, data.get("status"))
    return {"sid": message_sid, "status": str(data.get("status") or "queued"),
            "price": price, "price_unit": price_unit, "cost_usd": cost, "to": to_e164}
