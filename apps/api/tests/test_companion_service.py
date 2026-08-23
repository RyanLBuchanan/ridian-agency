"""Companion pairing/token logic (v6.9) — the pure-service half.

The middleware/auth-matrix pins live in test_companion_access.py; these
pin the credential mechanics themselves:
  - a pairing code is single-use, expiring, and screen-readable;
  - five wrong codes kill the code AND lock pairing; only the operator's
    regenerate (a loopback-only desktop action) unlocks;
  - the store holds a SHA-256, never a usable token, and listing devices
    never leaks the hash;
  - verifying a token writes NOTHING (state byte-identical) — last-seen
    is memory-only, because every state write snapshots first.
"""
import hashlib

import pytest

from app.services import companion_service as cs
from app.services import state_store


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    cs.reset_pairing_state()
    yield
    cs.reset_pairing_state()


def _state_bytes():
    if not state_store.STATE_DIR.exists():
        return {}
    return {p.name: p.read_bytes()
            for p in sorted(state_store.STATE_DIR.glob("*.json"))}


# --------------------------------------------------------------------------
# Pairing codes
# --------------------------------------------------------------------------

def test_code_is_screen_readable_and_single_use():
    code = cs.generate_pairing_code()["code"]
    assert len(code) == cs._CODE_LENGTH
    assert all(ch in cs._CODE_ALPHABET for ch in code)
    # No ambiguous glyphs in the alphabet at all.
    assert not set("01OIL") & set(cs._CODE_ALPHABET)
    out = cs.pair(code, device_name="Pixel 7")
    assert out["token"]
    with pytest.raises(cs.CompanionError, match="no active pairing code"):
        cs.pair(code)                                   # consumed


def test_code_is_case_insensitive_for_the_typist():
    code = cs.generate_pairing_code()["code"]
    assert cs.pair(code.lower())["token"]


def test_expired_code_refuses(monkeypatch):
    code = cs.generate_pairing_code()["code"]
    monkeypatch.setattr(cs, "_now", lambda: cs._code_expires_at + 1)
    with pytest.raises(cs.CompanionError, match="no active pairing code"):
        cs.pair(code)


def test_no_code_generated_refuses():
    with pytest.raises(cs.CompanionError, match="no active pairing code"):
        cs.pair("WHATEVER")


def test_five_wrong_codes_lock_pairing_and_regenerate_unlocks():
    code = cs.generate_pairing_code()["code"]
    wrong = "X" * cs._CODE_LENGTH
    for _ in range(cs.MAX_CODE_ATTEMPTS - 1):
        with pytest.raises(cs.CompanionError, match="wrong pairing code"):
            cs.pair(wrong)
    with pytest.raises(cs.CompanionError, match="locked"):
        cs.pair(wrong)                                  # 5th kills + locks
    assert cs.pairing_locked()
    # Even the CORRECT code is dead now.
    with pytest.raises(cs.CompanionError, match="locked"):
        cs.pair(code)
    # Nothing was written for any of it.
    assert state_store.load_list(cs._STORE) == []
    # The operator regenerating (loopback-only desktop action) unlocks.
    fresh = cs.generate_pairing_code()["code"]
    assert not cs.pairing_locked()
    assert cs.pair(fresh)["token"]


# --------------------------------------------------------------------------
# Device tokens
# --------------------------------------------------------------------------

def test_store_holds_hash_never_the_token():
    code = cs.generate_pairing_code()["code"]
    out = cs.pair(code, device_name="Pixel 7")
    raw = out["token"]
    stored = state_store.load_list(cs._STORE)
    assert len(stored) == 1
    assert stored[0]["token_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    blob = str(stored)
    assert raw not in blob                              # never persisted raw
    # Listing for the Settings view never exposes the hash either.
    listed = cs.list_devices()[0]
    assert "token_sha256" not in listed
    assert listed["name"] == "Pixel 7"


def test_verify_round_trip_and_reject():
    code = cs.generate_pairing_code()["code"]
    raw = cs.pair(code)["token"]
    device = cs.verify_token(raw)
    assert device and device["id"].startswith("cd_")
    assert cs.verify_token(raw[:-1] + ("A" if raw[-1] != "A" else "B")) is None
    assert cs.verify_token("") is None
    assert cs.verify_token(None) is None


def test_verify_never_writes_state():
    code = cs.generate_pairing_code()["code"]
    raw = cs.pair(code)["token"]
    before = _state_bytes()
    snaps = [s["id"] for s in state_store.list_snapshots()]
    for _ in range(3):
        assert cs.verify_token(raw)
    assert _state_bytes() == before
    assert [s["id"] for s in state_store.list_snapshots()] == snaps
    # ...but last-seen still surfaces, from memory.
    assert cs.list_devices()[0]["last_seen_iso"]


def test_revoke_kills_the_token():
    code = cs.generate_pairing_code()["code"]
    out = cs.pair(code)
    assert cs.revoke_device(out["device_id"]) is True
    assert cs.verify_token(out["token"]) is None
    assert cs.revoke_device(out["device_id"]) is False  # honest second answer


def test_devices_carry_provenance_stamp():
    code = cs.generate_pairing_code()["code"]
    cs.pair(code)
    stored = state_store.load_list(cs._STORE)[0]
    assert stored["written_by"] == "companion-pairing"
