"""Ambient watch (v6.9.8) — surfaces, never acts; noticed once, not daily.

The pins that matter:
  1. SURFACES NEVER ACTS, twice over: an AST allowlist on this module's
     imports AND on every attribute it touches on them (a write function
     never named is a write function never callable), plus the brief-style
     no-writes pin (state bytes identical AND the snapshot list unchanged).
  2. OCCURRENCE-STABLE KEYS: a quiet deal re-keys only when its activity
     stamp moves; a paid-late invoice keys on (env, id, due_date); a CC'd
     contact is not a sender; sandbox invoices never push.
  3. The Due tab reads ONLY the cache — never a live QBO/Gmail pull.
"""
import ast
import datetime as dt
from pathlib import Path

import pytest

from app.services import settings_service, state_store, watch_service

TODAY = dt.date(2026, 9, 1)
LIMITS = {"deal_quiet_days": 14, "invoice_grace_days": 3}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH",
                        tmp_path / "local_settings.json")
    with watch_service._cache_lock:
        watch_service._cache.update(
            {"findings": [], "computed_at": "", "unavailable": {}})
    yield


# --------------------------------------------------------------------------
# 1. The rules, pure
# --------------------------------------------------------------------------

def test_invoice_rule_grace_env_and_key():
    invoices = [
        {"id": "9", "doc_number": "1042", "customer": "Sandy",
         "due_date": "2026-08-20", "balance": 4500.0},   # 12d past due
        {"id": "8", "doc_number": "1041", "customer": "Close",
         "due_date": "2026-08-30", "balance": 100.0},    # inside 3d grace
        {"id": "7", "doc_number": "1040", "customer": "Paid",
         "due_date": "2026-08-01", "balance": 0},        # settled
    ]
    out = watch_service.findings_from(today=TODAY, limits=LIMITS,
                                      invoices=invoices,
                                      invoice_env="production")
    assert [f["key"] for f in out] == ["watch:inv:production:9:2026-08-20"]
    assert out[0]["push"] is True
    # Sandbox books surface but NEVER interrupt a phone.
    out = watch_service.findings_from(today=TODAY, limits=LIMITS,
                                      invoices=invoices[:1],
                                      invoice_env="sandbox")
    assert out[0]["push"] is False and "[sandbox]" in out[0]["detail"]


def test_deal_rule_uses_latest_activity_and_never_fires_without_basis():
    deals = [
        # Touch old, but EDITED recently: being worked, not quiet.
        {"id": "d1", "title": "Worked", "stage": "proposal",
         "last_touch_iso": "2026-07-01T09:00:00",
         "created_iso": "2026-06-01T09:00:00",
         "updated_iso": "2026-08-30T09:00:00"},
        # Never touched, created yesterday: NOT quiet (no 14 days elapsed).
        {"id": "d2", "title": "New", "stage": "lead",
         "last_touch_iso": "", "created_iso": "2026-08-31T09:00:00"},
        # Genuinely quiet on every stamp.
        {"id": "d3", "title": "Quiet", "stage": "contacted",
         "last_touch_iso": "2026-08-10T09:00:00",
         "created_iso": "2026-08-01T09:00:00",
         "updated_iso": "2026-08-10T10:00:00"},
        # Quiet but WON: inactive stages never fire.
        {"id": "d4", "title": "Won", "stage": "won",
         "last_touch_iso": "2026-01-01T09:00:00"},
        # No stamps at all: no basis, no finding.
        {"id": "d5", "title": "Blank", "stage": "lead",
         "last_touch_iso": "", "created_iso": ""},
    ]
    out = watch_service.findings_from(today=TODAY, limits=LIMITS, deals=deals)
    assert [f["key"] for f in out] == ["watch:deal:d3:2026-08-10T10:00:00"]
    assert out[0]["push"] is True
    # A new touch moves the stamp — a NEW key, so a later quiet stretch
    # notifies again; the same stretch never re-notifies (item 3's promise).
    deals[2]["last_touch_iso"] = "2026-09-01T08:00:00"
    out2 = watch_service.findings_from(today=TODAY + dt.timedelta(days=20),
                                       limits=LIMITS, deals=[deals[2]])
    assert out2[0]["key"] == "watch:deal:d3:2026-09-01T08:00:00"


