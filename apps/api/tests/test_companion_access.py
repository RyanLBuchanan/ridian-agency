"""CompanionGate auth matrix (v6.9) — the LAN is hostile until paired.

THE CONTRACT, pinned here:
  1. Loopback = the desktop: untouched, no cookie, no header, companion on
     or off — byte-for-byte the pre-v6.9 behavior.
  2. Companion disabled: every off-box request is refused, including the
     pairing surface itself.
  3. Enabled, unpaired: ONLY the pairing surface answers (page, manifest,
     icons, POST /companion/pair). /health, /, /static, business endpoints
     — all refused.
  4. Enabled, paired: ONLY the companion allowlist answers. Settings,
     memory/CRM, email send, OAuth, snapshots, operation source/audio
     streaming stay PC-only even with a valid token.
  5. Cross-site defenses: non-GET without the X-Ridian-Companion header is
     refused (forms can't set it); a non-IP Host header is refused (DNS
     rebinding); the CORS layer never allows credentials.
  6. Wrong codes lock pairing; revocation kills a cookie immediately.
"""
import datetime as dt
import re as _re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import companion_service as cs
from app.services import settings_service, state_store
from app.services.runtime_paths import resolve_backend_host

LAN_HOST = "http://192.168.1.7:8000"
HDR = {"X-Ridian-Companion": "1"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH",
                        tmp_path / "local_settings.json")
    cs.reset_pairing_state()
    yield
    cs.reset_pairing_state()


def _pc() -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def _lan(base_url: str = LAN_HOST) -> TestClient:
    return TestClient(app, base_url=base_url, client=("192.168.1.50", 40001))


def _enable():
    settings_service.save_settings({"companion_enabled": "true"})


def _paired_lan() -> tuple:
    """Enable, generate a code from the PC, pair from the LAN — the real
    flow. Returns (lan_client_with_cookie, device_id)."""
    _enable()
    code = _pc().post("/companion/pairing-code").json()["code"]
    lan = _lan()
    r = lan.post("/companion/pair", headers=HDR,
                 json={"code": code, "device_name": "Pixel 7"})
    assert r.status_code == 200, r.text
    return lan, r.json()["device_id"]


# --------------------------------------------------------------------------
# 1. Desktop parity — loopback is never gated
# --------------------------------------------------------------------------

@pytest.mark.parametrize("enabled", [False, True])
def test_loopback_is_untouched_with_companion_on_or_off(enabled):
    if enabled:
        _enable()
    pc = _pc()
    assert pc.get("/health").status_code == 200
    assert pc.get("/settings").status_code == 200
    assert pc.get("/obligations").status_code == 200
    assert pc.get("/approvals").status_code == 200
    # No cookie, no special header — plain desktop requests.
    assert pc.post("/obligations", json={"name": "x", "task": "y",
                   "cadence": {"kind": "weekly", "weekday": 0}}).status_code == 200


# --------------------------------------------------------------------------
# 2. Disabled = the LAN does not exist
# --------------------------------------------------------------------------

def test_disabled_refuses_everything_offbox_including_pairing():
    lan = _lan()
    for method, path in [("GET", "/companion"), ("GET", "/health"),
                         ("GET", "/morning-brief"), ("GET", "/obligations"),
                         ("GET", "/"), ("GET", "/static/app.js"),
                         ("GET", "/companion/manifest.json")]:
        r = lan.request(method, path)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
        assert "disabled" in r.json()["detail"]
    r = lan.post("/companion/pair", headers=HDR, json={"code": "X"})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]


# --------------------------------------------------------------------------
# 3. Enabled, unpaired = pairing surface only
# --------------------------------------------------------------------------

def test_unpaired_gets_pairing_surface_and_nothing_else():
    _enable()
    lan = _lan()
    assert lan.get("/companion").status_code == 200
    assert "Pair with your PC" in lan.get("/companion").text
    assert lan.get("/companion/manifest.json").status_code == 200
    assert lan.get("/static/companion-icon-192.png").status_code == 200
    for path in ["/health", "/morning-brief", "/obligations", "/approvals",
                 "/settings", "/", "/static/app.js", "/dashboard",
                 "/memory/contacts", "/companion/me"]:
        r = lan.get(path)
        assert r.status_code == 401, f"GET {path} -> {r.status_code}"
        assert "Not paired" in r.json()["detail"]


def test_csrf_header_required_on_every_offbox_write():
    _enable()
    lan = _lan()
    # Even the pair call itself: a cross-site form can POST, but it cannot
    # set the companion header.
    r = lan.post("/companion/pair", json={"code": "WHATEVER"})
    assert r.status_code == 403 and "header" in r.json()["detail"]
    r = lan.options("/morning-brief")
    assert r.status_code == 403


def test_dns_rebinding_hostname_is_refused():
    _enable()
    for url in ["http://evil.example.com", "http://ridian.attacker.io:8000"]:
        r = _lan(base_url=url).get("/companion")
        assert r.status_code == 403
        assert "IP address" in r.json()["detail"]
    # Literal-IP Host headers (the phone's actual request) pass.
    assert _lan("http://192.168.1.7:8000").get("/companion").status_code == 200
    assert _lan("http://10.0.0.3").get("/companion").status_code == 200


# --------------------------------------------------------------------------
# 4. Pairing flow — codes, lockout, cookies
# --------------------------------------------------------------------------

def test_pairing_flow_sets_cookie_and_grants_the_allowlist():
    lan, _device = _paired_lan()
    assert "ridian_companion" in lan.cookies
    me = lan.get("/companion/me")
    assert me.status_code == 200 and me.json()["name"] == "Pixel 7"
    assert lan.get("/obligations").status_code == 200
    assert lan.get("/approvals").status_code == 200
    assert lan.get("/morning-brief").status_code == 200
    assert lan.get("/operations/recent?limit=50").status_code == 200


def test_wrong_codes_lock_pairing_via_http():
    _enable()
    _pc().post("/companion/pairing-code")
    lan = _lan()
    for i in range(cs.MAX_CODE_ATTEMPTS):
        r = lan.post("/companion/pair", headers=HDR,
                     json={"code": "WRONGWRONG"})
        assert r.status_code == 403
    assert "locked" in r.json()["detail"]
    # And no device record was ever written.
    assert state_store.load_list("companion_devices") == []


def test_revoke_from_the_pc_kills_the_phone_cookie():
    lan, device_id = _paired_lan()
    assert lan.get("/obligations").status_code == 200
    assert _pc().post("/companion/revoke",
                      json={"device_id": device_id}).json()["revoked"] is True
    assert lan.get("/obligations").status_code == 401


def test_garbage_cookie_is_refused():
    _enable()
    lan = _lan()
    lan.cookies.set("ridian_companion", "A" * 43)
    assert lan.get("/obligations").status_code == 401


