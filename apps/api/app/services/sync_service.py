"""Owner Workspace sync (v7.1) — keep ridiantechnologies.com/owner current.

WHAT IT DOES. The Owner Workspace on the website shows a read-only home built
from the newest Owner Snapshot v1 document (owner_snapshot_service). This
module sends that document to the site by itself: outbound only, from this
PC, over HTTPS, authenticated by a per-device bearer token the owner issues on
the site. Nothing on the site can reach into this PC, and nothing here reads
anything from the site except the answer to its own request.

THE CONTRACT WITH THE SITE (docs/owner-workspace-sync.md):

  push     POST <site>/api/operator-snapshot/push
           Authorization: Bearer <device token>, Content-Type:
           application/json, body = the snapshot document (at most 2 MB).
  pair     POST <site>/api/devices/pair
           Authorization: Bearer <pairing code>, body {"label": ...};
           200 -> {"token", "expiresAt"}. A site without the exchange answers
           403/404/405; the code the owner pasted is then the device token
           itself (today's site issues it directly on /owner/devices), and it
           is saved only after one real push proves the site accepts it.
  refresh  POST <site>/api/devices/refresh with the current token;
           200 -> {"token", "expiresAt"}, and the old token is dead.
  revoke   POST <site>/api/devices/revoke with the current token. Best
           effort: Disconnect never depends on it.
  authorize (v7.2, what Connect does)
           POST <site>/api/devices/authorize/start {label} -> {requestId,
           userCode, verifyUrl, expiresAt, interval}. The renderer opens
           verifyUrl in the default browser; the owner approves it there.
           GET <site>/api/devices/authorize/poll?request=<requestId> ->
           pending | denied | approved {token, expiresAt} exactly once |
           410 expired. requestId is a polling secret: it stays in this
           process's memory and never reaches the renderer or a log.

THE RULES, each pinned by tests/test_owner_workspace_sync.py:

  1. The token is DPAPI-wrapped at rest in <data_dir>/owner_workspace.json
     (deliberately NOT in state/: status writes must not rotate the state
     backups), decrypted only for the request that uses it, never logged,
     never returned by an endpoint, never exported. It only ever travels to
     the site it was paired with, and redirects are not followed.
  2. Triggers: backend startup (after 60 s), an operation reaching a
     terminal state, any approval staged/answered/voided, any obligation or
     deal write, and a 30-minute timer. Every trigger is debounced 20 s so a
     burst becomes one push; a steady trickle can hold a push back at most
     120 s.
  3. 401 means the token is dead: it is discarded, the connection is marked
     disconnected, and nothing is sent again until the owner reconnects.
  4. 429, a network failure, a 5xx, a redirect or a refused document back off
     (exponential, honoring Retry-After) and are retried only on a later
     trigger, never in a loop.
  5. A token within 7 days of its known expiry is refreshed before the push;
     a site without the refresh endpoint is asked again after a day, not on
     every push. A token past its known expiry is never sent.
  6. An unchanged document is not sent (v7.2). The content hash of the last
     push the site accepted (sha256 of the canonical document without
     generatedAt and source.localUtcOffset) is remembered; a trigger or the
     timer that finds the same hash records "unchanged" and sends nothing.

The loopback-only routes in main.py are the only callers of pair(),
start_browser_pairing(), cancel_browser_pairing(),
disconnect() and status_view(); none of them is on the companion allowlist.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import httpx

from . import state_store
from .runtime_paths import data_dir, guard_real_state_write

log = logging.getLogger("ridian.owner_sync")

DEFAULT_SITE = "https://ridiantechnologies.com"
PUSH_PATH = "/api/operator-snapshot/push"
PAIR_PATH = "/api/devices/pair"
REFRESH_PATH = "/api/devices/refresh"
REVOKE_PATH = "/api/devices/revoke"
AUTHORIZE_START_PATH = "/api/devices/authorize/start"
AUTHORIZE_POLL_PATH = "/api/devices/authorize/poll"
APPROVE_PATH = "/owner/devices/approve"

# Beside local_settings.json, never under state/ — see rule 1.
SYNC_PATH = data_dir() / "owner_workspace.json"

DEBOUNCE_SECONDS = 20.0
MAX_COALESCE_SECONDS = 120.0
STARTUP_DELAY_SECONDS = 60.0
HEARTBEAT_SECONDS = 30 * 60.0
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_MAX_SECONDS = 30 * 60.0
RETRY_AFTER_CAP_SECONDS = 60 * 60.0
REFRESH_WINDOW = _dt.timedelta(days=7)
REFRESH_UNSUPPORTED_RETRY = _dt.timedelta(days=1)
MAX_BODY_BYTES = 2 * 1024 * 1024       # the site's own cap
TIMEOUT = 20.0
REVOKE_TIMEOUT = 10.0
LABEL_MAX_CHARS = 64
PAIRING_MAX_SECONDS = 10 * 60.0      # the site's own request lifetime
POLL_INTERVAL_DEFAULT = 5.0
POLL_INTERVAL_MIN = 2.0
POLL_INTERVAL_MAX = 30.0
PAIRING_ERROR_LIMIT = 5              # consecutive transient poll failures

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,256}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
USER_CODE_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")

# Answers that mean "this site has no such endpoint": the site's Origin gate
# refuses an unknown non-GET path with 403, a router answers 404/405.
_UNAVAILABLE = frozenset({403, 404, 405})
# Push answers that prove the site AUTHENTICATED the bearer token first: its
# only answer to a bad token is 401, and it checks the token before the
# content type (415), the size (413), the hourly allowance (429), the body
# (400) and the document (422).
_AUTHENTICATED = frozenset({200, 400, 413, 415, 422, 429})

# State-store writes that are sync triggers by themselves (rule 2). The
# operations store is special-cased: only a run REACHING a terminal state
# counts, not every progress write.
WATCHED_STORES = frozenset({"approvals", "obligations", "deals"})
# "partial" is a finished run that also hit errors, so it is terminal here.
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "partial"})

_SEAL_PREFIX = "dpapi1:"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_transport: Optional[httpx.BaseTransport] = None   # test seam; production = None
_store_lock = threading.RLock()


class SyncError(Exception):
    """A refusal the Settings UI can show verbatim. Never carries a token or
    a pairing code."""

    def __init__(self, detail: str, *, status: int = 400, code: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(value: Optional[_dt.datetime]) -> str:
    if value is None:
        return ""
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> Optional[_dt.datetime]:
    """A zone-aware UTC datetime, or None. An expiry without a zone is not
    trusted — guessing it could send a dead token or skip a refresh."""
    text = str(value or "").strip()
    if not text:
        return None
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(_dt.timezone.utc)


def _app_version() -> str:
    """Mirrors main.app_version(): the installer version, or "dev"."""
    return (os.environ.get("RIDIAN_APP_VERSION") or "").strip() or "dev"


def _client(timeout: float = TIMEOUT) -> httpx.Client:
    # follow_redirects stays False: a redirect must never carry the bearer
    # token to another address (www.ridiantechnologies.com answers 308).
    return httpx.Client(timeout=timeout, transport=_transport, follow_redirects=False,
                        headers={"User-Agent": f"RidianOperator/{_app_version()} owner-sync"})


def _bearer(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def _host(site: str) -> str:
    return urlsplit(site).hostname or site or "the Owner Workspace"


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _redirect_target(resp: httpx.Response) -> str:
    """scheme://host of a redirect's Location — never its path or query."""
    parts = urlsplit(resp.headers.get("location") or "")
    if parts.scheme and parts.hostname:
        return f"{parts.scheme}://{parts.hostname}"
    return ""


