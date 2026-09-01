"""Companion Web Push (v6.9.7) — the contracts that make it trustworthy.

The three pins that matter most:
  1. DEDUPLICATION: one notification per OCCURRENCE, stable across
     restarts, re-evaluations, and overdue days — the ledger key is the
     occurrence, never the evaluation.
  2. MARK-ON-ACCEPTANCE: a key enters the ledger only after a push service
     accepted the message; failure leaves it unmarked so the next
     evaluation retries, and the error is recorded — never a silent no-op.
  3. AT-REST: the VAPID private key is DPAPI-wrapped like the QuickBooks
     token; subscriptions die with their paired device.
"""
import datetime as dt
from types import SimpleNamespace

import pytest

from app.services import companion_service as cs
from app.services import (obligations_service, push_service, settings_service,
                          state_store)

MON1 = dt.date(2026, 9, 7)       # a Monday — cadence weekday derives from it
MON2 = dt.date(2026, 9, 14)

# Captured BEFORE the autouse fixture stubs it, so the isolation test can
# exercise the real wrapper's try/except.
_REAL_WATCH_CANDIDATES = push_service.watch_candidates


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH",
                        tmp_path / "local_settings.json")
    monkeypatch.setattr(push_service, "VAPID_PATH",
                        tmp_path / "companion_push_vapid.bin")
    # The v6.9.8 watch tier reaches QuickBooks/Gmail; the CORE contract
    # tests here stub it empty. Watch integration has its own tests below,
    # which re-monkeypatch this.
    monkeypatch.setattr(push_service, "watch_candidates",
                        lambda today=None: [])
    push_service._state.update(
        {"last_error": "", "last_success_iso": "", "last_eval_iso": ""})
    push_service._last_eval_ts = 0.0
    push_service._inflight.clear()
    yield


def _enable_push():
    settings_service.save_settings({"companion_push_enabled": "true"})
    push_service.ensure_vapid()


def _subscribed_device(device_id: str = "cd_test000001") -> None:
    state_store.save("companion_devices", [{
        "id": device_id, "name": "Pixel 7", "token_sha256": "irrelevant",
        "created_iso": "2026-08-31T09:00:00",
        "push_subscription": {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            "keys": {"p256dh": "pk", "auth": "ak"}},
    }])


@pytest.fixture()
def sent(monkeypatch):
    """Recorder standing in for pywebpush.webpush — captures every send."""
    calls = []
    monkeypatch.setattr(push_service, "webpush",
                        lambda **kw: calls.append(kw) or "ok")
    return calls


def _seed_weekly_obligation() -> str:
    ob = obligations_service.add_obligation(
        {"name": "Sales tax", "task": "File the monthly sales tax",
         "cadence": {"kind": "weekly", "weekday": MON1.weekday()}},
        written_by="manual")
    return ob["id"]


# --------------------------------------------------------------------------
# 1. VAPID at rest
# --------------------------------------------------------------------------

def test_vapid_private_key_is_dpapi_wrapped_at_rest():
    _enable_push()
    raw = push_service.VAPID_PATH.read_bytes()
    assert raw.startswith(b"RIDIAN-DPAPI-1\n")
    assert b"BEGIN" not in raw               # no plaintext PEM on disk
    key1 = push_service.public_key()
    assert key1 and "=" not in key1          # urlsafe, unpadded
    push_service.ensure_vapid()              # idempotent — same key survives
    assert push_service.public_key() == key1
    assert push_service.vapid_ready()


# --------------------------------------------------------------------------
# 2. Deduplication — per occurrence, restart-stable  (item 4)
# --------------------------------------------------------------------------