# --------------------------------------------------------------------------
# 5. THE SCOPE PIN — a paired phone still can't leave the allowlist
# --------------------------------------------------------------------------

def test_paired_device_is_denied_everything_off_the_allowlist():
    lan, _device = _paired_lan()
    denied = [
        ("GET", "/settings"), ("POST", "/settings"),
        ("GET", "/health"), ("GET", "/"), ("GET", "/static/app.js"),
        ("GET", "/memory/contacts"), ("POST", "/memory/contacts"),
        ("GET", "/dashboard"), ("GET", "/audit"),
        ("GET", "/quickbooks/status"), ("POST", "/quickbooks/connect"),
        ("GET", "/google/status"),
        ("POST", "/email/send-approved"),
        ("GET", "/snapshots"), ("POST", "/snapshots/restore"),
        # Obligation CRUD stays on the PC; only complete/dismiss travel.
        ("POST", "/obligations"),
        ("POST", "/obligations/obl_x/update"),
        ("POST", "/obligations/obl_x/delete"),
        # Reserved operation routes that stream source text / audio.
        ("GET", "/operations/load"), ("GET", "/operations/audio"),
        # Companion admin is PC-only even for a paired phone.
        ("GET", "/companion/status"), ("POST", "/companion/pairing-code"),
        ("POST", "/companion/revoke"),
    ]
    for method, path in denied:
        r = lan.request(method, path, headers=HDR if method != "GET" else {})
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
        assert "not available from a companion device" in r.json()["detail"]


def test_allowlisted_id_routes_reach_their_handlers():
    lan, _device = _paired_lan()
    # The gate passes these through — the HANDLER answers (404/400/200),
    # which is the proof the refusal above is the gate, not the route.
    r = lan.get("/operations/op_does_not_exist")
    assert r.status_code == 404
    r = lan.post("/operations/op_does_not_exist/dismiss", headers=HDR)
    assert r.status_code in (200, 404)
    r = lan.post("/obligations/obl_missing/complete", headers=HDR)
    assert r.status_code == 400                      # ObligationError: no such
    r = lan.post("/approvals/answer", headers=HDR,
                 json={"id": "appr_x", "value": "approve"})
    assert r.status_code == 200 and "error" in r.json()


def test_admin_endpoints_double_check_loopback_in_the_handler():
    """Belt-and-braces: even if the gate allowlisted them by mistake, the
    pairing-admin handlers refuse off-box callers themselves."""
    from app.main import _require_loopback

    class _Req:
        class client:
            host = "192.168.1.50"

    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _require_loopback(_Req())


# --------------------------------------------------------------------------
# 6. Pure gate logic
# --------------------------------------------------------------------------

def test_allowlist_matcher_semantics():
    allowed = [
        ("GET", "/morning-brief"), ("GET", "/obligations"),
        ("POST", "/obligations/obl_abc/complete"),
        ("POST", "/obligations/obl_abc/dismiss"),
        ("GET", "/approvals"), ("POST", "/approvals/answer"),
        ("POST", "/operations/run"), ("GET", "/operations/recent"),
        ("POST", "/operations/op_1/continue"),
        ("POST", "/operations/op_1/dismiss"),
        ("POST", "/operations/op_1/background"),
        ("GET", "/operations/op_1"), ("GET", "/companion/me"),
        # v6.9.7 Web Push: the SW script and the device's OWN subscription.
        ("GET", "/companion-sw.js"),
        ("POST", "/companion/push/subscribe"),
        ("POST", "/companion/push/unsubscribe"),
    ]
    for m, p in allowed:
        assert cs.device_request_allowed(m, p), f"{m} {p} should be allowed"
    refused = [
        ("GET", "/operations/load"), ("GET", "/operations/audio"),
        ("GET", "/operations/recent/extra"),
        ("POST", "/obligations"), ("POST", "/obligations/obl_abc/delete"),
        ("POST", "/obligations/obl_abc/update"),
        ("DELETE", "/memory/contacts"), ("GET", "/settings"),
        ("POST", "/operations/op_1/upload-source"),
        ("POST", "/operations/op_1/memory/commit"),
        ("GET", "/companion/status"), ("POST", "/companion/revoke"),
    ]
    for m, p in refused:
        assert not cs.device_request_allowed(m, p), f"{m} {p} must be refused"


def test_client_is_local_semantics():
    assert cs.client_is_local("127.0.0.1")
    assert cs.client_is_local("127.0.0.53")
    assert cs.client_is_local("::1")
    assert cs.client_is_local(None)             # in-process ASGI call
    assert cs.client_is_local("testclient")     # starlette TestClient default
    assert not cs.client_is_local("192.168.1.50")
    assert not cs.client_is_local("10.0.0.9")
    assert not cs.client_is_local("8.8.8.8")
    assert not cs.client_is_local("evil")


def test_gate_is_address_agnostic_for_any_non_loopback_peer():
    """There is NO Tailscale-specific rule, and deliberately so: every
    non-loopback peer walks the same path — enabled + IP-literal Host +
    paired cookie + allowlisted route. That is WHY a tailnet (100.64/10)
    peer works from outside the house, and it is the reason a future
    "LAN-only" tightening must break this test rather than pass quietly.
    Binding 0.0.0.0 binds EVERY interface, tailnet included."""
    for peer in ("192.168.1.50",      # home Wi-Fi
                 "10.224.51.133",     # another private range
                 "100.105.232.26",    # Tailscale CGNAT range
                 "100.64.0.1", "100.127.255.254"):
        assert not cs.client_is_local(peer), f"{peer} must be gated"
        assert cs.host_header_ok(peer), f"{peer} must pass the Host check"
    _enable()
    code = _pc().post("/companion/pairing-code").json()["code"]
    tailnet = TestClient(app, base_url="http://100.105.232.26:8000",
                         client=("100.105.232.26", 41234))
    # Unpaired: the pairing surface only.
    assert tailnet.get("/companion").status_code == 200
    assert tailnet.get("/obligations").status_code == 401
    assert tailnet.post("/companion/pair", headers=HDR,
                        json={"code": code, "device_name": "Pixel 7 (tailnet)"}
                        ).status_code == 200
    # Paired: the allowlist, and nothing beyond it.
    assert tailnet.get("/obligations").status_code == 200
    assert tailnet.get("/morning-brief").status_code == 200
    assert tailnet.get("/settings").status_code == 403
    assert tailnet.post("/settings", headers=HDR, json={}).status_code == 403


def test_host_header_ok_semantics():
    assert cs.host_header_ok("192.168.1.7:8000")
    assert cs.host_header_ok("192.168.1.7")
    assert cs.host_header_ok("[fe80::1]:8000")
    assert not cs.host_header_ok("evil.example.com")
    assert not cs.host_header_ok("evil.example.com:8000")
    assert not cs.host_header_ok("")