def test_mail_rule_requires_the_sender_to_be_the_deal_contact():
    base = {"id": "t1", "subject": "Business License", "from_me": False,
            "last_message_at": "2026-09-01T08:30",
            "contact": {"name": "Dorothy", "email": "dorothy@x.com",
                        "in_pipeline": True}}
    hit = watch_service.findings_from(
        today=TODAY, limits=LIMITS,
        needs_reply=[{**base, "last_from": "dorothy@x.com"}])
    assert [f["kind"] for f in hit] == ["inbound_match"]
    assert hit[0]["push"] is False            # Gmail already notified the phone
    # CC'd pipeline contact, stranger sender: NOT a match.
    assert watch_service.findings_from(
        today=TODAY, limits=LIMITS,
        needs_reply=[{**base, "last_from": "stranger@y.com"}]) == []
    # Operator spoke last / no active deal: not findings either.
    assert watch_service.findings_from(
        today=TODAY, limits=LIMITS,
        needs_reply=[{**base, "last_from": "dorothy@x.com",
                      "from_me": True}]) == []


def test_missed_obligation_covers_once_and_supersession():
    rows = [
        {"id": "ob1", "name": "Annual filing", "status": "overdue",
         "due_date": "2026-08-15", "days_overdue": 17, "missed_periods": 0,
         "cadence": {"kind": "once"}},
        {"id": "ob2", "name": "Weekly invoice run", "status": "overdue",
         "due_date": "2026-08-31", "days_overdue": 1, "missed_periods": 2,
         "cadence": {"kind": "weekly", "weekday": 0}},
        {"id": "ob3", "name": "Merely due", "status": "due_today",
         "due_date": "2026-09-01", "missed_periods": 0,
         "cadence": {"kind": "weekly", "weekday": 1}},
    ]
    out = watch_service.findings_from(today=TODAY, limits=LIMITS,
                                      obligations_due=rows)
    assert [f["key"] for f in out] == ["watch:obmiss:ob1:2026-08-15",
                                      "watch:obmiss:ob2:2026-08-31"]
    # Never a push: the existing ob:<id>:<due_date> key already covers the
    # rollover — a second ping about the same state is a duplicate.
    assert all(f["push"] is False for f in out)


def test_none_inputs_mean_absent_rules_not_empty_claims():
    snapshot = watch_service.evaluate_with(
        today=TODAY, deals=None, invoices=None, needs_reply=None,
        obligations_due=None,
        unavailable={"invoices": "QuickBooks unreachable",
                     "mail": "Gmail unreachable"})
    section = watch_service.brief_section(snapshot)
    assert section["unavailable"] is True
    assert "QuickBooks unreachable" in section["note"]


# --------------------------------------------------------------------------
# 2. Cache — the Due tab never pulls live
# --------------------------------------------------------------------------

def test_cached_serves_last_evaluation_and_never_fetches(monkeypatch):
    def _explode(*_a, **_k):
        raise AssertionError("cached() must never reach a source")
    for mod, fn in ((watch_service.quickbooks_service, "list_unpaid_invoices"),
                    (watch_service.inbox_service, "triage"),
                    (watch_service.pipeline_service, "list_deals")):
        monkeypatch.setattr(mod, fn, _explode)
    empty = watch_service.cached()
    assert empty["computed_at"] == ""        # honest: not evaluated yet
    watch_service.evaluate_with(today=TODAY, deals=[], invoices=[],
                                needs_reply=[], obligations_due=[])
    out = watch_service.cached()
    assert out["computed_at"] and out["findings"] == []


def test_push_candidates_are_the_push_tier_only(monkeypatch):
    monkeypatch.setattr(watch_service, "gather_and_evaluate", lambda today=None: {
        "findings": [
            {"kind": "invoice_overdue", "key": "watch:inv:production:9:2026-08-20",
             "push": True, "title": "Invoice #1042 is past due", "detail": "d",
             "tab": "due"},
            {"kind": "inbound_match", "key": "watch:mail:t1:x", "push": False,
             "title": "mail", "detail": "d", "tab": "due"},
        ], "computed_at": "2026-09-01T08:00:00", "unavailable": {}})
    pairs = watch_service.push_candidates(TODAY)
    assert [k for k, _p in pairs] == ["watch:inv:production:9:2026-08-20"]
    # The kill-switch silences the watch tier WITHOUT touching core pushes.
    settings_service.save_settings({"watch_push_enabled": "false"})
    assert watch_service.push_candidates(TODAY) == []


# --------------------------------------------------------------------------
# 3. Surfaces, never acts — pinned by source introspection
# --------------------------------------------------------------------------