def normalize_site(raw: Any) -> str:
    """The origin (scheme://host[:port]) of a usable Owner Workspace address,
    or "" when the value is blank or unusable. HTTPS only; plain HTTP is
    accepted for a loopback development server and nothing else, because the
    device token is a bearer credential."""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ""
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        return ""
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        return ""
    if scheme != "https" and not (scheme == "http" and host in _LOCAL_HOSTS):
        return ""
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}" + (f":{port}" if port is not None else "")


def configured_site() -> str:
    """Where a NEW pairing goes: the Advanced override when it is usable,
    otherwise the Ridian site. An existing connection keeps its own site."""
    from . import settings_service  # lazy: keeps this module's imports small
    return (normalize_site(settings_service.load_settings().get("owner_workspace_url"))
            or DEFAULT_SITE)


def normalize_label(raw: Any) -> str:
    """Whitespace collapsed; "" when empty, too long, or holding a control
    character."""
    text = " ".join(str(raw or "").split())
    if not text or len(text) > LABEL_MAX_CHARS:
        return ""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return ""
    return text


def default_label() -> str:
    from . import companion_service  # lazy
    return normalize_label(str(companion_service.pc_name())[:LABEL_MAX_CHARS]) or "This PC"


# ---------------------------------------------------------------------------
# The connection store: <data_dir>/owner_workspace.json
# ---------------------------------------------------------------------------

def _load() -> dict:
    with _store_lock:
        try:
            text = SYNC_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            log.warning("owner_sync.load_failed type=%s", type(exc).__name__)
            return {}
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            log.warning("owner_sync.load_failed type=JSONDecodeError")
            return {}
        return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    with _store_lock:
        guard_real_state_write(SYNC_PATH)
        SYNC_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SYNC_PATH.with_name("." + SYNC_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, SYNC_PATH)


def _update(expected_id: Optional[str], /, **fields: Any) -> bool:
    """Merge fields into the stored connection. With an expected_id, only
    while that id is still the stored connection_id: a push or refresh that
    finishes after a disconnect (or a reconnect) never rewrites the newer
    state. None applies unconditionally. Positional-only, so a caller can
    clear the stored connection_id field itself."""
    with _store_lock:
        data = _load()
        if expected_id is not None and data.get("connection_id", "") != expected_id:
            return False
        data.update(fields)
        _save(data)
        return True


def _seal(token: str) -> str:
    from . import dpapi
    return _SEAL_PREFIX + base64.b64encode(dpapi.protect(token.encode("utf-8"))).decode("ascii")


def _unseal(stored: Any) -> str:
    """The plaintext token, or "". A missing, plaintext, or undecryptable
    value never authenticates anything: fail closed."""
    text = str(stored or "")
    if not text.startswith(_SEAL_PREFIX):
        return ""
    from . import dpapi
    try:
        return dpapi.unprotect(base64.b64decode(text[len(_SEAL_PREFIX):])).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 — another Windows account, a corrupt file
        log.warning("owner_sync.token_unreadable type=%s", type(exc).__name__)
        return ""


def _connect(site: str, label: str, token: str, expires: Optional[_dt.datetime],
             *, via: str) -> str:
    connection_id = secrets.token_hex(8)
    with _store_lock:
        _save({
            "version": 1,
            "status": "connected",
            "connection_id": connection_id,
            "site": site,
            "label": label,
            "paired_via": via,
            "device_token": _seal(token),
            "token_expires_iso": _iso(expires),
            "connected_iso": _iso(_utcnow()),
            "last_attempt_iso": "",
            "last_success_iso": "",
            "last_result": "",
            "last_error": "",
            "last_snapshot_id": "",
            "last_pushed_content_hash": "",
            "last_checked_iso": "",
            "refresh_unsupported_until_iso": "",
            "disconnected_reason": "",
            "disconnected_iso": "",
            "remote_revoked": False,
        })
    return connection_id


