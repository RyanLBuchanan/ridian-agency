"""Companion Web Push (v6.9.7) — ambient notifications, honestly delivered.

WHAT NOTIFIES (deny by default — a notification the operator learns to
ignore is worse than none): an obligation occurrence coming due, an
approval staging, a run parking on a question. Nothing else.

WHEN IT EVALUATES (item 3 — the obligations model, no cron, no timer):
  - backend startup, in a background thread: the catch-up. Whatever came
    due while the PC was off is noticed on next launch, never skipped.
  - the moment an approval stages / a run parks (event, not poll).
  - opportunistically on existing read traffic (obligations, brief,
    approvals, dashboard), throttled to one evaluation per 10 minutes.
  Consequence, stated plainly: on an idle-but-running PC an obligation
  that crosses midnight notifies at the next interaction or next launch —
  that is the price of "no live timer", chosen deliberately.

DEDUPLICATION (item 4 — the make-or-break): a persisted ledger
(state_store "companion_push_ledger", {occurrence_key: notified_iso}).
Occurrence keys are restart-stable:
  ob:<obligation_id>:<due_date>   one per occurrence; the next period is a
                                  new date hence a new key; N missed
                                  periods collapse into the CURRENT
                                  occurrence's single notification.
  appr:<approval_id>              unique per staging; a re-staged gate has
                                  a new id and re-notifies — correct, the
                                  payload changed.
  op:<operation_id>:q<n>          n = len(needs_input): one per park; an
                                  answer that re-parks extends the list —
                                  new key, new notification.
A key is marked ONLY after a push service accepted the message for at
least one subscribed device. Failure leaves it unmarked (the next
evaluation retries) and records last_error — surfaced in
/companion/status and the desktop Settings, never a silent no-op
(item 6). One batched ledger save per evaluation (every state_store.save
snapshots first, so per-notification writes would churn backups); entries
older than 60 days are pruned on write.

KEYS + SUBSCRIPTIONS (item 1): the VAPID private key is generated on this
PC and stored DPAPI-encrypted like the QuickBooks token — no plaintext
secret on disk, no key file to co-locate. Subscriptions live ON the
paired-device record in "companion_devices", so revoking the device
revokes its push subscription in the same breath.
"""

from __future__ import annotations

import base64
import datetime as _dt
import ipaddress
import json
import logging
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from . import dpapi, settings_service, state_store
from .runtime_paths import data_dir, guard_real_state_write

log = logging.getLogger("ridian.push")

_LEDGER_STORE = "companion_push_ledger"
_DEVICES_STORE = "companion_devices"
_DPAPI_MAGIC = b"RIDIAN-DPAPI-1\n"
VAPID_PATH = data_dir() / "companion_push_vapid.bin"

# Contact claim for the push services (RFC 8292). A role address, not the
# operator's personal one — this value is sent to Google/Mozilla/Apple.
_VAPID_SUB = "mailto:companion@ridiantechnologies.com"

_SEND_TIMEOUT_SECONDS = 10       # a push POST that can hang is the Brief bug again
_TTL_SECONDS = 24 * 3600         # phone offline overnight still gets it
_EVAL_MIN_INTERVAL = 600         # opportunistic evaluations at most every 10 min
_LEDGER_KEEP_DAYS = 60

_ledger_lock = threading.Lock()  # ledger read-modify-write is atomic
_eval_lock = threading.Lock()
_last_eval_ts = 0.0
# Keys claimed by an in-flight send. Candidates are gathered and sent
# OUTSIDE the ledger lock (v6.9.8 — the watch tier reaches QuickBooks and
# Gmail, and an approval/park event push must never queue behind a sick
# network connection holding the lock); the claim set is what keeps a
# concurrent evaluation from double-sending the same key meanwhile.
_inflight: set = set()

# In-memory, deliberately: repopulated within seconds of every launch by the
# startup evaluation, so it is honest without churning state snapshots.
_state = {"last_error": "", "last_success_iso": "", "last_eval_iso": ""}


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def enabled() -> bool:
    return settings_service.get_bool_setting("companion_push_enabled",
                                             default=False)


