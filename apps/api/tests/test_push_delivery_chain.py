"""The push delivery chain, pinned end to end (2026-09-10).

Proved live against the installed 0.9.8 (a real FCM 201 to the paired
Pixel) — this file keeps that truth from silently rotting. Everything is
REAL except one seam: the DPAPI-wrapped VAPID file, the /companion/me
serving path behind the paired-device gate, pywebpush's actual ES256
signing and aes128gcm encryption, and the real dedup ledger all run; the
ONLY stub is requests.post — the push service's own socket — captured so
the assertions run against the actual outbound request (headers, TTL,
VAPID JWT, encrypted body), never against mocks of our own code.

The clock is FROZEN, not derived from the real date: the obligation is
seeded through add_obligation's own injectable ``today`` (the same seam
every due computation reads), so the scenario is deterministic forever —
the 0.9.8-era date-rot class can't recur here.
"""
import base64
import datetime as dt
import json
import os
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

from app.main import app
from app.services import companion_service as cs
from app.services import (obligations_service, push_service, settings_service,
                          state_store)

HDR = {"X-Ridian-Companion": "1"}
# Frozen clock, injected through production's own ``today`` seam.
SEED = dt.date(2026, 9, 1)       # the fake "now" the obligation is born on
MON1 = dt.date(2026, 9, 7)       # first Monday after SEED
MON2 = dt.date(2026, 9, 14)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH",
                        tmp_path / "local_settings.json")
    monkeypatch.setattr(push_service, "VAPID_PATH", tmp_path / "vapid.bin")
    cs.reset_pairing_state()
    settings_service.save_settings({
        "companion_enabled": "true",
        "companion_push_enabled": "true",
        # The REAL kill-switch keeps the watch tier's QBO/Gmail gather out
        # of these chain tests — no stubbing of our own code needed.
        "watch_push_enabled": "false",
    })
    push_service.ensure_vapid()        # what the /settings enable hook does
    push_service._state.update(
        {"last_error": "", "last_success_iso": "", "last_eval_iso": ""})
    push_service._last_eval_ts = 0.0
    push_service._inflight.clear()
    yield
    cs.reset_pairing_state()


@pytest.fixture()
def outbox(monkeypatch):
    """THE one stubbed boundary: the push service's HTTP endpoint.
    pywebpush's real signing/encryption stack runs above it; we capture
    exactly what would have gone over the wire."""
    import requests as _requests

    sent: list = []
    replies: list = []                 # per-call status override; default 201

    def _post(*args, **kwargs):
        url = args[0] if args else kwargs.get("url")
        data = kwargs.get("data", args[1] if len(args) > 1 else None)
        sent.append({"url": url, "data": data,
                     "headers": {k.lower(): v for k, v in
                                 dict(kwargs.get("headers") or {}).items()}})
        status = replies.pop(0) if replies else 201
        return SimpleNamespace(status_code=status,
                               reason="Created" if status < 400 else "Gone",
                               text="" if status < 400 else "gone",
                               headers={})

    monkeypatch.setattr(_requests, "post", _post)
    return SimpleNamespace(sent=sent, replies=replies)


def _paired() -> TestClient:
    pc = TestClient(app, client=("127.0.0.1", 50000))
    code = pc.post("/companion/pairing-code").json()["code"]
    lan = TestClient(app, base_url="http://192.168.1.7:8000",
                     client=("192.168.1.50", 40001))
    r = lan.post("/companion/pair", headers=HDR,
                 json={"code": code, "device_name": "Pixel 7"})
    assert r.status_code == 200, r.text
    return lan


def _real_subscription(endpoint="https://push.example.com/wpush/v2/abc123"):
    """Browser-shaped: a genuine P-256 point and 16-byte auth secret, so
    the real aes128gcm encryption actually runs."""
    browser_key = ec.generate_private_key(ec.SECP256R1())
    point = browser_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": endpoint,
            "keys": {"p256dh": _b64u(point), "auth": _b64u(os.urandom(16))}}


# --------------------------------------------------------------------------
# 1 + 2. One key end to end, and a well-formed signed request on the wire
# --------------------------------------------------------------------------