def test_one_notification_per_occurrence_across_reruns_and_restarts(sent):
    assert MON1.weekday() == 0 and MON2.weekday() == 0
    _enable_push()
    _subscribed_device()
    _seed_weekly_obligation()

    first = push_service.evaluate_and_push(today=MON1)
    assert first["sent"] >= 1
    baseline = len(sent)

    # Re-evaluation: nothing new.
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 0
    # "Restart": the ledger lives in the store, not in memory.
    push_service._state.update(
        {"last_error": "", "last_success_iso": "", "last_eval_iso": ""})
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 0
    # Day 2 of the SAME occurrence (now overdue): still the same key.
    assert push_service.evaluate_and_push(
        today=MON1 + dt.timedelta(days=1))["sent"] == 0
    assert len(sent) == baseline

    # The NEXT occurrence is a new key — exactly one more notification.
    nxt = push_service.evaluate_and_push(today=MON2)
    assert nxt["sent"] == 1
    payloads = [kw["data"] for kw in sent]
    assert all('"tab": "due"' in p or '"tab":"due"' in p.replace(" ", "")
               for p in payloads)


def test_ledger_keys_name_the_occurrence():
    _enable_push()
    ob_id = _seed_weekly_obligation()
    keys = [k for k, _p in push_service._candidates(today=MON1)]
    assert any(k.startswith(f"ob:{ob_id}:2026-") for k in keys)


# --------------------------------------------------------------------------
# 3. Mark-on-acceptance + honest failure  (items 4 + 6)
# --------------------------------------------------------------------------

def test_failed_send_is_not_marked_and_retries_after_recovery(monkeypatch):
    import requests as _requests
    _enable_push()
    _subscribed_device()
    _seed_weekly_obligation()

    def _down(**_kw):
        raise _requests.ConnectionError("connection refused")
    monkeypatch.setattr(push_service, "webpush", _down)
    out = push_service.evaluate_and_push(today=MON1)
    assert out["sent"] == 0 and out["attempted"] >= 1
    assert "unreachable" in push_service.status()["last_error"]
    assert state_store.load_dict("companion_push_ledger") == {}

    # Service back: the SAME occurrence now delivers, once, and the error
    # clears — recovery is as visible as failure.
    delivered = []
    monkeypatch.setattr(push_service, "webpush",
                        lambda **kw: delivered.append(kw) or "ok")
    assert push_service.evaluate_and_push(today=MON1)["sent"] >= 1
    assert push_service.status()["last_error"] == ""
    assert push_service.status()["last_success_iso"]
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 0


def test_gone_subscription_is_pruned_and_named(monkeypatch):
    _enable_push()
    _subscribed_device()
    _seed_weekly_obligation()

    def _gone(**_kw):
        exc = push_service.WebPushException("gone")
        exc.response = SimpleNamespace(status_code=410)
        raise exc
    monkeypatch.setattr(push_service, "webpush", _gone)
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 0
    assert push_service.subscribed_device_count() == 0
    assert "re-enable" in push_service.status()["last_error"]
    # Not marked: once the phone re-subscribes, the occurrence still lands.
    assert state_store.load_dict("companion_push_ledger") == {}


# --------------------------------------------------------------------------
# 4. Catch-up covers all three kinds — and ONLY those three  (items 2 + 3)
# --------------------------------------------------------------------------

def test_catch_up_notifies_approvals_and_parked_runs(sent):
    _enable_push()
    _subscribed_device()
    state_store.save("approvals", [{
        "id": "appr_cafe01", "operation_id": "op_1", "status": "pending",
        "question": "Create the $250 invoice?", "command": "invoice Sandra",
        "staged_at": "2026-08-31T09:00:00"}])
    state_store.save("operations", [{
        "id": "op_2", "status": "awaiting_input", "command": "research X",
        "needs_input": [{"question": "Which market?"}]}])

    assert push_service.evaluate_and_push(today=MON1 - dt.timedelta(days=1))["sent"] == 2
    tabs = sorted(kw["data"] for kw in sent)
    assert any('"tab": "approvals"' in d for d in tabs)
    assert any('"tab": "task"' in d for d in tabs)
    # Same state again — the PC restarting must not re-notify (item 4).
    assert push_service.evaluate_and_push(today=MON1 - dt.timedelta(days=1))["sent"] == 0