def test_resolve_backend_host_rules(monkeypatch):
    monkeypatch.delenv("RIDIAN_HOST", raising=False)
    monkeypatch.delenv("RIDIAN_SANDBOX", raising=False)
    assert resolve_backend_host(False) == "127.0.0.1"
    assert resolve_backend_host(True) == "0.0.0.0"
    # A sandboxed probe NEVER opens the LAN off a setting.
    monkeypatch.setenv("RIDIAN_SANDBOX", "1")
    assert resolve_backend_host(True) == "127.0.0.1"
    # The explicit per-child override wins everywhere.
    monkeypatch.setenv("RIDIAN_HOST", "0.0.0.0")
    assert resolve_backend_host(False) == "0.0.0.0"


# --------------------------------------------------------------------------
# 7. Companion admin surface (PC side)
# --------------------------------------------------------------------------

def _free_port() -> int:
    """A port with nothing listening: bind to 0, read it back, release it.
    The status tests probe THIS instead of the real port 8000 — on the dev
    machine the LIVE app may be listening there, and these tests must not
    depend on (or touch) it."""
    import socket as _s
    with _s.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def test_status_reports_devices_and_restart_state(monkeypatch):
    """restart_required comes from the PROBE, not from RIDIAN_BOUND_HOST:
    an env var claiming 0.0.0.0 must NOT make the UI promise reachability
    that no socket backs (the probe pin lives in
    test_status_probes_the_listener_instead_of_trusting_the_setting)."""
    lan, device_id = _paired_lan()
    port = _free_port()
    pc = TestClient(app, base_url=f"http://testserver:{port}",
                    client=("127.0.0.1", 50000))
    out = pc.get("/companion/status").json()
    assert out["enabled"] is True
    assert out["devices"][0]["id"] == device_id
    assert out["devices"][0]["name"] == "Pixel 7"
    assert out["url"].endswith("/companion")
    monkeypatch.setenv("RIDIAN_BOUND_HOST", "0.0.0.0")
    out = pc.get("/companion/status").json()
    assert out["bound_host"] == "0.0.0.0"        # reported, not trusted
    assert out["lan_listening"] is False
    assert out["restart_required"] is True


def test_status_offers_both_entry_points_labelled(monkeypatch):
    """Enabling binds every interface, so both addresses are real entry
    points — and they mean different things, so both are reported."""
    _enable()
    monkeypatch.setattr(cs, "lan_ip", lambda: "192.168.1.7")
    monkeypatch.setattr(cs, "tailnet_ip", lambda: "100.105.232.26")
    out = _pc().get("/companion/status").json()
    assert out["lan_ip"] == "192.168.1.7"
    assert out["url"].endswith("/companion") and "192.168.1.7" in out["url"]
    assert out["tailnet_ip"] == "100.105.232.26"
    assert "100.105.232.26" in out["tailnet_url"]
    assert out["tailnet_url"].endswith("/companion")


def test_no_tailnet_means_no_row_not_a_dead_one(monkeypatch):
    _enable()
    monkeypatch.setattr(cs, "lan_ip", lambda: "192.168.1.7")
    monkeypatch.setattr(cs, "tailnet_ip", lambda: "")
    out = _pc().get("/companion/status").json()
    assert out["tailnet_ip"] == ""
    assert out["tailnet_url"] == ""          # renderer omits the row entirely
    assert out["url"]                        # the LAN row still stands


def test_tailnet_detection_only_ever_reports_the_cgnat_range(monkeypatch):
    """Detected, never assumed. A machine with no tailnet must report "" —
    even though the routing probe will happily hand back the default-route
    address if the range check is dropped."""
    import socket as _socket

    class _FakeSock:
        def __init__(self, addr): self._addr = addr
        def settimeout(self, _t): pass
        def connect(self, _a): pass
        def getsockname(self): return (self._addr, 9)
        def close(self): pass

    def _no_hostname_addrs(*_a, **_k):
        return [(None, None, None, None, ("192.168.1.7", 0))]

    monkeypatch.setattr(cs.socket, "getaddrinfo", _no_hostname_addrs)
    # Routing probe answers with the LAN address (no tailnet): must be "".
    monkeypatch.setattr(cs.socket, "socket",
                        lambda *a, **k: _FakeSock("192.168.1.7"))
    assert cs.tailnet_ip() == ""
    # Routing probe answers inside 100.64/10: that IS the tailnet.
    monkeypatch.setattr(cs.socket, "socket",
                        lambda *a, **k: _FakeSock("100.105.232.26"))
    assert cs.tailnet_ip() == "100.105.232.26"
    # Hostname enumeration alone is enough when it carries a tailnet address.
    monkeypatch.setattr(cs.socket, "getaddrinfo", lambda *a, **k: [
        (None, None, None, None, ("10.0.0.5", 0)),
        (None, None, None, None, ("100.71.2.3", 0))])
    monkeypatch.setattr(cs.socket, "socket",
                        lambda *a, **k: _FakeSock("192.168.1.7"))
    assert cs.tailnet_ip() == "100.71.2.3"
    assert cs.tailnet_ip() != _socket.gethostname()


def test_settings_copy_states_what_enabling_actually_does():
    """The copy used to say "on my Wi-Fi". Enabling binds 0.0.0.0 — every
    interface, VPN and tailnet included — so the copy has to say that."""
    index = (_RENDERER / "index.html").read_text(encoding="utf-8")
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    label = index.split('name="companion_enabled"', 1)[1].split("</label>", 1)[0]
    assert "every network interface" in label.lower()
    assert "wi-fi" not in label.lower(), "the label must not claim Wi-Fi only"
    body = app_js.split("async function _companionRefresh", 1)[1][:4000]
    # Both the off-state and the listening-state copy say it plainly.
    assert "EVERY network interface" in body
    assert "every network interface on this PC" in body
    # And neither claims Wi-Fi exclusivity any more.
    assert "Listening on your Wi-Fi" not in body
    assert "on this Wi-Fi." not in body


def test_renderer_labels_each_address_and_skips_a_missing_tailnet():
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    body = app_js.split("async function _companionRefresh", 1)[1][:4000]
    assert "on this network" in body
    assert "from anywhere (Tailscale)" in body
    # The tailnet row is pushed ONLY when the backend reported one.
    assert "if (s.tailnet_url)" in body
    assert body.index("addrs = [") < body.index("if (s.tailnet_url)")