# ---------------------------------------------------------------------------
# VAPID keys — generated on the PC, private key DPAPI-encrypted at rest
# ---------------------------------------------------------------------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ensure_vapid() -> str:
    """Create the keypair if missing; return the public application-server
    key (urlsafe b64 of the uncompressed P-256 point). Raises DpapiError /
    OSError honestly — callers surface, never swallow into success."""
    if not VAPID_PATH.exists():
        vapid = Vapid02()
        vapid.generate_keys()
        guard_real_state_write(VAPID_PATH)
        VAPID_PATH.parent.mkdir(parents=True, exist_ok=True)
        VAPID_PATH.write_bytes(_DPAPI_MAGIC + dpapi.protect(vapid.private_pem()))
        log.info("push.vapid_generated")
    return public_key()


def _load_vapid() -> Optional[Vapid02]:
    if not VAPID_PATH.exists():
        return None
    raw = VAPID_PATH.read_bytes()
    if not raw.startswith(_DPAPI_MAGIC):
        raise dpapi.DpapiError("VAPID key file is not DPAPI-wrapped.")
    return Vapid02.from_pem(dpapi.unprotect(raw[len(_DPAPI_MAGIC):]))


def public_key() -> str:
    """Application-server key for pushManager.subscribe, "" when absent."""
    try:
        vapid = _load_vapid()
    except dpapi.DpapiError as exc:
        _state["last_error"] = str(exc)
        return ""
    if vapid is None:
        return ""
    return _b64url(vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint))


def vapid_ready() -> bool:
    return bool(public_key())


# ---------------------------------------------------------------------------
# Subscriptions — stored ON the paired-device record (revoked with it)
# ---------------------------------------------------------------------------

class SubscriptionError(ValueError):
    """Deterministic refusal of a malformed/unsafe subscription."""


def _validated(subscription: dict) -> dict:
    endpoint = str((subscription or {}).get("endpoint") or "")
    if not endpoint.startswith("https://") or len(endpoint) > 1024:
        raise SubscriptionError("push endpoint must be a short https:// URL.")
    host = urlsplit(endpoint).hostname or ""
    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if is_ip_literal:
        # The PC will POST to this URL. A paired phone is trusted, but an IP
        # literal is never a real push service — refuse the SSRF shape.
        raise SubscriptionError("push endpoint must name a push service, "
                                "not an IP address.")
    keys = (subscription or {}).get("keys") or {}
    p256dh = str(keys.get("p256dh") or "")
    auth = str(keys.get("auth") or "")
    if not (0 < len(p256dh) < 300 and 0 < len(auth) < 300):
        raise SubscriptionError("subscription keys are missing or malformed.")
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def save_subscription(device_id: str, subscription: dict) -> None:
    sub = _validated(subscription)
    devices = state_store.load_list(_DEVICES_STORE)
    for device in devices:
        if device.get("id") == device_id:
            device["push_subscription"] = sub
            device["push_subscribed_iso"] = _now_iso()
            state_store.save(_DEVICES_STORE, devices)
            log.info("push.subscribed device=%s", device_id)
            return
    raise SubscriptionError("unknown device.")


def drop_subscription(device_id: str) -> bool:
    devices = state_store.load_list(_DEVICES_STORE)
    for device in devices:
        if device.get("id") == device_id and device.get("push_subscription"):
            device.pop("push_subscription", None)
            device.pop("push_subscribed_iso", None)
            state_store.save(_DEVICES_STORE, devices)
            log.info("push.unsubscribed device=%s", device_id)
            return True
    return False


def subscribed_device_count() -> int:
    return sum(1 for d in state_store.load_list(_DEVICES_STORE)
               if d.get("push_subscription"))