def _disconnect_locally(connection_id: Optional[str], reason: str) -> bool:
    """Discard the token and mark the connection disconnected. Used for a
    dead token (401), a token past its expiry, and an unreadable token."""
    now = _iso(_utcnow())
    return _update(connection_id, status="disconnected", device_token="",
                   token_expires_iso="", connection_id="", disconnected_reason=reason,
                   disconnected_iso=now, last_attempt_iso=now, last_result=reason,
                   last_error="", remote_revoked=False)


def is_connected() -> bool:
    data = _load()
    return data.get("status") == "connected" and bool(data.get("device_token"))


def status_view() -> dict:
    """What Settings and the rail line show. An explicit field list: the
    device token is never part of it."""
    data = _load()
    connected = data.get("status") == "connected" and bool(data.get("device_token"))
    engine = _engine
    if connected:
        state = "connected"
    elif data.get("status"):
        state = "disconnected"
    else:
        state = "not_connected"
    return {
        "connected": connected,
        "status": state,
        "label": str(data.get("label") or ""),
        "site": str(data.get("site") or ""),
        "configured_site": configured_site(),
        "default_site": DEFAULT_SITE,
        "paired_via": str(data.get("paired_via") or ""),
        "connected_iso": str(data.get("connected_iso") or ""),
        "token_expires_iso": str(data.get("token_expires_iso") or "") if connected else "",
        "last_attempt_iso": str(data.get("last_attempt_iso") or ""),
        "last_success_iso": str(data.get("last_success_iso") or ""),
        "last_result": str(data.get("last_result") or ""),
        "last_error": str(data.get("last_error") or ""),
        "last_checked_iso": str(data.get("last_checked_iso") or ""),
        # The site holds this PC's current state: the last attempt was sent
        # and accepted, or found nothing new to send (all three clear last_error).
        "up_to_date": connected and str(data.get("last_result") or "") in (
            "accepted", "duplicate", "unchanged"),
        "disconnected_reason": str(data.get("disconnected_reason") or ""),
        "disconnected_iso": str(data.get("disconnected_iso") or ""),
        "remote_revoked": bool(data.get("remote_revoked")),
        "default_label": default_label(),
        "next_attempt_iso": engine.next_attempt_iso() if (engine is not None and connected) else "",
        "sync_running": engine is not None,
        "pairing": pairing_view(),
    }


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