def test_reparked_run_notifies_again_answered_state_does_not(sent, monkeypatch):
    _enable_push()
    _subscribed_device()
    # Run the event hook synchronously for determinism.
    monkeypatch.setattr(push_service, "_spawn", lambda fn, *a: fn(*a))
    snapshot = {"id": "op_9", "status": "awaiting_input", "command": "plan",
                "needs_input": [{"question": "Budget?"}]}
    push_service.notify_run_parked(snapshot)
    push_service.notify_run_parked(snapshot)              # duplicate event
    assert len(sent) == 1
    snapshot2 = {**snapshot,
                 "needs_input": snapshot["needs_input"]
                 + [{"question": "And the deadline?"}]}
    push_service.notify_run_parked(snapshot2)             # re-parked: new ask
    assert len(sent) == 2


def test_approval_stage_event_notifies_once(sent, monkeypatch):
    _enable_push()
    _subscribed_device()
    monkeypatch.setattr(push_service, "_spawn", lambda fn, *a: fn(*a))
    entry = {"id": "appr_beef02", "question": "Send it?", "command": "propose"}
    push_service.notify_approval_staged(entry)
    push_service.notify_approval_staged(entry)
    assert len(sent) == 1
    assert '"tab": "approvals"' in sent[0]["data"]


def test_disabled_is_a_true_no_op_that_skips_nothing_later(sent):
    """Item 3's honesty: while OFF nothing sends AND nothing is marked, so
    enabling later still notifies what is STILL actionable."""
    _subscribed_device()
    _seed_weekly_obligation()
    assert push_service.evaluate_and_push(today=MON1) == {
        "sent": 0, "reason": "disabled"}
    assert sent == [] and state_store.load_dict("companion_push_ledger") == {}
    _enable_push()
    assert push_service.evaluate_and_push(today=MON1)["sent"] >= 1


# --------------------------------------------------------------------------
# 5. Subscriptions — validated, device-bound, revoked with the device
# --------------------------------------------------------------------------

def test_subscription_validation_refuses_the_unsafe_shapes():
    _subscribed_device()
    for bad in [
        {"endpoint": "http://fcm.googleapis.com/x", "keys": {"p256dh": "p", "auth": "a"}},
        {"endpoint": "https://192.168.1.1/x", "keys": {"p256dh": "p", "auth": "a"}},
        {"endpoint": "https://fcm.googleapis.com/" + "x" * 1100,
         "keys": {"p256dh": "p", "auth": "a"}},
        {"endpoint": "https://fcm.googleapis.com/x", "keys": {}},
    ]:
        with pytest.raises(push_service.SubscriptionError):
            push_service.save_subscription("cd_test000001", bad)
    with pytest.raises(push_service.SubscriptionError):
        push_service.save_subscription("cd_missing", {
            "endpoint": "https://fcm.googleapis.com/x",
            "keys": {"p256dh": "p", "auth": "a"}})


def test_revoking_the_device_revokes_its_subscription():
    _subscribed_device("cd_test000001")
    assert push_service.subscribed_device_count() == 1
    assert cs.revoke_device("cd_test000001") is True
    assert push_service.subscribed_device_count() == 0
    assert push_service.device_is_subscribed("cd_test000001") is False


def test_real_pywebpush_pipeline_encrypts_and_fails_honestly():
    """NO mocks: a browser-shaped subscription (real P-256 point, real
    16-byte auth secret) pushed through the REAL pywebpush pipeline — VAPID
    signature from our DPAPI-wrapped key, aes128gcm encryption — to a
    reserved-TLD endpoint that cannot resolve. Proves the whole send path
    up to the socket, and that the failure surfaces instead of no-opping."""
    import base64
    import os as _os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    _enable_push()
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    state_store.save("companion_devices", [{
        "id": "cd_real0001", "name": "Pixel 7", "token_sha256": "x",
        "push_subscription": {
            "endpoint": "https://push.invalid/wpush/v2/token",
            "keys": {
                "p256dh": base64.urlsafe_b64encode(point).rstrip(b"=").decode(),
                "auth": base64.urlsafe_b64encode(_os.urandom(16)).rstrip(b"=").decode()},
        }}])
    ok = push_service._send_to_subscriptions(
        {"title": "t", "body": "b", "tab": "due"}, tag="k")
    assert ok is False
    assert "unreachable" in push_service.status()["last_error"]