def test_inter_is_bundled_for_both_surfaces():
    """Bundled so the PC and the phone render the SAME face. Inter is on
    neither OS by default, so a stack alone fell through to Segoe UI here
    and Roboto there."""
    api_font = _STATIC / "fonts" / "InterVariable.woff2"
    desk_font = _RENDERER / "fonts" / "InterVariable.woff2"
    for f in (api_font, desk_font):
        assert f.exists(), f
        assert f.read_bytes()[:4] == b"wOF2", f"{f} is not a woff2"
    # The SAME file on both surfaces — not two different cuts of Inter.
    assert api_font.read_bytes() == desk_font.read_bytes()
    # The OFL requires the license to travel with the font.
    for lic in (_STATIC / "fonts" / "Inter-LICENSE.txt",
                _RENDERER / "fonts" / "Inter-LICENSE.txt"):
        assert lic.exists() and "SIL OPEN FONT LICENSE" in lic.read_text(
            encoding="utf-8").upper()
    # Declared at both ends, covering the full variable weight range.
    css = (_RENDERER / "styles.css").read_text(encoding="utf-8")
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    for sheet, url in ((css, "fonts/InterVariable.woff2"),
                       (html, "/static/fonts/InterVariable.woff2")):
        face = sheet.split("@font-face", 1)[1].split("}", 1)[0]
        assert url in face
        assert "font-weight: 100 900" in face
        assert "format('woff2')" in face
    # And the font actually SERVES from the backend for the phone.
    r = _pc().get("/static/fonts/InterVariable.woff2")
    assert r.status_code == 200 and r.content[:4] == b"wOF2"


def test_pairing_code_requires_the_toggle_first():
    r = _pc().post("/companion/pairing-code")
    assert r.status_code == 400
    assert "Enable the companion first" in r.json()["detail"]


def test_settings_round_trip_of_the_toggle():
    pc = _pc()
    out = pc.post("/settings", json={"companion_enabled": "true"}).json()
    assert out["companion_enabled"] == "true"
    assert settings_service.get_bool_setting("companion_enabled") is True
    out = pc.post("/settings", json={"companion_enabled": "false"}).json()
    assert out["companion_enabled"] == "false"


# --------------------------------------------------------------------------
# 7b. THE ESCALATION PIN — an endpoint allowlist is not confinement
#
# Found by adversarial review of the first cut: /operations/run can make the
# planner STAGE any gate, and /approvals/answer RE-EXECUTES the staged tool.
# Both were allowlisted, so a paired phone could stage a full-state restore
# and then approve it — destroying contacts/deals/memory — even though the
# /snapshots/restore ENDPOINT was refused. Answering is now gated by KIND.
# --------------------------------------------------------------------------

def _stage(reason: str, tool: str = "restore_backup") -> str:
    """A staged approval exactly as stage_from_tool persists one — the state
    a phone-initiated run leaves behind after the planner hits a gate."""
    entry = {
        "id": "appr_" + reason[:8] + "01", "operation_id": "op_staged",
        "command": "staged by a run", "folder": "", "tool": tool,
        "kwargs": {"timestamp": "20260101-000000-000000"}, "reason": reason,
        "question": "Approve?",
        "options": [{"label": "Approve", "value": "approve"},
                    {"label": "Cancel", "value": "cancel"}],
        "gate_flags": {}, "user_stated_numbers": [], "user_provided_emails": [],
        "staged_at": "2026-08-23T09:00:00", "status": "pending",
        "answered_at": "", "outcome": "",
    }
    items = state_store.load_list("approvals")
    items.insert(0, entry)
    state_store.save("approvals", items)
    return entry["id"]


def test_paired_phone_cannot_approve_a_destructive_restore():
    lan, _device = _paired_lan()
    appr_id = _stage("restore_pending")
    r = lan.post("/approvals/answer", headers=HDR,
                 json={"id": appr_id, "value": "approve"})
    assert r.status_code == 403, r.text
    assert "only be answered on the PC" in r.json()["detail"]
    # Still pending — the phone's attempt changed nothing.
    assert any(a["id"] == appr_id for a in _pc().get("/approvals").json()["approvals"])
    # And the PC can still answer it.
    assert _pc().post("/approvals/answer",
                      json={"id": appr_id, "value": "cancel"}).status_code == 200


def test_paired_phone_cannot_approve_contact_merge_or_delete():
    lan, _device = _paired_lan()
    appr_id = _stage("contact_admin_pending", tool="merge_contacts")
    r = lan.post("/approvals/answer", headers=HDR,
                 json={"id": appr_id, "value": "approve"})
    assert r.status_code == 403


def test_paired_phone_may_answer_the_couch_approvals():
    """The point of the feature: approve the invoice from the sofa. These
    reach the real gate path (the response is the gate's own answer)."""
    lan, _device = _paired_lan()
    for reason, tool in (("invoice_plan_pending", "create_quickbooks_invoice"),
                         ("proposal_plan_pending", "write_proposal"),
                         ("research_plan_pending", "research_topic")):
        appr_id = _stage(reason, tool=tool)
        r = lan.post("/approvals/answer", headers=HDR,
                     json={"id": appr_id, "value": "cancel"})
        assert r.status_code == 200, f"{reason}: {r.text}"


def test_approval_kind_allowlist_is_deny_by_default():
    from app.services import companion_service as c
    assert c.device_may_answer_approval("invoice_plan_pending")
    assert not c.device_may_answer_approval("restore_pending")
    assert not c.device_may_answer_approval("contact_admin_pending")
    assert not c.device_may_answer_approval("")
    assert not c.device_may_answer_approval("anything_else_pending")


def test_non_ascii_pairing_code_is_a_wrong_code_not_a_crash():
    """compare_digest raises TypeError on non-ASCII str — a pasted emoji
    must count as a wrong attempt, not a 500."""
    _enable()
    _pc().post("/companion/pairing-code")
    r = _lan().post("/companion/pair", headers=HDR, json={"code": "🔥🔥🔥🔥🔥🔥🔥🔥"})
    assert r.status_code == 403
    assert "wrong pairing code" in r.json()["detail"]
    assert cs._failed_attempts == 1              # it counted toward lockout