_ALLOWED_IMPORTS = {"__future__", "datetime", "logging", "threading", "typing"}
_ALLOWED_SERVICES = {"inbox_service", "obligations_service", "pipeline_service",
                     "quickbooks_service", "settings_service"}
_ALLOWED_ATTRS = {
    "pipeline_service": {"ACTIVE_STAGES", "list_deals"},
    "quickbooks_service": {"list_unpaid_invoices", "get_environment"},
    "inbox_service": {"triage"},
    "obligations_service": {"due_obligations"},
    "settings_service": {"get_int_setting", "get_bool_setting"},
}


def _watch_tree():
    src = (Path(watch_service.__file__)).read_text(encoding="utf-8")
    return ast.parse(src)


def test_watch_imports_no_tools_pinned_by_ast():
    """The import allowlist is EXACT: no operator_tools, no artifact /
    email / document / gmail-compose modules, no state_store — nothing
    act-capable is even importable without failing this pin."""
    tree = _watch_tree()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                    # relative: the service names
                imported.update(a.name for a in node.names)
            else:
                imported.add(node.module or "")
    assert imported == _ALLOWED_IMPORTS | _ALLOWED_SERVICES, imported


def test_watch_calls_only_read_attributes_pinned_by_ast():
    """An allowlisted module still exports writes (create_invoice,
    mark_complete, save_settings...). This pin closes that hole: every
    attribute watch_service touches on a service is named here, and every
    named attribute is a read."""
    tree = _watch_tree()
    used: dict[str, set] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in _ALLOWED_SERVICES):
            used.setdefault(node.value.id, set()).add(node.attr)
    for mod, attrs in used.items():
        assert attrs <= _ALLOWED_ATTRS[mod], (mod, attrs)
    forbidden = ("create", "send", "save", "delete", "update", "add_",
                 "mark_", "begin", "disconnect", "restore")
    for attrs in used.values():
        for attr in attrs:
            assert not any(f in attr for f in forbidden), attr


def test_watch_never_writes_state(monkeypatch, tmp_path):
    """Both halves of the brief pin: bytes identical AND no new snapshot
    (state_store.save snapshots first, so an identical rewrite would still
    show up there). Scope: the state store — the underlying Google/QBO
    READ clients refresh their own token files by their own rules."""
    state_store.save("deals", [{"id": "d3", "title": "Quiet",
                                "stage": "contacted",
                                "last_touch_iso": "2026-08-10T09:00:00"}])
    monkeypatch.setattr(watch_service.pipeline_service, "list_deals",
                        lambda: state_store.load_list("deals"))
    monkeypatch.setattr(watch_service.quickbooks_service, "list_unpaid_invoices",
                        lambda limit=100: [{"id": "9", "doc_number": "1042",
                                            "customer": "S", "balance": 10.0,
                                            "due_date": "2026-08-01"}])
    monkeypatch.setattr(watch_service.quickbooks_service, "get_environment",
                        lambda: "production")
    monkeypatch.setattr(watch_service.inbox_service, "triage",
                        lambda **kw: {"needs_reply": []})
    monkeypatch.setattr(watch_service.obligations_service, "due_obligations",
                        lambda today=None: [])
    before = {p.name: p.read_bytes()
              for p in sorted(state_store.STATE_DIR.glob("*.json"))}
    snaps_before = [s["id"] for s in state_store.list_snapshots()]
    out = watch_service.gather_and_evaluate(TODAY)
    assert len(out["findings"]) == 2          # it really evaluated
    after = {p.name: p.read_bytes()
             for p in sorted(state_store.STATE_DIR.glob("*.json"))}
    assert after == before
    assert [s["id"] for s in state_store.list_snapshots()] == snaps_before


def test_one_sick_source_never_blinds_the_rest(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("socket stall")
    monkeypatch.setattr(watch_service.quickbooks_service,
                        "list_unpaid_invoices", _boom)
    monkeypatch.setattr(watch_service.inbox_service, "triage", _boom)
    monkeypatch.setattr(watch_service.pipeline_service, "list_deals",
                        lambda: [{"id": "d3", "title": "Quiet",
                                  "stage": "contacted",
                                  "last_touch_iso": "2026-08-10T09:00:00"}])
    monkeypatch.setattr(watch_service.obligations_service, "due_obligations",
                        lambda today=None: [])
    out = watch_service.gather_and_evaluate(TODAY)
    assert [f["kind"] for f in out["findings"]] == ["deal_quiet"]
    assert set(out["unavailable"]) == {"invoices", "mail"}