def test_startup_catch_up_runs_on_app_lifespan(monkeypatch):
    """Item 3's launch half is wired into the app's lifespan — the frozen
    backend runs it on every boot, off-thread."""
    from fastapi.testclient import TestClient

    from app.main import app
    called = []
    monkeypatch.setattr(push_service, "startup_catch_up",
                        lambda: called.append(1))
    with TestClient(app, client=("127.0.0.1", 50001)):
        pass
    assert called == [1]


# --------------------------------------------------------------------------
# 6. HTTPS enabler — sandboxed processes never touch it, failures are named
# --------------------------------------------------------------------------

def test_tls_listener_refuses_to_start_sandboxed(monkeypatch):
    from app.services import companion_tls
    monkeypatch.setenv("RIDIAN_SANDBOX", "1")
    monkeypatch.setitem(companion_tls.state, "error", "")
    assert companion_tls.start_if_possible(object()) is False
    assert "sandbox" in companion_tls.state["error"]


def test_tls_absence_is_named_not_silent(monkeypatch):
    """No tailscale.exe -> the state says so (Settings shows it), and the
    listener simply does not start. Item 6: never a quiet no-op."""
    from app.services import companion_tls
    monkeypatch.delenv("RIDIAN_SANDBOX", raising=False)
    monkeypatch.setattr(companion_tls, "_tailscale_exe", lambda: None)
    monkeypatch.setitem(companion_tls.state, "error", "")
    assert companion_tls.start_if_possible(object()) is False
    assert "tailscale.exe not found" in companion_tls.state["error"]


# --------------------------------------------------------------------------
# 6b. v6.9.8 watch tier — routed through the SAME ledger, never in the way
# --------------------------------------------------------------------------

def _watch_pair(key="watch:deal:d3:2026-08-10T09:00:00", title="Deal gone quiet"):
    return (key, {"title": title, "body": "b", "tab": "due"})


def test_watch_findings_route_through_the_ledger_once(sent, monkeypatch):
    _enable_push()
    _subscribed_device()
    monkeypatch.setattr(push_service, "watch_candidates",
                        lambda today=None: [_watch_pair()])
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 1
    # Same finding, next evaluation / restart: silent (item 3 of the ask).
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 0
    # The stamp moved (a touch, then quiet again): a new key notifies.
    monkeypatch.setattr(push_service, "watch_candidates", lambda today=None: [
        _watch_pair("watch:deal:d3:2026-09-05T09:00:00")])
    assert push_service.evaluate_and_push(today=MON2)["sent"] == 1


def test_watch_burst_collapses_into_one_digest(sent, monkeypatch):
    """A cold start over aged books must not fire N pings — one digest,
    every underlying key marked, so nothing re-fires individually later."""
    _enable_push()
    _subscribed_device()
    pairs = [_watch_pair(f"watch:inv:production:{i}:2026-08-01",
                         f"Invoice #{i} is past due") for i in range(5)]
    monkeypatch.setattr(push_service, "watch_candidates",
                        lambda today=None: list(pairs))
    out = push_service.evaluate_and_push(today=MON1)
    assert out["sent"] == 5                   # five KEYS marked...
    assert len(sent) == 1                     # ...by ONE push
    assert "Ridian noticed 5 things" in sent[0]["data"]
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 0


def test_watch_failure_never_suppresses_core_pushes(sent, monkeypatch):
    _enable_push()
    _subscribed_device()
    _seed_weekly_obligation()

    def _boom(today=None):
        raise RuntimeError("gmail fell over")
    monkeypatch.setattr(push_service, "watch_candidates",
                        _REAL_WATCH_CANDIDATES)   # the real isolating wrapper
    import app.services.watch_service as ws
    monkeypatch.setattr(ws, "push_candidates", _boom)
    out = push_service.evaluate_and_push(today=MON1)
    assert out["sent"] >= 1                   # the obligation still pushed