def test_status_probes_the_listener_instead_of_trusting_the_setting():
    """restart_required is answered by a real connect to our own LAN socket
    — the settings toggle only takes effect at the next start. Proven in
    BOTH directions on a scratch port this test controls (never the real
    port 8000, where the dev machine's LIVE app may be listening): a
    listener the test opens is found; the same port closed is not."""
    import socket as _s

    _enable()
    if not cs.lan_ip():
        import pytest as _pytest
        _pytest.skip("machine has no LAN address to probe")
    with _s.socket() as srv:
        srv.bind(("0.0.0.0", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        pc = TestClient(app, base_url=f"http://testserver:{port}",
                        client=("127.0.0.1", 50000))
        out = pc.get("/companion/status").json()
        assert out["lan_listening"] is True      # the probe found OUR socket
        assert out["restart_required"] is False
    # Socket closed: the same port now honestly reports not-listening.
    out = pc.get("/companion/status").json()
    assert out["lan_listening"] is False
    assert out["restart_required"] is True
    assert cs.lan_listener_reachable("", 8000) is False
    assert cs.lan_listener_reachable("192.0.2.1", 9) is False   # TEST-NET-1


# --------------------------------------------------------------------------
# 8. Shipped surface — page assets bundled, desktop wiring present
# --------------------------------------------------------------------------

_REPO = __import__("pathlib").Path(__file__).resolve().parents[3]
_STATIC = _REPO / "apps" / "api" / "app" / "static"
_RENDERER = _REPO / "desktop" / "renderer"


def test_companion_assets_live_in_the_bundled_static_dir():
    """app/static ships wholesale into the frozen backend (--add-data), so
    presence HERE is presence in the installer."""
    for name in ("companion.html", "companion-manifest.json",
                 "companion-icon-192.png", "companion-icon-512.png",
                 "companion-icon-maskable.png", "companion-apple-touch.png"):
        assert (_STATIC / name).exists(), name


def test_companion_page_carries_its_contract():
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert "X-Ridian-Companion" in html          # CSRF header on every write
    assert "/companion/manifest.json" in html
    assert "/companion/pair" in html
    assert "text/event-stream" in html           # runs stream like the desktop
    manifest = (_STATIC / "companion-manifest.json").read_text(encoding="utf-8")
    assert '"start_url": "/companion"' in manifest


def test_task_is_the_landing_surface_and_the_field_is_ready():
    """An agent app opens on its command line: Task is the first tab, the
    only section not pre-hidden, and the composer takes focus on boot."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
    buttons = _re.findall(r'data-tab="([a-z]+)"', nav)
    assert buttons[0] == "task", f"Task must be the first tab, got {buttons}"
    assert set(buttons) == {"task", "due", "brief", "approvals"}
    # Exactly the non-task sections ship hidden, so Task is what renders.
    for tab in ("due", "brief", "approvals"):
        assert f'<section id="tab-{tab}" class="hidden">' in html, tab
    assert '<section id="tab-task">' in html
    # ...and the Task nav button is the one marked active.
    assert 'data-tab="task" class="active"' in nav
    # The chat field is focused at boot, not merely present.
    assert '$("task-input").focus({ preventScroll: true })' in html


def test_visual_identity_matches_the_desktop():
    """Same typeface stack, same blue, same mark, same card/button treatment
    — read from the DESKTOP stylesheet, so a token change there that is not
    mirrored here fails this test rather than drifting silently."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    css = (_RENDERER / "styles.css").read_text(encoding="utf-8")

    def token(sheet: str, name: str, scope: str = ":root") -> str:
        block = sheet.split(scope + " {", 1)[1].split("}", 1)[0]
        line = next(l for l in block.splitlines() if l.strip().startswith(name + ":"))
        return line.split(":", 1)[1].strip().rstrip(";")

    # Typeface: the desktop's exact declared stack.
    desktop_font = next(l.split(":", 1)[1].strip().rstrip(";")
                        for l in css.splitlines()
                        if l.strip().startswith("font-family: Inter"))
    assert desktop_font in html, f"font stack drifted from {desktop_font!r}"

    # The blue, the surfaces, the radii — light AND dark.
    for name in ("--color-accent", "--color-surface", "--color-border",
                 "--color-text-strong", "--radius-md", "--shadow-soft",
                 "--gradient-primary"):
        assert f"{name}: {token(css, name)}" in html, f"{name} drifted (light)"
    for name in ("--color-accent", "--color-surface", "--color-bg"):
        want = token(css, name, '[data-theme="dark"]')
        assert f"{name}: {want}" in html, f"{name} drifted (dark)"

    # The sunrise-waves mark, at the desktop's own brand-mark geometry.
    assert 'class="brand-mark" src="/static/companion-icon-192.png"' in html
    assert "width: 32px; height: 32px; border-radius: 9px" in html
    # Button treatment: the .btn family, gradient primary + bordered ghost.
    assert "btn-primary { background: var(--gradient-primary)" in html
    assert "btn-ghost {" in html and "--color-border-strong" in html
    # Cards use the surface/border/radius/shadow set, not ad-hoc colors.
    assert "background: var(--color-surface);" in html


def test_header_names_the_pc_not_just_the_phone():
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert '"Connected to " + pc' in html
    assert "me.pc_name" in html
    # The phone's own name is secondary, appended after the PC.
    assert html.index("me.pc_name") < html.index('me.name ? " · " + me.name')


def test_gate_passes_streaming_responses_through_untouched():
    """The gate is pure-ASGI precisely so operator SSE keeps streaming. A
    real Starlette app behind the real gate: the stream must arrive intact
    for loopback AND for a paired device, and be refused for an unpaired
    one — proving the gate decides BEFORE the stream, then gets out of the
    way."""
    from starlette.applications import Starlette
    from starlette.responses import StreamingResponse
    from starlette.routing import Route

    from app.main import CompanionGate

    async def _ticks(_request):
        async def gen():
            for i in range(3):
                yield f"event: tick\ndata: {i}\n\n".encode()
        return StreamingResponse(gen(), media_type="text/event-stream")

    inner = Starlette(routes=[Route("/morning-brief", _ticks)])
    gated = CompanionGate(inner)
    expected = "".join(f"event: tick\ndata: {i}\n\n" for i in range(3))

    r = TestClient(gated, client=("127.0.0.1", 5000)).get("/morning-brief")
    assert r.status_code == 200 and r.text == expected
    assert r.headers["content-type"].startswith("text/event-stream")

    _enable()
    code = _pc().post("/companion/pairing-code").json()["code"]
    phone = TestClient(gated, base_url=LAN_HOST, client=("192.168.1.50", 40001))
    unpaired = phone.get("/morning-brief")
    assert unpaired.status_code == 401                 # decided before streaming
    token = cs.pair(code, "Pixel 7")["token"]
    phone.cookies.set("ridian_companion", token)
    r = phone.get("/morning-brief")
    assert r.status_code == 200 and r.text == expected


def test_desktop_settings_block_is_wired():
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "'companion_enabled'" in app_js       # in SETTINGS_BOOL_FIELDS
    assert "_companionRefresh" in app_js
    assert "companion/pairing-code" in app_js
    index = (_RENDERER / "index.html").read_text(encoding="utf-8")
    assert 'name="companion_enabled"' in index
    assert 'id="settings-companion-status"' in index


# --------------------------------------------------------------------------
# 9. Honest loading — "Loading..." must END (v6.9.5)
#
# Field bug: over a Tailscale relay the Brief tab sat on "Loading..."
# indefinitely. Two unbounded waits stacked. The page's fetch had no
# deadline, so a dropped/half-open relay connection left the promise
# forever unsettled — the render never fired because there was nothing to
# render, and the catch never fired because nothing rejected. Underneath,
# /morning-brief runs up to ~29 SEQUENTIAL Google/QuickBooks round trips at
# googleapiclient's 60s-per-socket-op default, so a sick source pushed the
# response out for minutes before the per-section try/excepts could fire.
# Both ends are now bounded: every load resolves to content, an honest
# error with a Retry, or an explicit timeout message.
# --------------------------------------------------------------------------


def test_google_service_builders_carry_a_socket_timeout():
    """A stalled Google socket must raise within seconds — surfacing as the
    brief's honest per-section "unreachable" note — never hold the whole
    brief for googleapiclient's 60s-per-socket-op default."""
    from google.oauth2.credentials import Credentials

    from app.services import calendar_service, gmail_service, inbox_service

    creds = Credentials(token="t")
    for mod in (inbox_service, calendar_service, gmail_service):
        svc = mod._build_service(creds)
        # AuthorizedHttp wraps the actual httplib2.Http carrying the timeout.
        assert svc._http.http.timeout == mod._HTTP_TIMEOUT_SECONDS, mod.__name__
        assert 5 <= mod._HTTP_TIMEOUT_SECONDS <= 30, mod.__name__


def test_companion_fetch_has_a_deadline_and_the_stream_does_not():
    """api() aborts a fetch that will never settle and names the state; the
    SSE run stream gets a deadline on the HANDSHAKE only — a live run is
    open-ended by design, so the timer is released before the first read."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert "AbortController" in html
    assert "READ_TIMEOUT_MS" in html and "WRITE_TIMEOUT_MS" in html
    # Reads say the PC went quiet; writes are honest about the unknown.
    assert "did not answer within" in html
    assert "may still have completed" in html
    stream_fn = html.split("async function streamRun", 1)[1].split(
        "\nfunction ", 1)[0]
    assert "signal: ctrl.signal" in stream_fn
    assert stream_fn.index("clearTimeout") < stream_fn.index("getReader")


def test_every_loader_fails_to_a_retry_not_a_dead_end():
    """"Loading..." is a transit state, never a destination: each tab's
    catch renders the shared failState (message + Retry), and a boot
    failure that is not the pairing 401 may not strand a blank page."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert "function failState" in html and ">Retry</button>" in html
    for target, retry in (("due-list", "loadDue"),
                          ("brief-body", "loadBrief"),
                          ("appr-list", "loadApprovals"),
                          ("awaiting-list", "loadAwaiting")):
        assert f'failState("{target}", e, {retry})' in html, target
    assert 'id="boot-view"' in html
    assert "location.reload()" in html


# --------------------------------------------------------------------------
# 10. Web Push (v6.9.7) — device-bound subscriptions, honest surfaces
# --------------------------------------------------------------------------

def _enable_push():
    settings_service.save_settings({"companion_push_enabled": "true"})


def test_paired_phone_can_subscribe_and_unsubscribe_its_own_device():
    from app.services import push_service
    lan, device_id = _paired_lan()
    _enable_push()
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
           "keys": {"p256dh": "pk", "auth": "ak"}}
    r = lan.post("/companion/push/subscribe", headers=HDR, json=sub)
    assert r.status_code == 200, r.text
    assert push_service.device_is_subscribed(device_id)
    r = lan.post("/companion/push/unsubscribe", headers=HDR, json={})
    assert r.status_code == 200 and r.json()["removed"] is True
    assert not push_service.device_is_subscribed(device_id)


def test_subscribe_refused_from_loopback_when_disabled_and_when_unpaired():
    lan, _device = _paired_lan()
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
           "keys": {"p256dh": "pk", "auth": "ak"}}
    # Push toggle off: an honest 400, not a stored-but-dead subscription.
    r = lan.post("/companion/push/subscribe", headers=HDR, json=sub)
    assert r.status_code == 400 and "switched off" in r.json()["detail"]
    _enable_push()
    # Loopback has no device record to attach a subscription to.
    r = _pc().post("/companion/push/subscribe", json=sub)
    assert r.status_code == 400 and "paired phone" in r.json()["detail"]
    # Unpaired off-box: the gate refuses before the route runs.
    assert _lan().post("/companion/push/subscribe", headers=HDR,
                       json=sub).status_code == 401


