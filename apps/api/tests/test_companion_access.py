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

def test_status_reports_devices_and_restart_state(monkeypatch):
    """restart_required comes from the PROBE, not from RIDIAN_BOUND_HOST:
    an env var claiming 0.0.0.0 must NOT make the UI promise reachability
    that no socket backs (the probe pin lives in
    test_status_probes_the_listener_instead_of_trusting_the_setting)."""
    lan, device_id = _paired_lan()
    out = _pc().get("/companion/status").json()
    assert out["enabled"] is True
    assert out["devices"][0]["id"] == device_id
    assert out["devices"][0]["name"] == "Pixel 7"
    assert out["url"].endswith("/companion")
    monkeypatch.setenv("RIDIAN_BOUND_HOST", "0.0.0.0")
    out = _pc().get("/companion/status").json()
    assert out["bound_host"] == "0.0.0.0"        # reported, not trusted
    assert out["lan_listening"] is False
    assert out["restart_required"] is True


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
    — the settings toggle only takes effect at the next start."""
    _enable()
    out = _pc().get("/companion/status").json()
    # Nothing is actually listening in-process under TestClient.
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