def test_candidates_and_sends_run_outside_the_ledger_lock(monkeypatch):
    """The structural fix from the design review: a stalled watch gather
    (QBO/Gmail) must never hold the ledger lock that the approval/park
    event pushes contend on."""
    _enable_push()
    _subscribed_device()
    seen = {}

    def _core(today=None):
        seen["core_locked"] = push_service._ledger_lock.locked()
        return []

    def _watch(today=None):
        seen["watch_locked"] = push_service._ledger_lock.locked()
        return [_watch_pair()]
    monkeypatch.setattr(push_service, "_candidates", _core)
    monkeypatch.setattr(push_service, "watch_candidates", _watch)

    def _send(payload, tag):
        seen["send_locked"] = push_service._ledger_lock.locked()
        return True
    monkeypatch.setattr(push_service, "_send_to_subscriptions", _send)
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 1
    assert seen == {"core_locked": False, "watch_locked": False,
                    "send_locked": False}


def test_old_ledger_entries_reopen_at_diff_time_without_a_save(sent):
    """The 60-day re-notify must not depend on unrelated push traffic:
    pruning applies when DIFFING, not only when saving."""
    _enable_push()
    _subscribed_device()
    _seed_weekly_obligation()
    state_store.save("companion_push_ledger",
                     {f"ob:stale:key": "2026-01-01T00:00:00"})
    assert push_service.evaluate_and_push(today=MON1)["sent"] >= 1


# --------------------------------------------------------------------------
# 6c. TLS listener claims its URL only AFTER the bind (v6.9.8 fix)
# --------------------------------------------------------------------------

def _tls_ready(monkeypatch, server_factory):
    from app.services import companion_tls
    monkeypatch.delenv("RIDIAN_SANDBOX", raising=False)
    monkeypatch.setattr(companion_tls, "ensure_cert",
                        lambda: ("node.tail.ts.net", "c.crt", "c.key"))
    monkeypatch.setattr(companion_tls, "_make_server",
                        lambda app, port, cert, key: server_factory())
    monkeypatch.setitem(companion_tls.state, "url", "")
    monkeypatch.setitem(companion_tls.state, "error", "")
    monkeypatch.setitem(companion_tls.state, "host", "")
    return companion_tls


def test_tls_url_claimed_only_after_the_listener_binds(monkeypatch):
    class _Binds:
        started = False
        def run(self):
            import time as _t
            _t.sleep(0.1)
            self.started = True
            _t.sleep(3)
    tls = _tls_ready(monkeypatch, _Binds)
    assert tls.start_if_possible(object(), port=9443) is True
    assert tls.state["url"] == "https://node.tail.ts.net:9443/companion"
    assert tls.state["error"] == ""


def test_tls_bind_failure_is_named_never_an_unbacked_url(monkeypatch):
    """The 0.9.6 flaw, fixed: a thread that dies before binding (port in
    use, unreadable cert) must surface as an ERROR in Settings — never an
    https URL with nothing behind it."""
    class _Dies:
        started = False
        def run(self):
            return                # thread ends without ever binding
    tls = _tls_ready(monkeypatch, _Dies)
    assert tls.start_if_possible(object(), port=9443) is False
    assert tls.state["url"] == ""
    assert "died before binding" in tls.state["error"]


# --------------------------------------------------------------------------
# 7. Throttle — opportunistic evaluation never stampedes  (item 3)
# --------------------------------------------------------------------------

def test_maybe_evaluate_is_throttled(monkeypatch):
    _enable_push()
    spawned = []
    monkeypatch.setattr(push_service, "_spawn", lambda fn, *a: spawned.append(fn))
    push_service.maybe_evaluate()
    push_service.maybe_evaluate()
    push_service.maybe_evaluate()
    assert len(spawned) == 1                  # one per 10-minute window
    push_service._last_eval_ts = 0.0          # window elapsed
    push_service.maybe_evaluate()
    assert len(spawned) == 2
