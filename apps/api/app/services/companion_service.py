"""Phone companion access (v6.9) — LAN exposure behind explicit pairing.

THREAT MODEL, stated plainly: enabling the companion binds the backend to
the LAN, which turns every endpoint into a network surface. The rules:

  1. OFF BY DEFAULT. With companion disabled the backend binds loopback
     only, and even if something else exposed the port, the middleware
     refuses every non-loopback request outright.
  2. LOOPBACK IS THE DESKTOP. Requests from 127.0.0.1/::1 are the local
     app and bypass companion auth entirely — the desktop's behavior is
     byte-identical whether the companion is on or off.
  3. PAIRING IS OPERATOR-INITIATED. A short-lived single-use code is
     generated from the desktop Settings view (loopback-only endpoint),
     read off the PC screen, and typed into the phone. Five wrong
     attempts invalidate the code and lock pairing; only the operator
     regenerating (again loopback-only) unlocks it.
  4. THE PHONE HOLDS A TOKEN, THE PC HOLDS A HASH. Pairing exchanges the
     code for a long random device token, returned once and stored only
     as a SHA-256 hash — the state store never contains a usable
     credential.
  5. DENY BY DEFAULT. A paired phone may reach ONLY the companion
     allowlist (brief, obligations, approvals, operations). Settings,
     snapshots/restore, and OAuth connect/disconnect surfaces stay
     loopback-only even with a valid token. The allowlist lives in
     main.py next to the middleware and is pinned by test.

Pairing codes live in MEMORY only: codes die with the process on purpose.
Per-device last-seen is memory-FIRST (fresh, per request) with a
day-throttled persisted copy (v6.9.10): every state_store.save snapshots
first, so a per-request write would churn backups — the record is written
at most once per device per 24h, which is enough to tell three same-named
pairings apart across restarts and to prune the dead ones.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import logging
import re
import secrets
import socket
import time
import uuid
from hmac import compare_digest
from typing import Optional

from . import state_store
from .memory_service import _stamp

log = logging.getLogger("ridian.companion")

_STORE = "companion_devices"

CODE_TTL_SECONDS = 600          # a pairing code lives 10 minutes
MAX_CODE_ATTEMPTS = 5           # then the code dies and pairing locks
LOCKOUT_SECONDS = 900           # until the operator regenerates, or 15 min
# Unambiguous alphabet (no 0/O/1/I/L) — the code is read off a screen.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 8

# In-memory pairing state — deliberately not persisted.
_active_code: Optional[str] = None
_code_expires_at: float = 0.0
_failed_attempts: int = 0
_locked_until: float = 0.0
_last_seen: dict = {}           # device id -> unix ts, memory only


class CompanionError(ValueError):
    """Deterministic refusal — raised before any write happens."""


def _now() -> float:
    return time.time()


def reset_pairing_state() -> None:
    """Test hook + process init: forget codes, attempts, lockout."""
    global _active_code, _code_expires_at, _failed_attempts, _locked_until
    _active_code = None
    _code_expires_at = 0.0
    _failed_attempts = 0
    _locked_until = 0.0
    _last_seen.clear()


def generate_pairing_code() -> dict:
    """Operator action (loopback-only endpoint). Regenerating replaces any
    active code and — because it is an explicit operator action on the PC —
    clears a lockout."""
    global _active_code, _code_expires_at, _failed_attempts, _locked_until
    prune_stale_devices()                    # operator moment; already writes
    _active_code = "".join(secrets.choice(_CODE_ALPHABET)
                           for _ in range(_CODE_LENGTH))
    _code_expires_at = _now() + CODE_TTL_SECONDS
    _failed_attempts = 0
    _locked_until = 0.0
    log.info("companion.pairing_code_generated")
    return {"code": _active_code,
            "expires_iso": _dt.datetime.fromtimestamp(
                _code_expires_at).isoformat(timespec="seconds"),
            "ttl_seconds": CODE_TTL_SECONDS}


def pairing_locked() -> bool:
    return _now() < _locked_until


def pair(code: str, device_name: str = "") -> dict:
    """Exchange a pairing code for a device token. The RAW token appears
    only in this return value; the store keeps its SHA-256."""
    global _active_code, _failed_attempts, _locked_until
    if pairing_locked():
        raise CompanionError("pairing is locked after too many wrong codes — "
                             "generate a new code on the PC to try again.")
    if not _active_code or _now() > _code_expires_at:
        raise CompanionError("no active pairing code — generate one in "
                             "Settings on the PC first.")
    supplied = str(code or "").strip().upper()
    # compare_digest raises TypeError on non-ASCII str operands; a pasted
    # emoji must count as a wrong code, not a 500.
    if not supplied.isascii() or not compare_digest(supplied, _active_code):
        _failed_attempts += 1
        if _failed_attempts >= MAX_CODE_ATTEMPTS:
            _active_code = None
            _locked_until = _now() + LOCKOUT_SECONDS
            log.warning("companion.pairing_locked attempts=%s", _failed_attempts)
            raise CompanionError("too many wrong codes — pairing is locked. "
                                 "Generate a new code on the PC.")
        raise CompanionError("wrong pairing code.")
    _active_code = None                      # single-use
    prune_stale_devices()                    # pairing writes anyway
    token = secrets.token_urlsafe(32)
    entry = {
        "id": "cd_" + uuid.uuid4().hex[:12],
        "name": str(device_name or "").strip()[:60] or "Companion device",
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "created_iso": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    _stamp(entry, "companion-pairing", "")
    items = state_store.load_list(_STORE)
    items.insert(0, entry)
    state_store.save(_STORE, items)
    log.info("companion.device_paired id=%s", entry["id"])
    return {"device_id": entry["id"], "name": entry["name"], "token": token}


# Persist a device's last-seen at most this often — one write (and one
# snapshot) per device per day, never per request.
_LAST_SEEN_PERSIST_HOURS = 24
# Prune pairings unseen this long. The companion cookie's max-age is 30
# days, so a 45-days-unseen pairing's credential expired two weeks ago —
# removing the record costs no working access, it only clears clutter
# (and dead hashes). Re-pairing costs one code.
PRUNE_UNSEEN_DAYS = 45


def verify_token(raw_token: str) -> Optional[dict]:
    """Device record for a valid token, else None. Last-seen: memory
    always; persisted onto the record at most once per 24h so same-named
    pairings stay distinguishable across restarts."""
    if not raw_token:
        return None
    digest = hashlib.sha256(str(raw_token).encode()).hexdigest()
    devices = state_store.load_list(_STORE)
    for device in devices:
        if compare_digest(digest, str(device.get("token_sha256") or "")):
            _last_seen[device.get("id")] = _now()
            now_dt = _dt.datetime.now()
            stale = True
            try:
                stale = (now_dt - _dt.datetime.fromisoformat(
                    device.get("last_seen_iso") or "")
                ) > _dt.timedelta(hours=_LAST_SEEN_PERSIST_HOURS)
            except ValueError:
                pass                          # absent/unparseable = stale
            if stale:
                device["last_seen_iso"] = now_dt.isoformat(timespec="seconds")
                state_store.save(_STORE, devices)
            return device
    return None


def list_devices() -> list:
    """For the Settings view — hashes are NOT included. last_seen prefers
    the in-memory stamp (fresh to the minute) and falls back to the
    day-granular persisted one, so three same-named pairings read apart."""
    out = []
    for device in state_store.load_list(_STORE):
        seen = _last_seen.get(device.get("id"))
        out.append({
            "id": device.get("id"),
            "name": device.get("name"),
            "created_iso": device.get("created_iso"),
            "last_seen_iso": (_dt.datetime.fromtimestamp(seen)
                              .isoformat(timespec="seconds") if seen
                              else (device.get("last_seen_iso") or "")),
        })
    return out


def prune_stale_devices(now: Optional[_dt.datetime] = None) -> int:
    """Remove pairings unseen for PRUNE_UNSEEN_DAYS+ (basis: persisted
    last-seen, else the pairing date; a device seen THIS session always
    survives; unparseable dates are kept — never guess-revoke). Called at
    the operator-action moments that already write: code generation and
    pairing. Returns how many were removed."""
    now = now or _dt.datetime.now()
    cutoff = now - _dt.timedelta(days=PRUNE_UNSEEN_DAYS)
    items = state_store.load_list(_STORE)
    kept: list[dict] = []
    removed = 0
    for device in items:
        basis = (device.get("last_seen_iso") or device.get("created_iso") or "")
        try:
            alive = _dt.datetime.fromisoformat(basis) > cutoff
        except ValueError:
            alive = True
        if device.get("id") in _last_seen:
            alive = True
        if alive:
            kept.append(device)
        else:
            removed += 1
            _last_seen.pop(device.get("id"), None)
            log.info("companion.device_pruned id=%s unseen_since=%s",
                     device.get("id"), basis)
    if removed:
        state_store.save(_STORE, kept)
    return removed


def revoke_device(device_id: str) -> bool:
    items = state_store.load_list(_STORE)
    kept = [d for d in items if d.get("id") != device_id]
    if len(kept) == len(items):
        return False
    state_store.save(_STORE, kept)
    _last_seen.pop(device_id, None)
    log.info("companion.device_revoked id=%s", device_id)
    return True


# ---------------------------------------------------------------------------
# Request gating (pure — consulted by the CompanionGate middleware)
# ---------------------------------------------------------------------------

# Reachable from off-box WITHOUT a device token: exactly the pairing
# surface. The page itself must load so it can show the pairing form.
_PREAUTH_ALLOWED = frozenset({
    ("GET", "/companion"),
    ("GET", "/companion/manifest.json"),
    ("POST", "/companion/pair"),
    ("GET", "/static/companion-icon-192.png"),
    ("GET", "/static/companion-icon-512.png"),
    ("GET", "/static/companion-icon-maskable.png"),
    ("GET", "/static/companion-apple-touch.png"),
})

# Reachable WITH a paired device token. DENY BY DEFAULT: everything not
# named here stays loopback-only — settings, snapshots/restore, OAuth
# connect/disconnect, memory/CRM, email send, file/audio streaming, native
# open-file, workflow runners, uploads. Expanding this list is a deliberate
# security decision, made here, pinned by test_companion_access.
_DEVICE_ALLOWED_EXACT = frozenset({
    ("GET", "/companion/me"),
    ("GET", "/morning-brief"),
    ("GET", "/obligations"),
    ("GET", "/approvals"),
    ("POST", "/approvals/answer"),
    ("POST", "/operations/run"),
    ("GET", "/operations/recent"),
    # v6.9.7 Web Push: the service worker script, and the paired device
    # registering/clearing ITS OWN push subscription (the endpoint reads the
    # device from the gate's scope — no device id is accepted from the body).
    ("GET", "/companion-sw.js"),
    ("POST", "/companion/push/subscribe"),
    ("POST", "/companion/push/unsubscribe"),
})
_OB_ACTION_RE = re.compile(r"^/obligations/[A-Za-z0-9_-]+/(complete|dismiss)$")
_OP_ACTION_RE = re.compile(r"^/operations/[A-Za-z0-9_-]+/(continue|dismiss|background)$")
# GET of one operation record — but never the reserved non-id routes that
# stream source text or audio from disk, or the PC's live run state (v7.7).
_OP_GET_RE = re.compile(r"^/operations/(?!recent$|load$|audio$|live$)[A-Za-z0-9_-]+$")


# Which STAGED APPROVAL KINDS a companion device may answer. Deny by
# default — the endpoint allowlist alone is NOT confinement, because
# /operations/run can make the planner stage any gate and /approvals/answer
# re-executes the staged tool. Destructive kinds (a full-state restore that
# overwrites contacts/deals/memory, contact merge/delete) stay on the PC no
# matter how they were staged. Pinned by test_companion_access.
_DEVICE_APPROVABLE_REASONS = frozenset({
    "invoice_plan_pending",       # the couch-approval use case
    "proposal_plan_pending",      # writes a document, non-destructive
    "research_plan_pending",      # approves spend, non-destructive
    "sms_send_pending",           # v7.0: one text to an allowlisted label, preview shown
})


def device_may_answer_approval(reason: str) -> bool:
    return str(reason or "") in _DEVICE_APPROVABLE_REASONS


def preauth_request_allowed(method: str, path: str) -> bool:
    return (method.upper(), path) in _PREAUTH_ALLOWED


def device_request_allowed(method: str, path: str) -> bool:
    method = method.upper()
    if (method, path) in _DEVICE_ALLOWED_EXACT:
        return True
    if preauth_request_allowed(method, path):
        return True
    if method == "POST" and (_OB_ACTION_RE.match(path)
                             or _OP_ACTION_RE.match(path)):
        return True
    if method == "GET" and _OP_GET_RE.match(path):
        return True
    return False


def client_is_local(host: Optional[str]) -> bool:
    """True for the desktop: a loopback TCP peer. ``None`` and the literal
    "testclient" occur only for in-process ASGI calls (starlette's
    TestClient default) — a real socket peer from uvicorn is always a real
    address, so treating them as local is production-safe."""
    if host is None or host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_ok(host_header: str) -> bool:
    """DNS-rebinding defense for off-box requests: the phone reaches the PC
    by literal IP, so a non-IP Host header (some DNS name an attacker's page
    resolved here) is refused outright. localhost forms are fine — those
    requests are loopback and never reach this check.

    v6.9.7 exception, exact-match only: the HTTPS listener serves at this
    machine's OWN ts.net name (Web Push needs a secure context), so that
    one hostname — known-good because companion_tls set it when the
    listener started — is admitted. An attacker's domain resolving to this
    PC still presents THEIR Host header and is still refused."""
    raw = str(host_header or "").strip()
    if not raw:
        return False
    if raw.startswith("["):                       # [v6]:port
        hostname = raw.partition("]")[0].lstrip("[")
    else:
        hostname = raw.partition(":")[0]
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        from . import companion_tls
        own = (companion_tls.state.get("host") or "").strip().lower()
        return bool(own) and hostname.lower() == own


def lan_listener_reachable(ip: str, port: int) -> bool:
    """Is the backend ACTUALLY listening on the LAN address right now?

    Probed, not inferred: the settings toggle only takes effect at the next
    start, and the dev launcher binds loopback regardless. A real TCP
    connect to our own LAN socket is the only honest answer."""
    if not ip:
        return False
    try:
        with socket.create_connection((ip, int(port)), timeout=0.4):
            return True
    except OSError:
        return False


_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")   # Tailscale's range


def tailnet_ip() -> str:
    """This machine's Tailscale address, DETECTED not assumed — empty when
    there is no tailnet. Two independent probes, both validated against the
    CGNAT range so a machine without Tailscale can never report a false one:
    enumerate the host's own addresses, then ask the routing table which
    source address reaches Tailscale's MagicDNS resolver (100.100.100.100).
    The UDP connect() sends no packet; it only resolves the route."""
    def _cgnat(addr: str) -> bool:
        try:
            return ipaddress.ip_address(addr) in _CGNAT_NET
        except ValueError:
            return False

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            addr = info[4][0]
            if _cgnat(addr):
                return addr
    except (OSError, socket.gaierror):
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.3)
            s.connect(("100.100.100.100", 9))
            addr = s.getsockname()[0]
            # Without a tailnet the OS answers with the default-route
            # address; the range check is what makes this honest.
            return addr if _cgnat(addr) else ""
        finally:
            s.close()
    except OSError:
        return ""


def pc_name() -> str:
    """A human label for THIS machine, for the phone's header — the phone is
    a window, and the window should say what it looks through to."""
    try:
        return socket.gethostname() or "this PC"
    except OSError:
        return "this PC"


def lan_ip() -> str:
    """The PC's LAN address, for the Settings view to display the companion
    URL. UDP connect() never sends a packet — it only resolves the route."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return ""