def test_service_worker_is_served_to_paired_devices_with_scope_header():
    lan, _device = _paired_lan()
    r = lan.get("/companion-sw.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/companion"
    assert "notificationclick" in r.text
    assert _lan().get("/companion-sw.js").status_code == 401


def test_host_header_admits_exactly_our_own_tsnet_name(monkeypatch):
    """The HTTPS listener (Web Push needs a secure context) serves at this
    machine's ts.net name — the ONE non-IP Host admitted. Anything else is
    still DNS-rebinding and still refused."""
    from app.services import companion_tls
    assert not cs.host_header_ok("razerblade.tail1234.ts.net:8443")
    monkeypatch.setitem(companion_tls.state, "host", "razerblade.tail1234.ts.net")
    assert cs.host_header_ok("razerblade.tail1234.ts.net:8443")
    assert cs.host_header_ok("RAZERBLADE.tail1234.ts.net")
    assert not cs.host_header_ok("evil.example.com:8443")
    assert not cs.host_header_ok("razerblade.tail1234.ts.net.evil.com")


def test_status_and_me_carry_the_push_state(monkeypatch, tmp_path):
    from app.services import push_service
    monkeypatch.setattr(push_service, "VAPID_PATH",
                        tmp_path / "vapid.bin")
    lan, _device = _paired_lan()
    # Enabling the toggle through /settings generates the DPAPI-wrapped key.
    out = _pc().post("/settings",
                     json={"companion_push_enabled": "true"}).json()
    assert out["companion_push_enabled"] == "true"
    raw = (tmp_path / "vapid.bin").read_bytes()
    assert raw.startswith(b"RIDIAN-DPAPI-1\n") and b"BEGIN" not in raw
    status = _pc().get("/companion/status").json()
    assert status["push"]["enabled"] is True
    assert status["push"]["vapid_ready"] is True
    assert status["push"]["subscribed_devices"] == 0
    assert "last_error" in status["push"] and "https_error" in status
    me = lan.get("/companion/me").json()
    assert me["push"]["enabled"] is True
    assert me["push"]["key"] and me["push"]["subscribed"] is False


def test_companion_page_wires_push_honestly():
    """The page offers push only where it can exist, says so where it
    cannot (plain-http origin), and lands notification taps on their tab."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert 'id="push-card"' in html and "setupPush" in html
    assert "isSecureContext" in html          # names the HTTPS requirement
    assert "https://&hellip;ts.net" in html
    assert 'register("/companion-sw.js"' in html
    assert '{ scope: "/companion" }' in html
    assert "applicationServerKey" in html and "userVisibleOnly" in html
    # Tap-to-tab: SW message when open, #hash when opened by the tap.
    assert "location.hash.slice(1)" in html
    assert 'navigator.serviceWorker.addEventListener("message"' in html
    # The SW itself: push + click handlers, tag dedup, and NO fetch handler
    # (the page's honest loading states must never be masked by a cache).
    sw = (_STATIC / "companion-sw.js").read_text(encoding="utf-8")
    assert 'addEventListener("push"' in sw
    assert 'addEventListener("notificationclick"' in sw
    assert "openWindow" in sw and '"/companion#" + tab' in sw
    assert "tag" in sw
    assert 'addEventListener("fetch"' not in sw


def test_due_tab_findings_come_from_the_cache_only(monkeypatch):
    """v6.9.8: the Due tab is a LOCAL read. Findings are the last completed
    evaluation (stamped), never a live QBO/Gmail pull — the arm's-length
    'Loading forever' class must not come back through this door."""
    import datetime as dt

    from app.services import watch_service
    with watch_service._cache_lock:
        watch_service._cache.update(
            {"findings": [], "computed_at": "", "unavailable": {}})

    def _explode(*_a, **_k):
        raise AssertionError("/obligations must never reach a watch source")
    monkeypatch.setattr(watch_service, "gather_and_evaluate", _explode)
    lan, _device = _paired_lan()
    out = lan.get("/obligations").json()["findings"]
    assert out["computed_at"] == "" and out["findings"] == []
    watch_service.evaluate_with(
        today=dt.date(2026, 9, 1),
        deals=[{"id": "d3", "title": "Quiet", "stage": "contacted",
                "last_touch_iso": "2026-08-01T09:00:00"}])
    out = lan.get("/obligations").json()["findings"]
    assert out["computed_at"] and len(out["findings"]) == 1
    assert out["findings"][0]["kind"] == "deal_quiet"


def test_findings_render_distinguished_on_both_surfaces():
    """Item 5: an obligation is a commitment, a finding is something
    Ridian noticed — the NOTICED chip and accent (never warn/error)
    treatment mark the difference on the phone AND the desktop brief."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert 'id="noticed-list"' in html
    assert '<span class="badge notice">NOTICED</span>' in html
    notice_css = html.split(".badge.notice {", 1)[1].split("}", 1)[0]
    assert "var(--color-accent-soft)" in notice_css
    assert "warn" not in notice_css and "error" not in notice_css
    assert ".card.finding" in html
    assert '"ridian_noticed", "Ridian noticed"' in html   # brief section
    # Honest freshness: the Due tab names the evaluation time or its absence.
    assert "not evaluated yet" in html
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "'Ridian noticed'" in app_js
    assert ">NOTICED</span>" in app_js
    index = (_RENDERER / "index.html").read_text(encoding="utf-8")
    assert 'name="watch_push_enabled"' in index
    assert 'name="watch_deal_quiet_days"' in index
    assert 'name="watch_invoice_grace_days"' in index


def test_desktop_settings_carry_the_push_toggle_and_status():
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "'companion_push_enabled'" in app_js
    assert "_companionPushStatus" in app_js
    assert "PUSH PROBLEM" in app_js           # failure always names itself
    index = (_RENDERER / "index.html").read_text(encoding="utf-8")
    assert 'name="companion_push_enabled"' in index
    assert 'id="settings-push-status"' in index


# --------------------------------------------------------------------------
# 11. v6.9.10 — the running version is readable, failures name their fix,
#     and same-named pairings read apart (and prune when long dead)
# --------------------------------------------------------------------------

def test_version_is_stamped_on_every_surface(monkeypatch):
    """One source of truth (the supervisor's RIDIAN_APP_VERSION): /health
    for the window title + Settings, /companion/me for the phone, and the
    PAGE ITSELF stamped at serve time — a phone showing an old number is
    provably on a stale cached copy."""
    monkeypatch.setenv("RIDIAN_APP_VERSION", "9.9.9-test")
    assert _pc().get("/health").json()["app_version"] == "9.9.9-test"
    lan, _device = _paired_lan()
    assert lan.get("/companion/me").json()["app_version"] == "9.9.9-test"
    page = lan.get("/companion")
    assert "Ridian Companion v9.9.9-test" in page.text
    assert "__RIDIAN_VERSION__" not in page.text     # every placeholder filled
    assert page.headers["cache-control"] == "no-cache"
    assert _pc().get("/companion/status").json()["app_version"] == "9.9.9-test"
    # The page detects ITS OWN staleness: stamped const vs live /me value.
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert 'const PAGE_VERSION = "__RIDIAN_VERSION__"' in html
    assert "old cached page" in html and 'id="version-line"' in html
    # Desktop: title + Settings header fed from /health's app_version.
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "_applyVersion" in app_js and "document.title" in app_js
    assert 'id="settings-version"' in (_RENDERER / "index.html").read_text(
        encoding="utf-8")


def test_tls_failures_name_the_fix_not_just_the_condition():
    """"Tailscale is NoState, not Running" was honest but inert. Every
    operator-facing failure string in companion_tls now carries an action
    — what to start, install, or enable, and then restart Ridian."""
    src = (_REPO / "apps" / "api" / "app" / "services"
           / "companion_tls.py").read_text(encoding="utf-8")
    assert src.count("restart Ridian") >= 6
    assert "start Tailscale from" in src
    assert "install it from tailscale.com" in src
    assert "enable DNS → MagicDNS" in src
    assert "HTTPS Certificates in the Tailscale admin" in src
    assert "set RIDIAN_TLS_PORT" in src
    # And the live no-tailscale path carries its action end to end.
    from app.services import companion_tls
    import unittest.mock as _mock
    with _mock.patch.object(companion_tls, "_tailscale_exe", lambda: None):
        with pytest.raises(RuntimeError, match="then restart Ridian"):
            companion_tls.ensure_cert()


def test_last_seen_persists_daily_so_pairings_read_apart():
    lan, device_id = _paired_lan()
    assert lan.get("/obligations").status_code == 200   # verify_token ran
    stored = next(d for d in state_store.load_list("companion_devices")
                  if d["id"] == device_id)
    first_stamp = stored["last_seen_iso"]
    assert first_stamp                                  # persisted...
    snaps = [s["id"] for s in state_store.list_snapshots()]
    assert lan.get("/obligations").status_code == 200
    # ...but throttled: a second request the same day writes NOTHING.
    assert [s["id"] for s in state_store.list_snapshots()] == snaps
    stored = next(d for d in state_store.load_list("companion_devices")
                  if d["id"] == device_id)
    assert stored["last_seen_iso"] == first_stamp
    # A restart (memory gone) still shows the persisted stamp.
    cs._last_seen.clear()
    row = next(d for d in cs.list_devices() if d["id"] == device_id)
    assert row["last_seen_iso"] == first_stamp and row["created_iso"]


def test_stale_pairings_prune_at_operator_moments():
    """Three "Pixel 7"s from re-pair testing: the two long-dead ones (whose
    30-day cookies expired weeks ago) vanish the next time a pairing code
    is generated; anything recent, in-session, or unparseable is kept."""
    _enable()
    state_store.save("companion_devices", [
        {"id": "cd_dead0000001", "name": "Pixel 7", "token_sha256": "x",
         "created_iso": "2026-05-01T09:00:00",
         "last_seen_iso": "2026-06-01T09:00:00"},      # unseen ~90d: prune
        {"id": "cd_dead0000002", "name": "Pixel 7", "token_sha256": "x",
         "created_iso": "2026-05-01T09:00:00"},        # never seen: prune
        {"id": "cd_live0000001", "name": "Pixel 7", "token_sha256": "x",
         "created_iso": "2026-08-30T09:00:00",
         "last_seen_iso": "2026-08-31T09:00:00"},      # recent: keep
        {"id": "cd_weird000001", "name": "Old shape", "token_sha256": "x",
         "created_iso": "not-a-date"},                 # unparseable: keep
        {"id": "cd_mem0000001", "name": "In session", "token_sha256": "x",
         "created_iso": "2026-01-01T09:00:00"},        # seen THIS session
    ])
    cs._last_seen["cd_mem0000001"] = cs._now()
    _pc().post("/companion/pairing-code")
    kept = {d["id"] for d in state_store.load_list("companion_devices")}
    assert kept == {"cd_live0000001", "cd_weird000001", "cd_mem0000001"}
    # The Settings row copy distinguishes what remains.
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "paired ${paired}" in app_js and "never seen" in app_js


def test_arms_length_type_and_touch_targets():
    """READABILITY PIN: the phone is read at arm's length — >=17px body
    text, >=48px touch height on the tabs and every button, and the
    eyebrow/meta lines at real text contrast. The PALETTE stays the
    desktop's exactly (test_visual_identity_matches_the_desktop); only the
    phone's own type-scale/touch tokens differ."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert "--fs-body: 1.25rem" in html           # 20px at the default root
    assert "--touch-min: 48px" in html
    nav_css = html.split("nav button {", 1)[1].split("}", 1)[0]
    assert "min-height: var(--touch-min)" in nav_css
    btn_css = html.split(".btn {", 1)[1].split("}", 1)[0]
    assert "min-height: var(--touch-min)" in btn_css
    # The header eyebrow (CONNECTED TO ...) reads at full text color.
    sub_css = html.split(".brand-sub {", 1)[1].split("}", 1)[0]
    assert "var(--color-text)" in sub_css
    assert "muted" not in sub_css
    # Card meta lines ("next: 2026-09-01") scan at note size in the
    # stronger muted, not the faded muted-soft.
    meta_css = html.split(".meta {", 1)[1].split("}", 1)[0]
    assert "var(--color-muted)" in meta_css
    assert "var(--fs-sm)" in meta_css
    assert "var(--color-muted-soft)" not in meta_css


def test_companion_brief_names_the_sender_like_the_desktop():
    """FIELD BUG (v6.9.6): the companion's needs_reply line read
    i.from_name / i.from_email — keys that exist on NO row that
    normalize_thread/classify produce — so every item rendered as an
    orphaned ": Subject" while the desktop (reading contact.name /
    last_from) named the sender fine. Pin the companion to the row's REAL
    keys, to the desktop's precedence (contact join first, then the bare
    address), and to subject-alone when there is no sender."""
    from app.services import inbox_service

    raw = {"id": "t1", "messages": [{
        "internalDate": "1756600000000",
        "snippet": "renewal is due",
        "payload": {"headers": [
            {"name": "Subject", "value": "Business License"},
            {"name": "From",
             "value": "Dorothy de la Parra <dorothy@example.com>"},
            {"name": "To", "value": "ryan@ridiantechnologies.com"},
        ]},
    }]}
    row = inbox_service.normalize_thread(raw, "ryan@ridiantechnologies.com")
    item = inbox_service.classify([row], contacts={
        "dorothy@example.com": {"name": "Dorothy de la Parra",
                                "contact_id": "c1", "in_pipeline": False},
    })["needs_reply"][0]
    assert item["contact"]["name"] == "Dorothy de la Parra"
    assert item["last_from"] == "dorothy@example.com"
    assert item["subject"] == "Business License"

    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    entry = html.split('["needs_reply"', 1)[1].split("],", 1)[0]
    # Every i.<key> the formatter reads must exist on the real row.
    for key in set(_re.findall(r"\bi\.(\w+)", entry)):
        assert key in item, f"companion reads i.{key}, which is not on the row"
    # The desktop's precedence: contact join names the sender, else address.
    assert entry.index("contact") < entry.index("last_from")
    # The phantom keys are gone for good.
    assert "from_name" not in html and "from_email" not in html
    # No sender: the subject alone — never an orphaned ": Subject".
    assert 'who ? ' in entry and ': (i.subject || "")' in entry


def test_second_type_step_briefs_and_tabs_read_at_arms_length():
    """v6.9.6: the operator's Android font scale is already raised, so the
    APP carries the size — 20px body, 16px section eyebrows at full text
    color, brief items in strong color, and a four-tab bar (no hamburger)
    with an icon above each label and an unmistakable active state. Still
    rem-based so the system setting multiplies on top."""
    html = (_STATIC / "companion.html").read_text(encoding="utf-8")
    assert "--fs-body: 1.25rem" in html
    assert "--fs-eyebrow: 1rem" in html
    h2 = html.split("\nh2 {", 1)[1].split("}", 1)[0]
    assert "var(--fs-eyebrow)" in h2 and "var(--color-text)" in h2
    assert "muted" not in h2
    # Brief card text is primary reading: strong, never the dimmest thing.
    assert "#brief-body .card { color: var(--color-text-strong); }" in html
    # Four tabs stay, each with an icon above its label.
    nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert len(_re.findall(r'data-tab="[a-z]+"', nav)) == 4
    assert nav.count("<svg") == 4
    btn = html.split("nav button {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" in btn
    assert "var(--fs-eyebrow)" in btn                # 16px labels
    assert "border-top: 3px" in btn
    # The active tab reads at a glance: accent text + bar + soft fill.
    active = html.split("nav button.active {", 1)[1].split("}", 1)[0]
    assert "var(--color-accent)" in active
    assert "var(--color-accent-soft)" in active


# --------------------------------------------------------------------------
# 13. Owner Snapshot v1 export is PC-only
# --------------------------------------------------------------------------

def test_paired_phone_cannot_export_the_owner_snapshot(monkeypatch, tmp_path):
    """The snapshot is a local file for the operator to inspect and move by
    hand. It is absent from the companion allowlist, so a paired phone with
    a valid cookie AND the CSRF header gets the generic 403 — and no
    exports/ directory is created."""
    from app.services import owner_snapshot_service
    monkeypatch.setattr(owner_snapshot_service, "data_dir", lambda: tmp_path)
    lan, _device = _paired_lan()
    r = lan.post("/owner-snapshot/export", headers=HDR)
    assert r.status_code == 403
    assert "not available from a companion device" in r.json()["detail"]
    assert not (tmp_path / "exports").exists()