def device_is_subscribed(device_id: str) -> bool:
    return any(d.get("id") == device_id and d.get("push_subscription")
               for d in state_store.load_list(_DEVICES_STORE))


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _send_to_subscriptions(payload: dict, tag: str) -> bool:
    """POST one notification to every subscribed device. True when at least
    one push service ACCEPTED it — the only condition that marks the
    ledger. 404/410 prunes that subscription (the phone re-subscribes on
    its next open); other failures are recorded, never swallowed."""
    devices = state_store.load_list(_DEVICES_STORE)
    subscribed = [d for d in devices if d.get("push_subscription")]
    if not subscribed:
        return False
    try:
        vapid = _load_vapid()
    except dpapi.DpapiError as exc:
        _state["last_error"] = str(exc)
        return False
    if vapid is None:
        _state["last_error"] = ("push is enabled but no VAPID key exists — "
                                "toggle the setting off and on to regenerate.")
        return False
    delivered = False
    pruned = False
    for device in subscribed:
        try:
            webpush(subscription_info=device["push_subscription"],
                    data=json.dumps({**payload, "tag": tag}),
                    vapid_private_key=vapid,
                    vapid_claims={"sub": _VAPID_SUB},
                    timeout=_SEND_TIMEOUT_SECONDS, ttl=_TTL_SECONDS)
            delivered = True
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # The subscription is dead at the push service. Prune it and
                # say so — the phone offers re-subscribe on next open.
                device.pop("push_subscription", None)
                device.pop("push_subscribed_iso", None)
                pruned = True
                _state["last_error"] = (f"{device.get('name')}'s subscription "
                                        "expired — open the companion on the "
                                        "phone to re-enable notifications.")
                log.warning("push.subscription_gone device=%s", device.get("id"))
            else:
                _state["last_error"] = f"push service refused ({exc})"[:300]
                log.warning("push.send_refused status=%s", status)
        except requests.RequestException as exc:
            _state["last_error"] = (f"push service unreachable "
                                    f"({type(exc).__name__})")
            log.warning("push.send_unreachable type=%s", type(exc).__name__)
    if pruned:
        state_store.save(_DEVICES_STORE, devices)
    if delivered:
        _state["last_success_iso"] = _now_iso()
        _state["last_error"] = ""
    return delivered


# ---------------------------------------------------------------------------
# Candidates — exactly the three notifiable kinds, nothing else
# ---------------------------------------------------------------------------

def _candidates(today: Optional[_dt.date] = None) -> list[tuple[str, dict]]:
    from . import approval_inbox_service, obligations_service

    out: list[tuple[str, dict]] = []
    for d in obligations_service.due_obligations(today=today):
        key = f"ob:{d['id']}:{d['due_date']}"
        if d.get("status") == "overdue":
            title = f"OVERDUE {d.get('days_overdue', 0)}d: {d.get('name', '')}"
        else:
            title = f"Due today: {d.get('name', '')}"
        body = str(d.get("task") or "")
        if d.get("missed_periods"):
            body = f"{body} · {d['missed_periods']} earlier missed".strip(" ·")
        out.append((key, {"title": title, "body": body, "tab": "due"}))
    for a in approval_inbox_service.list_pending():
        out.append((f"appr:{a.get('id')}", {
            "title": "Approval waiting",
            "body": str(a.get("question") or a.get("command") or ""),
            "tab": "approvals"}))
    for op in state_store.load_list("operations"):
        if op.get("status") != "awaiting_input":
            continue
        needs = op.get("needs_input") or []
        last = needs[-1] if needs else {}
        out.append((f"op:{op.get('id')}:q{len(needs)}", {
            "title": "Ridian is waiting on you",
            "body": str(last.get("question") or op.get("command") or ""),
            "tab": "task"}))
    return out


# ---------------------------------------------------------------------------
# Evaluation + event notifiers
# ---------------------------------------------------------------------------

def _prune(ledger: dict) -> dict:
    cutoff = (_dt.datetime.now()
              - _dt.timedelta(days=_LEDGER_KEEP_DAYS)).isoformat()
    return {k: v for k, v in ledger.items() if str(v) >= cutoff}


def watch_candidates(today: Optional[_dt.date] = None) -> list:
    """v6.9.8 ambient-watch tier. Network-touching, so ALWAYS called
    outside the ledger lock — and isolated: a sick source must never
    suppress the core obligation/approval/park notifications."""
    try:
        from . import watch_service
        return watch_service.push_candidates(today)
    except Exception:  # noqa: BLE001 — watch is never load-bearing
        log.warning("push.watch_candidates_failed", exc_info=True)
        return []


# More than this many fresh watch findings in one pass collapse into a
# single digest push — a cold start over aged books must not fire a burst
# of pings (the fastest way to teach the operator to ignore the channel).
_WATCH_DIGEST_THRESHOLD = 3


def _mark_accepted(keys: list) -> None:
    with _ledger_lock:
        ledger = state_store.load_dict(_LEDGER_STORE)
        now = _now_iso()
        for k in keys:
            ledger[k] = now
        state_store.save(_LEDGER_STORE, _prune(ledger))