def test_signing_key_is_the_served_key_and_the_request_is_well_formed(outbox):
    lan = _paired()

    # The key loads from the DPAPI-wrapped file in the data dir...
    raw = push_service.VAPID_PATH.read_bytes()
    assert raw.startswith(b"RIDIAN-DPAPI-1\n") and b"BEGIN" not in raw
    served = lan.get("/companion/me").json()["push"]["key"]
    # ...and the file-derived key IS the served key, byte for byte.
    assert push_service.public_key() == served

    sub = _real_subscription()
    assert lan.post("/companion/push/subscribe", headers=HDR,
                    json=sub).status_code == 200

    payload = {"title": "Chain pin", "body": "b", "tab": "due"}
    assert push_service._send_to_subscriptions(payload, tag="chain:pin") is True

    [req] = outbox.sent
    assert req["url"] == sub["endpoint"]
    h = req["headers"]
    assert h["content-encoding"] == "aes128gcm"
    assert h["ttl"] == str(push_service._TTL_SECONDS)

    # VAPID auth header: the k= param is the SERVED key, and the JWT's
    # signature VERIFIES against that exact public key — the signing key
    # and the key the phone subscribed under cannot drift apart silently.
    auth = h["authorization"]
    assert auth.startswith("vapid t=")
    jwt = auth.split("t=", 1)[1].split(",", 1)[0].strip()
    k_param = auth.split("k=", 1)[1].strip()
    assert k_param == served
    hdr_b64, claims_b64, sig_b64 = jwt.split(".")
    assert json.loads(_unb64u(hdr_b64))["alg"] == "ES256"
    claims = json.loads(_unb64u(claims_b64))
    assert claims["aud"] == "https://push.example.com"
    assert claims["sub"] == "mailto:companion@ridiantechnologies.com"
    assert claims["exp"] > time.time()
    pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), _unb64u(served))
    sig = _unb64u(sig_b64)
    pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"),
                                    int.from_bytes(sig[32:], "big")),
               f"{hdr_b64}.{claims_b64}".encode(),
               ec.ECDSA(hashes.SHA256()))          # raises on any drift

    # Encrypted body shape (RFC 8188 aes128gcm): 16B salt + 4B record size
    # + 1B keyid length + 65B uncompressed P-256 point, then exactly
    # plaintext + pad delimiter + AES-GCM tag. And it is ciphertext.
    body = bytes(req["data"])
    assert int.from_bytes(body[16:20], "big") == 4096
    assert body[20] == 65 and body[21] == 0x04
    expected_plain = json.dumps({**payload, "tag": "chain:pin"}).encode()
    assert len(body) == 86 + len(expected_plain) + 17
    assert expected_plain not in body


# --------------------------------------------------------------------------
# 3. The ledger suppresses the same occurrence, never a new one
# --------------------------------------------------------------------------

def test_ledger_suppresses_repeat_occurrence_but_not_a_new_one(outbox):
    lan = _paired()
    assert lan.post("/companion/push/subscribe", headers=HDR,
                    json=_real_subscription()).status_code == 200
    obligations_service.add_obligation(
        {"name": "Chain obligation", "task": "file it",
         "cadence": {"kind": "weekly", "weekday": 0}},
        written_by="manual", today=SEED)     # frozen clock, production's seam

    assert push_service.evaluate_and_push(today=MON1)["sent"] == 1
    assert len(outbox.sent) == 1
    # Same occurrence, re-evaluated: suppressed — zero wire traffic.
    assert push_service.evaluate_and_push(today=MON1)["sent"] == 0
    assert len(outbox.sent) == 1
    # The NEXT occurrence is a new key: it must NOT be suppressed.
    assert push_service.evaluate_and_push(today=MON2)["sent"] == 1
    assert len(outbox.sent) == 2


# --------------------------------------------------------------------------
# 4. 404/410 from the push service: a NAMED dead state, never swallowed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("gone_status", [404, 410])
def test_gone_from_the_push_service_is_a_named_dead_state(outbox, gone_status):
    lan = _paired()
    assert lan.post("/companion/push/subscribe", headers=HDR,
                    json=_real_subscription()).status_code == 200
    outbox.replies.append(gone_status)
    assert push_service._send_to_subscriptions(
        {"title": "x", "body": "", "tab": "due"}, tag="chain:gone") is False
    # Named state 1: the subscription is marked dead (pruned from the
    # device record) — the phone re-registers on its next open.
    assert push_service.subscribed_device_count() == 0
    # Named state 2: status says WHICH phone and WHAT to do.
    err = push_service.status()["last_error"]
    assert "Pixel 7" in err and "re-enable" in err
    # And nothing was marked: once re-subscribed, the send still happens.
    assert state_store.load_dict("companion_push_ledger") == {}