def _content_hash(document: Any) -> str:
    """sha256 of the canonical document WITHOUT generatedAt and
    source.localUtcOffset, the two fields that change on every export even
    when nothing else did. The same rule the site uses to spot duplicates."""
    copy = json.loads(json.dumps(document))
    if isinstance(copy, dict):
        copy.pop("generatedAt", None)
        if isinstance(copy.get("source"), dict):
            copy["source"].pop("localUtcOffset", None)
    canonical = json.dumps(copy, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_body() -> tuple[bytes, str]:
    """(body, content hash): the Owner Snapshot v1 document exactly as the
    exporter builds it, compact-encoded. SyncError when the exporter's own
    policy check refuses it or it is over the site's size cap."""
    from . import owner_snapshot_service  # lazy: it imports half the services
    try:
        document = owner_snapshot_service.build_snapshot()
    except owner_snapshot_service.SnapshotPolicyError as exc:
        # Policy messages name a field or a JSON path, never a value.
        raise SyncError(f"The snapshot was refused by its own policy check ({exc}).",
                        code="policy_refused") from exc
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise SyncError(f"The snapshot is {len(body)} bytes; the Owner Workspace accepts "
                        f"at most {MAX_BODY_BYTES}.", code="too_large")
    return body, _content_hash(document)


def _issued(resp: httpx.Response) -> tuple[str, Optional[_dt.datetime]]:
    """(token, expiry) from a pair or refresh answer; ("", None) when the
    answer carries no usable token."""
    data = _json(resp)
    token = str(data.get("token") or "").strip()
    if not TOKEN_RE.match(token):
        return "", None
    return token, _parse_iso(data.get("expiresAt"))


def _retry_after(resp: httpx.Response) -> float:
    raw = resp.headers.get("retry-after") or _json(resp).get("retryAfterSeconds")
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        seconds = 0.0
    return max(BACKOFF_BASE_SECONDS, min(RETRY_AFTER_CAP_SECONDS, seconds))


def _record_push(connection_id: str, site: str, resp: httpx.Response,
                 reasons: list, content_hash: str = "") -> tuple[str, Optional[float]]:
    """Store what a push answer means. Returns (result, backoff): backoff
    None clears any backoff, 0.0 backs off on the exponential schedule, and
    a positive value backs off at least that many seconds."""
    now = _iso(_utcnow())
    host = _host(site)
    status = resp.status_code
    if status == 200:
        data = _json(resp)
        result = "duplicate" if data.get("duplicate") else "accepted"
        snapshot_id = str(data.get("snapshotId") or "")[:64]
        _update(connection_id, last_attempt_iso=now, last_success_iso=now, last_checked_iso=now,
                last_result=result, last_error="", last_snapshot_id=snapshot_id,
                last_pushed_content_hash=content_hash)
        log.info("owner_sync.pushed result=%s snapshot=%s sha256=%s bytes=%s reasons=%s",
                 result, snapshot_id, str(data.get("sha256") or "")[:12], data.get("bytes"),
                 ",".join(reasons) or "-")
        return result, None
    if status == 401:
        _disconnect_locally(connection_id, "unauthorized")
        log.warning("owner_sync.unauthorized host=%s — the site refused this device's token; "
                    "it was discarded and nothing will be sent until you reconnect", host)
        return "unauthorized", None
    if status == 429:
        wait = _retry_after(resp)
        _update(connection_id, last_attempt_iso=now, last_result="rate_limited",
                last_error=("The Owner Workspace is rate limiting this device. Ridian will "
                            "try again after the limit resets."))
        log.info("owner_sync.rate_limited host=%s retry_after=%s", host, int(wait))
        return "rate_limited", wait
    if status in (400, 413, 415, 422):
        data = _json(resp)
        reason = str(data.get("reason") or data.get("error") or f"HTTP {status}")[:60]
        where = str(data.get("detail") or "")[:120]
        text = f"The Owner Workspace refused the snapshot ({reason}" + (f" at {where}" if where else "") + ")."
        _update(connection_id, last_attempt_iso=now, last_result="rejected", last_error=text)
        log.warning("owner_sync.rejected status=%s reason=%s detail=%s", status, reason, where)
        return "rejected", 0.0
    if 300 <= status < 400:
        target = _redirect_target(resp)
        text = (f"{host} redirected this device (HTTP {status})" + (f" to {target}" if target else "")
                + "; the token is never sent through a redirect. Set the Owner Workspace site under "
                "Advanced to the address the site uses, then disconnect and connect again.")
        _update(connection_id, last_attempt_iso=now, last_result="redirected", last_error=text)
        log.warning("owner_sync.redirected status=%s target=%s", status, target or "-")
        return "redirected", 0.0
    _update(connection_id, last_attempt_iso=now, last_result="server_error",
            last_error=f"The Owner Workspace answered HTTP {status}. Ridian will try again later.")
    log.warning("owner_sync.server_error host=%s status=%s", host, status)
    return "server_error", 0.0


def _maybe_refresh(client: httpx.Client, data: dict, token: str) -> str:
    """Rotate a token that is within REFRESH_WINDOW of its known expiry.
    Returns the token to push with: the new one, the unchanged one when the
    site cannot refresh right now, or "" when the site says it is dead."""
    connection_id = str(data.get("connection_id") or "")
    site = str(data.get("site") or "")
    now = _utcnow()
    not_before = _parse_iso(data.get("refresh_unsupported_until_iso"))
    if not_before is not None and now < not_before:
        return token
    try:
        resp = client.post(site + REFRESH_PATH, headers=_bearer(token), json={})
    except httpx.HTTPError as exc:
        log.warning("owner_sync.refresh_unreachable type=%s", type(exc).__name__)
        return token
    if resp.status_code == 200:
        fresh, expires = _issued(resp)
        if not fresh:
            log.warning("owner_sync.refresh_without_token")
            return token
        _update(connection_id, device_token=_seal(fresh), token_expires_iso=_iso(expires),
                refreshed_iso=_iso(now))
        log.info("owner_sync.token_refreshed expires=%s", _iso(expires) or "unknown")
        return fresh
    if resp.status_code == 401:
        _disconnect_locally(connection_id, "unauthorized")
        log.warning("owner_sync.unauthorized_on_refresh — the token was discarded")
        return ""
    if resp.status_code in _UNAVAILABLE:
        _update(connection_id, refresh_unsupported_until_iso=_iso(now + REFRESH_UNSUPPORTED_RETRY))
        log.info("owner_sync.refresh_unsupported status=%s — asking again in a day",
                 resp.status_code)
        return token
    log.warning("owner_sync.refresh_failed status=%s", resp.status_code)
    return token


def push_now(reasons: Optional[list] = None) -> tuple[str, Optional[float]]:
    """One push attempt with the stored connection; see _record_push for the
    return value. Sends nothing unless connected."""
    reasons = list(reasons or [])
    data = _load()
    if data.get("status") != "connected":
        return "not_connected", None
    connection_id = str(data.get("connection_id") or "")
    site = str(data.get("site") or "")
    token = _unseal(data.get("device_token"))
    if not token or not site or not connection_id:
        _disconnect_locally(connection_id or None, "unreadable")
        log.warning("owner_sync.token_unusable — disconnected; connect again in Settings")
        return "disconnected", None
    now = _utcnow()
    expires = _parse_iso(data.get("token_expires_iso"))
    if expires is not None and now >= expires:
        _disconnect_locally(connection_id, "expired")
        log.warning("owner_sync.token_expired host=%s — disconnected without sending it", _host(site))
        return "disconnected", None
    with _client() as client:
        if expires is not None and now >= expires - REFRESH_WINDOW:
            token = _maybe_refresh(client, data, token)
            if not token:
                return "unauthorized", None
        try:
            body, content_hash = _snapshot_body()
        except SyncError as exc:
            _update(connection_id, last_attempt_iso=_iso(now), last_result=exc.code or "refused",
                    last_error=exc.detail)
            log.warning("owner_sync.snapshot_refused code=%s", exc.code or "refused")
            return exc.code or "refused", 0.0
        if content_hash and content_hash == data.get("last_pushed_content_hash"):
            # Rule 6: the site already holds exactly this content.
            _update(connection_id, last_checked_iso=_iso(now), last_result="unchanged", last_error="")
            log.info("owner_sync.unchanged reasons=%s", ",".join(reasons) or "-")
            return "unchanged", None
        try:
            resp = client.post(site + PUSH_PATH, content=body,
                               headers={**_bearer(token), "Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            _update(connection_id, last_attempt_iso=_iso(now), last_result="network_error",
                    last_error=(f"Could not reach {_host(site)} ({type(exc).__name__}). "
                                "Ridian will try again later."))
            log.warning("owner_sync.network_error host=%s type=%s", _host(site), type(exc).__name__)
            return "network_error", 0.0
    return _record_push(connection_id, site, resp, reasons, content_hash)


# ---------------------------------------------------------------------------
# Pairing and disconnecting (loopback routes only)
# ---------------------------------------------------------------------------

def pair(code: str, label: str = "") -> dict:
    """Connect this PC. Saves the device token only after the site has
    accepted it; returns status_view(). SyncError on every refusal."""
    code = str(code or "").strip()
    if not CODE_RE.match(code):
        raise SyncError("Paste the pairing code exactly as the Owner Workspace shows it "
                        "(letters, digits, - and _).")
    wanted = str(label or "").strip()
    label = normalize_label(wanted) if wanted else default_label()
    if not label:
        raise SyncError(f"The device label must be 1 to {LABEL_MAX_CHARS} characters.")
    current = _load()
    if current.get("status") == "connected" and current.get("device_token"):
        raise SyncError(f"This PC is already connected as {current.get('label') or 'this PC'}. "
                        "Disconnect first.", status=409)
    site = configured_site()
    host = _host(site)
    with _client() as client:
        try:
            resp = client.post(site + PAIR_PATH, headers=_bearer(code), json={"label": label})
        except httpx.HTTPError as exc:
            raise SyncError(f"Could not reach {host} ({type(exc).__name__}). Check the connection "
                            "and try again.", status=502) from exc
        if resp.status_code == 200:
            token, expires = _issued(resp)
            if not token:
                raise SyncError(f"{host} answered the pairing without a usable token. "
                                "Nothing was saved.", status=502)
            _connect(site, label, token, expires, via="pairing")
            log.info("owner_sync.paired host=%s via=pairing expires=%s", host,
                     _iso(expires) or "unknown")
            notify("paired")
            return status_view()
        if resp.status_code in (400, 401):
            raise SyncError(f"{host} did not accept that pairing code. Create a new one on the "
                            "Owner Workspace and try again.")
        if 300 <= resp.status_code < 400:
            target = _redirect_target(resp)
            raise SyncError(f"{host} redirected the pairing (HTTP {resp.status_code})"
                            + (f" to {target}" if target else "")
                            + ". Set the Owner Workspace site under Advanced to the address the "
                            "site uses, save, and connect again.", status=502)
        if resp.status_code not in _UNAVAILABLE:
            raise SyncError(f"{host} could not pair right now (HTTP {resp.status_code}). "
                            "Try again in a minute.", status=502)
        return _pair_with_device_token(client, site, code, label)


def _pair_with_device_token(client: httpx.Client, site: str, code: str, label: str) -> dict:
    """The site has no pairing exchange: the pasted code is the device token
    itself. One real push verifies it; nothing is saved unless the site
    authenticated it."""
    host = _host(site)
    body, content_hash = _snapshot_body()   # a SyncError here saves nothing
    try:
        resp = client.post(site + PUSH_PATH, content=body,
                           headers={**_bearer(code), "Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise SyncError(f"Could not reach {host} ({type(exc).__name__}). Nothing was saved.",
                        status=502) from exc
    if resp.status_code == 401:
        raise SyncError(f"{host} did not accept that code. On the Owner Workspace open Device "
                        "tokens, create a new token, and paste it here.")
    if resp.status_code not in _AUTHENTICATED:
        raise SyncError(f"{host} could not verify the code (HTTP {resp.status_code}). Nothing "
                        "was saved; try again in a minute.", status=502)
    connection_id = _connect(site, label, code, None, via="device_token")
    log.info("owner_sync.paired host=%s via=device_token verify_status=%s", host,
             resp.status_code)
    # The verifying push was a real sync: record it like any other.
    result, backoff = _record_push(connection_id, site, resp, ["paired"], content_hash)
    engine = _engine
    if engine is not None:
        engine.apply_result(result, backoff)
    return status_view()


# ---------------------------------------------------------------------------
# One-click pairing: browser approval (what Connect does)
# ---------------------------------------------------------------------------

class _RedactPollSecret(logging.Filter):
    """httpx logs every request line, URL included, at INFO; the poll URL
    carries the pairing request's polling secret in its query. Redact it
    before any handler sees the record."""

    _SECRET = re.compile(r"(\brequest=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — never break logging
            return True
        if AUTHORIZE_POLL_PATH in message and "request=" in message:
            record.msg = self._SECRET.sub(r"\1[redacted]", message)
            record.args = ()
        return True


logging.getLogger("httpx").addFilter(_RedactPollSecret())

_pairing_lock = threading.Lock()
_pairing: Optional[dict] = None
_pairing_sleep: Callable[[float], None] = time.sleep      # test seam
_pairing_clock: Callable[[], float] = time.monotonic      # test seam


def _spawn_pairing(pairing_id: str) -> None:
    threading.Thread(target=run_pairing, args=(pairing_id,), name="ridian-owner-pairing",
                     daemon=True).start()


def pairing_view() -> Optional[dict]:
    """The public side of the current browser pairing. Never the polling secret."""
    with _pairing_lock:
        p = _pairing
        if p is None:
            return None
        return {"state": p["state"], "userCode": p["user_code"], "verifyUrl": p["verify_url"],
                "expiresAt": p["expires_iso"], "label": p["label"], "detail": p["detail"]}


def _set_pairing(pairing_id: str, **fields: Any) -> bool:
    with _pairing_lock:
        if _pairing is None or _pairing["id"] != pairing_id:
            return False
        _pairing.update(fields)
        return True


def _pairing_waiting(pairing_id: str) -> bool:
    with _pairing_lock:
        return _pairing is not None and _pairing["id"] == pairing_id and _pairing["state"] == "waiting"


def _clamp_interval(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = POLL_INTERVAL_DEFAULT
    return max(POLL_INTERVAL_MIN, min(POLL_INTERVAL_MAX, seconds))


def _valid_verify_url(url: str, site: str) -> bool:
    """The approval page on the SAME site, and nothing else: a pairing answer
    can never make this app open an arbitrary address."""
    try:
        parts, home = urlsplit(url or ""), urlsplit(site)
        same_port = parts.port == home.port
    except ValueError:
        return False
    return (parts.scheme == home.scheme and (parts.hostname or "") == (home.hostname or "")
            and same_port and parts.path == APPROVE_PATH and not parts.username
            and not parts.password and not parts.fragment and bool(parts.query))


def start_browser_pairing(label: str = "") -> dict:
    """Connect: ask the site for a pairing request and return what the
    renderer needs to open the approval page and show the code. The polling
    secret stays in this process's memory; a background thread polls until
    the token arrives, the owner denies, or the request expires (10 minutes
    at most). A newer Connect supersedes an older one."""
    global _pairing
    wanted = str(label or "").strip()
    label = normalize_label(wanted) if wanted else default_label()
    if not label:
        raise SyncError(f"The device label must be 1 to {LABEL_MAX_CHARS} characters.")
    current = _load()
    if current.get("status") == "connected" and current.get("device_token"):
        raise SyncError(f"This PC is already connected as {current.get('label') or 'this PC'}. "
                        "Disconnect first.", status=409)
    site = configured_site()
    host = _host(site)
    try:
        with _client() as client:
            resp = client.post(site + AUTHORIZE_START_PATH, json={"label": label})
    except httpx.HTTPError as exc:
        raise SyncError(f"Could not reach {host} ({type(exc).__name__}). Check the connection "
                        "and try again.", status=502) from exc
    if resp.status_code == 429:
        minutes = int((_retry_after(resp) + 59) // 60)
        raise SyncError(f"{host} is limiting connection attempts from this network. Try again in "
                        f"about {minutes} minute(s).", status=429)
    if resp.status_code in _UNAVAILABLE:
        raise SyncError(f"{host} does not offer browser approval. Use Connect with a token "
                        "under Advanced instead.", status=502)
    if resp.status_code == 400:
        raise SyncError(f"{host} did not accept the device label. Set a shorter one under "
                        "Advanced and try again.")
    if resp.status_code != 200:
        raise SyncError(f"{host} could not start pairing (HTTP {resp.status_code}). Try again "
                        "in a minute.", status=502)
    data = _json(resp)
    request_id = str(data.get("requestId") or "")
    user_code = str(data.get("userCode") or "")
    verify_url = str(data.get("verifyUrl") or "")
    expires = _parse_iso(data.get("expiresAt"))
    if not (TOKEN_RE.match(request_id) and USER_CODE_RE.match(user_code)
            and _valid_verify_url(verify_url, site)):
        raise SyncError(f"{host} answered with a pairing request this app does not recognise. "
                        "Nothing was opened.", status=502)
    started = _pairing_clock()
    deadline = started + PAIRING_MAX_SECONDS
    if expires is not None:
        deadline = min(deadline, started + max(0.0, (expires - _utcnow()).total_seconds()))
    pairing_id = secrets.token_hex(8)
    with _pairing_lock:
        _pairing = {"id": pairing_id, "state": "waiting", "request_id": request_id,
                    "user_code": user_code, "verify_url": verify_url,
                    "expires_iso": _iso(expires), "label": label, "site": site,
                    "interval": _clamp_interval(data.get("interval")), "deadline": deadline,
                    "detail": ""}
    log.info("owner_sync.pairing_started host=%s", host)
    _spawn_pairing(pairing_id)
    return pairing_view() or {}


def cancel_browser_pairing() -> Optional[dict]:
    with _pairing_lock:
        if _pairing is not None and _pairing["state"] == "waiting":
            _pairing["state"] = "cancelled"
            _pairing["detail"] = "Cancelled on this PC. Nothing was saved."
            log.info("owner_sync.pairing_cancelled")
    return pairing_view()


def _revoke_quietly(site: str, token: str) -> bool:
    try:
        with _client(timeout=REVOKE_TIMEOUT) as client:
            resp = client.post(site + REVOKE_PATH, headers=_bearer(token), json={})
        return resp.status_code in (200, 204)
    except httpx.HTTPError:
        return False


def _poll_pairing_once(pairing_id: str, pending: dict) -> str:
    """One poll. Returns the pairing state, or "error" for a transient failure."""
    site = pending["site"]
    host = _host(site)
    try:
        with _client() as client:
            resp = client.get(site + AUTHORIZE_POLL_PATH, params={"request": pending["request_id"]})
    except httpx.HTTPError as exc:
        log.warning("owner_sync.pairing_poll_unreachable type=%s", type(exc).__name__)
        return "error"
    data = _json(resp)
    status = str(data.get("status") or "")
    if resp.status_code == 200 and status == "pending":
        _set_pairing(pairing_id, interval=_clamp_interval(data.get("interval", pending["interval"])))
        return "waiting"
    if resp.status_code == 200 and status == "denied":
        _set_pairing(pairing_id, state="denied", detail=f"The request was denied on {host}. Nothing was saved.")
        log.info("owner_sync.pairing_denied host=%s", host)
        return "denied"
    if resp.status_code == 200 and status == "approved":
        token, expires = _issued(resp)
        if not token:
            _set_pairing(pairing_id, state="failed",
                         detail=f"{host} approved the request but sent no usable token. Try again.")
            log.warning("owner_sync.pairing_without_token")
            return "failed"
        with _pairing_lock:
            claimed = (_pairing is not None and _pairing["id"] == pairing_id
                       and _pairing["state"] == "waiting" and not is_connected())
            if claimed:
                _pairing.update(state="approved", detail="Connected.")
        if not claimed:
            # Cancelled, superseded, or connected another way while the
            # approval was in flight: the site already issued this token, so
            # retire it there too rather than leave it live and unused.
            _revoke_quietly(site, token)
            log.info("owner_sync.pairing_token_discarded")
            return "cancelled"
        try:
            _connect(site, pending["label"], token, expires, via="browser")
        except Exception as exc:  # e.g. DPAPI unavailable: nothing half-saved
            _set_pairing(pairing_id, state="failed",
                         detail="The approval arrived but this PC could not store the token. Try again.")
            log.warning("owner_sync.pairing_store_failed type=%s", type(exc).__name__)
            _revoke_quietly(site, token)
            return "failed"
        log.info("owner_sync.paired host=%s via=browser expires=%s", host, _iso(expires) or "unknown")
        notify("paired")
        return "approved"
    if resp.status_code == 410:
        _set_pairing(pairing_id, state="expired",
                     detail="The approval request expired or was already used. Choose Connect to start again.")
        log.info("owner_sync.pairing_expired host=%s", host)
        return "expired"
    log.warning("owner_sync.pairing_poll_status status=%s", resp.status_code)
    return "error"


def run_pairing(pairing_id: str) -> str:
    """Polls until the pairing ends and returns its final state. Runs on its
    own thread in production; tests call it with a fake clock and sleep."""
    errors = 0
    while True:
        with _pairing_lock:
            pending = dict(_pairing) if _pairing is not None and _pairing["id"] == pairing_id else None
        if pending is None:
            return "superseded"
        if pending["state"] != "waiting":
            return pending["state"]
        if _pairing_clock() >= pending["deadline"]:
            _set_pairing(pairing_id, state="expired",
                         detail="The approval request expired. Choose Connect to start again.")
            log.info("owner_sync.pairing_expired")
            return "expired"
        _pairing_sleep(pending["interval"])
        if not _pairing_waiting(pairing_id):
            continue
        state = _poll_pairing_once(pairing_id, pending)
        if state == "error":
            errors += 1
            if errors >= PAIRING_ERROR_LIMIT:
                _set_pairing(pairing_id, state="failed",
                             detail=f"Could not hear back from {_host(pending['site'])}. Choose Connect to try again.")
                return "failed"
            continue
        errors = 0
        if state != "waiting":
            return state


def disconnect() -> dict:
    """Local revoke first (authoritative), then a best-effort remote revoke.
    Returns status_view() plus a human detail line."""
    with _store_lock:
        data = _load()
        token = _unseal(data.get("device_token"))
        site = str(data.get("site") or "")
        label = str(data.get("label") or "")
        was_connected = data.get("status") == "connected"
        if data:
            data.update(status="disconnected", device_token="", token_expires_iso="",
                        connection_id="", disconnected_reason="operator",
                        disconnected_iso=_iso(_utcnow()), remote_revoked=False)
            _save(data)
    remote = False
    if token and site:
        try:
            with _client(timeout=REVOKE_TIMEOUT) as client:
                resp = client.post(site + REVOKE_PATH, headers=_bearer(token), json={})
            remote = resp.status_code in (200, 204)
        except httpx.HTTPError:
            remote = False
        if remote:
            # Only while still disconnected (connection id ""), never over a
            # connection made in the meantime.
            _update("", remote_revoked=True)
    log.info("owner_sync.disconnected by=operator was_connected=%s remote_revoked=%s",
             was_connected, remote)
    host = _host(site) if site else "the Owner Workspace"
    if remote:
        detail = f"Disconnected. {host} revoked this device's token."
    elif token:
        detail = (f"Disconnected on this PC. {host} did not confirm the revoke, so revoke "
                  f"“{label or 'this PC'}” under Device tokens on the Owner Workspace "
                  "to be sure.")
    else:
        detail = "Disconnected."
    view = status_view()
    view["detail"] = detail
    view["remote_revoked"] = remote
    return view


# ---------------------------------------------------------------------------
# The engine: triggers, debounce, backoff, heartbeat
# ---------------------------------------------------------------------------

def _finished_ids(data: Any) -> set:
    return {str(op.get("id")) for op in (data or [])
            if isinstance(op, dict) and str(op.get("status") or "") in TERMINAL_STATUSES}


class SyncEngine:
    """Debounced, backed-off push scheduler. One per backend process
    (start_engine). Tests drive their own with a fake clock through
    attach() / arm() / tick() and never start the thread."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], _dt.datetime] = _utcnow) -> None:
        self._clock = clock
        self._wall = wall
        self._cond = threading.Condition()
        self._pending_since: Optional[float] = None
        self._due: Optional[float] = None
        self._reasons: set = set()
        self._backoff_until = 0.0
        self._failures = 0
        self._next_heartbeat: Optional[float] = None
        self._known_finished: Optional[set] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = False

    # -- triggers -----------------------------------------------------------

    def trigger(self, reason: str, delay: float = DEBOUNCE_SECONDS) -> None:
        with self._cond:
            self._trigger_locked(str(reason), delay, self._clock())
            self._cond.notify_all()

    def _trigger_locked(self, reason: str, delay: float, now: float) -> None:
        if self._pending_since is None or self._due is None:
            self._pending_since = now
            self._due = now + delay
        else:
            # Trailing debounce, capped so a steady trickle cannot starve it.
            self._due = min(max(self._due, now + delay),
                            self._pending_since + MAX_COALESCE_SECONDS)
        self._reasons.add(reason)

    def on_store_saved(self, name: str, data: Any) -> None:
        """state_store save listener (rule 2)."""
        if name in WATCHED_STORES:
            self.trigger(name)
        elif name == "operations" and self._newly_finished(data):
            self.trigger("operation_finished")

    def _newly_finished(self, data: Any) -> bool:
        current = _finished_ids(data)
        with self._cond:
            known = self._known_finished if self._known_finished is not None else set()
            self._known_finished = current
        return bool(current - known)

    # -- lifecycle ------------------------------------------------------------

    def attach(self) -> None:
        """Listen to state-store writes. The finished-run set is seeded first,
        so runs that ended before this process started never count."""
        try:
            seed = _finished_ids(state_store.load_list("operations"))
        except Exception:  # noqa: BLE001 — a bad store must not stop the backend
            seed = set()
        with self._cond:
            self._known_finished = seed
        state_store.add_save_listener(self.on_store_saved)

    def detach(self) -> None:
        state_store.remove_save_listener(self.on_store_saved)

    def arm(self) -> None:
        """Schedule the startup push and start the heartbeat."""
        with self._cond:
            now = self._clock()
            self._next_heartbeat = now + HEARTBEAT_SECONDS
            self._trigger_locked("startup", STARTUP_DELAY_SECONDS, now)
            self._cond.notify_all()

    def start(self) -> None:
        with self._cond:
            if self._thread is not None:
                return
            self._stopping = False
        self.attach()
        self.arm()
        thread = threading.Thread(target=self._run, name="ridian-owner-sync", daemon=True)
        with self._cond:
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.detach()
        with self._cond:
            self._stopping = True
            thread, self._thread = self._thread, None
            self._cond.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    return
                wake = self._next_wake_locked()
                wait = 60.0 if wake is None else wake - self._clock()
                if wait > 0:
                    self._cond.wait(min(wait, 60.0))
                    continue
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — the loop must outlive any bug
                log.exception("owner_sync.tick_failed")
                with self._cond:
                    self._cond.wait(5.0)

    # -- scheduling -----------------------------------------------------------

    def _next_wake_locked(self) -> Optional[float]:
        times = []
        if self._due is not None:
            times.append(max(self._due, self._backoff_until))
        if self._next_heartbeat is not None:
            times.append(self._next_heartbeat)
        return min(times) if times else None

    def next_wake(self) -> Optional[float]:
        with self._cond:
            return self._next_wake_locked()

    def next_attempt_iso(self) -> str:
        """When the pending push will go, as UTC Z; "" when none is pending."""
        with self._cond:
            if self._due is None:
                return ""
            delta = max(0.0, max(self._due, self._backoff_until) - self._clock())
        return _iso(self._wall() + _dt.timedelta(seconds=delta))

    def pending(self) -> bool:
        with self._cond:
            return self._due is not None

    def tick(self) -> Optional[str]:
        """Do whatever is due now. Returns the push result when a push ran."""
        with self._cond:
            now = self._clock()
            if self._next_heartbeat is not None and now >= self._next_heartbeat:
                self._next_heartbeat = now + HEARTBEAT_SECONDS
                self._trigger_locked("timer", DEBOUNCE_SECONDS, now)
            if self._due is None or now < max(self._due, self._backoff_until):
                return None
            reasons = sorted(self._reasons)
            self._pending_since = None
            self._due = None
            self._reasons = set()
        try:
            result, backoff = push_now(reasons)
        except Exception:  # noqa: BLE001 — back off instead of crashing the loop
            log.exception("owner_sync.push_failed")
            result, backoff = "error", 0.0
        with self._cond:
            self._apply_locked(backoff)
            self._next_heartbeat = self._clock() + HEARTBEAT_SECONDS
        return result

    def apply_result(self, result: str, backoff: Optional[float]) -> None:
        """For a push made outside tick() (the pairing verification)."""
        with self._cond:
            self._apply_locked(backoff)

    def _apply_locked(self, backoff: Optional[float]) -> None:
        if backoff is None:
            self._failures = 0
            self._backoff_until = 0.0
            return
        self._failures = min(self._failures + 1, 10)
        wait = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (self._failures - 1)))
        self._backoff_until = self._clock() + max(wait, backoff)


# ---------------------------------------------------------------------------
# The process-wide engine
# ---------------------------------------------------------------------------

_engine: Optional[SyncEngine] = None
_engine_lock = threading.Lock()


def start_engine() -> SyncEngine:
    """Called once from the app lifespan. Inert until the owner connects."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = SyncEngine()
            _engine.start()
        return _engine


def stop_engine() -> None:
    global _engine
    with _engine_lock:
        engine, _engine = _engine, None
    if engine is not None:
        engine.stop()


def use_engine(engine: Optional[SyncEngine]) -> Optional[SyncEngine]:
    """Install an engine without starting its thread (tests). Returns the
    previous one."""
    global _engine
    with _engine_lock:
        previous, _engine = _engine, engine
    return previous


def notify(reason: str) -> None:
    engine = _engine
    if engine is not None:
        engine.trigger(reason)