def evaluate_and_push(today: Optional[_dt.date] = None) -> dict:
    """One pass: candidates minus ledger, send, mark what was ACCEPTED.
    Safe to call any time; a no-op when disabled or nothing is new.
    ``sent`` counts LEDGER KEYS marked (a digest marks several at once).

    Candidates are computed and sent OUTSIDE the ledger lock; the lock
    guards only the claim (diff vs ledger + inflight) and the mark. The
    ledger is pruned AT DIFF TIME, so a >60-day-old key re-notifies even
    if no save has happened since it aged out."""
    if not enabled():
        return {"sent": 0, "reason": "disabled"}
    core = _candidates(today)
    watch = watch_candidates(today)
    with _ledger_lock:
        ledger = _prune(state_store.load_dict(_LEDGER_STORE))
        claimed = [(k, p) for k, p in core + watch
                   if k not in ledger and k not in _inflight]
        _inflight.update(k for k, _p in claimed)
    accepted: list = []
    try:
        core_keys = {k for k, _p in core}
        fresh_core = [(k, p) for k, p in claimed if k in core_keys]
        fresh_watch = [(k, p) for k, p in claimed if k not in core_keys]
        for key, payload in fresh_core:
            if _send_to_subscriptions(payload, tag=key):
                accepted.append(key)
        if len(fresh_watch) > _WATCH_DIGEST_THRESHOLD:
            digest = {"title": f"Ridian noticed {len(fresh_watch)} things",
                      "body": " · ".join(p["title"] for _k, p in fresh_watch[:5]),
                      "tab": "due"}
            if _send_to_subscriptions(digest, tag="watch:digest"):
                accepted.extend(k for k, _p in fresh_watch)
        else:
            for key, payload in fresh_watch:
                if _send_to_subscriptions(payload, tag=key):
                    accepted.append(key)
    finally:
        if accepted:
            _mark_accepted(accepted)
        with _ledger_lock:
            _inflight.difference_update(k for k, _p in claimed)
    _state["last_eval_iso"] = _now_iso()
    log.info("push.evaluated attempted=%s sent=%s", len(claimed), len(accepted))
    return {"sent": len(accepted), "attempted": len(claimed)}


def _notify_one(key: str, payload: dict) -> None:
    if not enabled():
        return
    with _ledger_lock:
        ledger = _prune(state_store.load_dict(_LEDGER_STORE))
        if key in ledger or key in _inflight:
            return
        _inflight.add(key)
    try:
        if _send_to_subscriptions(payload, tag=key):
            _mark_accepted([key])
    finally:
        with _ledger_lock:
            _inflight.discard(key)


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def notify_approval_staged(entry: dict) -> None:
    """Event hook, called by stage_from_tool AFTER the save. Fire-and-forget
    in a thread: staging must never wait on (or fail with) a push."""
    _spawn(_notify_one, f"appr:{entry.get('id')}", {
        "title": "Approval waiting",
        "body": str(entry.get("question") or entry.get("command") or ""),
        "tab": "approvals"})


def notify_run_parked(snapshot: dict) -> None:
    """Event hook, called when a run persists as awaiting_input."""
    needs = snapshot.get("needs_input") or []
    last = needs[-1] if needs else {}
    _spawn(_notify_one, f"op:{snapshot.get('id')}:q{len(needs)}", {
        "title": "Ridian is waiting on you",
        "body": str(last.get("question") or snapshot.get("command") or ""),
        "tab": "task"})


def maybe_evaluate() -> None:
    """Opportunistic, throttled evaluation on existing read traffic — the
    'while the PC is running' half of the obligations model. No timer."""
    global _last_eval_ts
    if not enabled():
        return
    now = time.monotonic()
    with _eval_lock:
        if now - _last_eval_ts < _EVAL_MIN_INTERVAL:
            return
        _last_eval_ts = now
    _spawn(evaluate_and_push)


def startup_catch_up() -> None:
    """The launch half: whatever came due while the PC was off notifies
    now, once. Runs in a background thread so boot never waits on it."""
    global _last_eval_ts
    with _eval_lock:
        _last_eval_ts = time.monotonic()
    _spawn(evaluate_and_push)


def status() -> dict:
    """For /companion/status and the Settings view — the honest state."""
    return {
        "enabled": enabled(),
        "vapid_ready": vapid_ready(),
        "subscribed_devices": subscribed_device_count(),
        "last_success_iso": _state["last_success_iso"],
        "last_eval_iso": _state["last_eval_iso"],
        "last_error": _state["last_error"],
    }
