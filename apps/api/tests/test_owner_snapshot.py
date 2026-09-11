"""Owner Snapshot v1 — the export is allowlisted, local, and read-only.

Pins (the same shape as the other gates):
  1. SOURCES: an AST allowlist on the module's imports AND on every attribute
     it touches on the services — a writer never named is a writer never
     callable; no file is opened.
  2. FIELDS: (a) no output key matches the forbidden pattern; (b) no value
     equals or contains a configured secret; (c) no value carries the data
     dir or a drive-letter path; (d) the document round-trips through the
     committed JSON Schema; (e) denied fields never appear even when the
     source records carry them; (f) a MUTATED allowlist is refused, not
     exported; (g) free text has emails/phones replaced, and a MUTATED scrub
     is refused, not exported; (h) every timestamp is UTC RFC 3339 with Z.
  3. NO WRITES: state bytes identical, backups list unchanged, and exactly
     one file appears, under <data_dir>/exports/.
"""
import ast
import datetime as dt
import json
import re
from pathlib import Path

import jsonschema
import pydantic
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services import (brief_service, google_drive_service,
                          quickbooks_service, settings_service, state_store,
                          watch_service)
from app.services import owner_snapshot_service as svc

_REAL_BUILD_BRIEF = brief_service.build_brief   # captured before any patch
_DOCS = Path(__file__).resolve().parents[3] / "docs"
SCHEMA_PATH = _DOCS / "owner-snapshot-v1.schema.json"
SERVICE_PATH = Path(svc.__file__)
UTC = dt.timezone.utc

SECRETS = {
    "anthropic_api_key": "sk-ant-TESTSECRET-alpha-0001",
    "openai_api_key": "sk-TESTSECRET-bravo-0002",
    "smtp_password": "smtp-TESTSECRET-charlie-0003",
    "quickbooks_client_secret": "qbo-TESTSECRET-delta-0004",
}
TOKEN_SECRET = "REFRESH-TESTSECRET-echo-0005"
LOCAL_PATH = r"C:\Users\ryan\Documents\client-plan.pdf"
SENTINELS = {
    "email": "sarah@example.test",
    "phone": "+1 251 555 0100",
    "notes": "NOTES-SENTINEL private observation",
    "value_usd": "48000.00",
    "steps": "STEPS-SENTINEL transcript line",
    "receipt": "RECEIPT-SENTINEL final text",
    "kwargs": "KWARGS-SENTINEL",
    "options": "OPTIONS-SENTINEL",
    "task": "TASK-SENTINEL invoice the chamber",
    "touches": "TOUCH-SENTINEL",
    "artifact_folder": r"C:\Users\ryan\AppData\Roaming\Ridian Operator\outputs\20260910-090000_run",
}
BRIEF_ROW_SENTINELS = ("INVOICE-ROW-SENTINEL", "THREAD-ROW-SENTINEL",
                       "EVENT-ROW-SENTINEL")
# Real-looking contact details that must never travel inside free text.
FREE_TEXT_EMAIL = "marcus.delacroix@gulfshoreschamber.org"
FREE_TEXT_PHONE_A = "(251) 555-0142"
FREE_TEXT_PHONE_B = "251-555-0199"
FREE_TEXT_PHONE_C = "+1 251.555.0177"
COMMAND_TEXT = (f"Draft a check-in to Marcus Delacroix at {FREE_TEXT_EMAIL} "
                f"or call {FREE_TEXT_PHONE_A} before Friday")
QUESTION_TEXT = (f"Send this to sarah.chen@chenbakery.com and cc the office at "
                 f"{FREE_TEXT_PHONE_B}? Total $1,000.00. Create it?")
FOLLOW_UP_TEXT = (f"Call Greg at {FREE_TEXT_PHONE_C} or email "
                  f"greg@gulfcoastchamber.org about the Navigator review")
NAIVE_COMPLETED = "2026-07-21T11:28:01"          # a parked run's naive local stamp
OFFSET_STARTED = "2026-07-21T11:27:46-05:00"     # explicit offset

DRIVE_LETTER = re.compile(r"[A-Za-z]:[\\/]")
Z_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _canned_brief(today=None):  # noqa: ARG001 — signature parity
    def sec(items, unavailable=False):
        return {"items": items, "empty": not items, "unavailable": unavailable,
                "note": ""}
    return {"generated_for": "2026-09-10", "sections": {
        "obligations_due": sec([{"name": "Chamber invoice"}]),
        "today_events": sec([{"summary": "EVENT-ROW-SENTINEL 9am"}]),
        "needs_reply": sec([{"subject": "THREAD-ROW-SENTINEL", "from": "x@example.test"}]),
        "due_today": sec([]),
        "due_this_week": sec([{"title": "Bakery pilot", "value_usd": "48000.00"}]),
        "stale_deals": sec([]),
        "unpaid_invoices": sec([{"doc_number": "INVOICE-ROW-SENTINEL", "balance": 4500.0}]),
        "awaiting_approval": sec([{"question": "approve?"}]),
        "ridian_noticed": sec([], unavailable=True),
    }}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings_service, "SETTINGS_PATH",
                        tmp_path / "local_settings.json")
    monkeypatch.setattr(quickbooks_service, "TOKEN_PATH",
                        tmp_path / "quickbooks_token.json")
    monkeypatch.setattr(google_drive_service, "TOKEN_PATH",
                        tmp_path / "google_token.json")
    monkeypatch.setattr(google_drive_service, "CREDENTIALS_PATH",
                        tmp_path / "google_credentials.json")
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(svc, "data_dir", lambda: tmp_path)
    # Offline by default: the brief's live sources are stubbed. One test
    # deliberately uses the real build_brief (offline it degrades honestly).
    monkeypatch.setattr(brief_service, "build_brief", _canned_brief)
    yield
    with watch_service._cache_lock:
        watch_service._cache.update(
            {"findings": [], "computed_at": "", "unavailable": {}})


def _seed(extra: dict | None = None) -> None:
    """Realistic records that carry EVERYTHING the export must drop or scrub."""
    extra = extra or {}
    now = "2026-09-10T08:30:00"
    state_store.save("contacts", [{
        "id": "c_1", "name": "Sarah Chen", "role": "Owner",
        "company": "Chen Bakery", "email": SENTINELS["email"],
        "phone": SENTINELS["phone"],
        "notes": SENTINELS["notes"] + " " + SECRETS["anthropic_api_key"],
        "source": "chamber lunch", "last_contact_iso": "2026-09-01",
        "created_iso": now, "updated_iso": now, "written_by": "save_memory",
        "source_op": "op_1", **extra}])
    state_store.save("deals", [{
        "id": "deal_1", "contact_id": "c_1", "contact_name": "Sarah Chen",
        "title": "Bakery assistant pilot", "stage": "proposal",
        "value_usd": SENTINELS["value_usd"], "next_action": "Send proposal",
        "next_action_date": "2026-09-12",
        "notes": SENTINELS["notes"] + " " + SECRETS["quickbooks_client_secret"],
        "created_iso": now, "updated_iso": now,
        "last_touch_iso": "2026-09-05T10:00:00",
        "touches": [{"when": now, "what": SENTINELS["touches"]}],
        "written_by": "save_memory", "source_op": "op_1", **extra},
        {"id": "deal_2", "contact_id": "c_1", "contact_name": "Sarah Chen",
         "title": "Old lead", "stage": "lost", "value_usd": "1.00",
         "next_action": "", "next_action_date": "", "notes": "",
         "created_iso": now, "updated_iso": now, "last_touch_iso": "",
         "touches": [], "written_by": "save_memory", "source_op": ""}])
    state_store.save("obligations", [{
        "id": "obl_1", "name": "Chamber invoice", "task": SENTINELS["task"],
        "cadence": {"kind": "monthly_day", "day": 1},
        "last_completed_period": "2026-08-01", "dismissed_period": "",
        "created_iso": now, "updated_iso": now, "written_by": "operator",
        "source_op": "", **extra}])
    state_store.save("follow_ups", [
        {"id": "fu_1", "what": FOLLOW_UP_TEXT, "who": "Greg Alexander",
         "due_iso": "Next available business day", "status": "open",
         "source_run": "run_x", "created_iso": now, "updated_iso": now,
         "written_by": "save_memory", "source_op": "op_1", **extra},
        {"id": "fu_3", "what": "Renew membership", "who": "Ryan",
         "due_iso": "2026-03-01", "status": "open", "source_run": "",
         "created_iso": now, "updated_iso": now, "written_by": "save_memory",
         "source_op": ""},
        {"id": "fu_2", "what": "Done thing", "who": "Marcus", "due_iso": "2026-09-01",
         "status": "done", "source_run": "", "created_iso": now,
         "updated_iso": now, "written_by": "save_memory", "source_op": ""}])
    state_store.save("projects", [{
        "id": "proj_a", "name": "Chamber", "created_at": now, "parent_id": "", **extra}])
    state_store.save("approvals", [{
        "id": "appr_1", "operation_id": "op_1",
        "command": "Invoice the chamber for September",
        "folder": SENTINELS["artifact_folder"], "tool": "create_quickbooks_invoice",
        "kwargs": {"customer": SENTINELS["kwargs"], "email": SENTINELS["email"]},
        "reason": "invoice_plan_pending", "question": QUESTION_TEXT,
        "options": [{"label": SENTINELS["options"], "price": 500}],
        "gate_flags": {"research_plan_asked": True}, "user_stated_numbers": ["500"],
        "user_provided_emails": [SENTINELS["email"]],
        "staged_at": "2026-08-20T09:00:00", "status": "pending",
        "answered_at": "", "outcome": "", **extra},
        {"id": "appr_old", "operation_id": "op_0", "command": "x", "tool": "t",
         "reason": "r", "question": "q", "staged_at": "2026-08-01T09:00:00",
         "status": "answered", "answered_at": now, "outcome": "approved"}])
    state_store.save("operations", [{
        "id": "op_1", "command": f"Summarize {LOCAL_PATH} for the chamber",
        "intent": "research", "artifact_folder": SENTINELS["artifact_folder"],
        "started_at": OFFSET_STARTED, "completed_at": NAIVE_COMPLETED,
        "status": "completed",
        "steps": [{"name": "planner", "detail":
                   SENTINELS["steps"] + " " + SECRETS["openai_api_key"]}],
        "tools_used": ["read_document", "web_research", "open_browser"],
        "sources_count": 2, "audio_generated": False, "audio_duration_seconds": 0,
        "artifacts": [
            {"name": "research_summary.md",
             "path": SENTINELS["artifact_folder"] + r"\research_summary.md",
             "kind": "md"},
            {"name": "operation_log.json",
             "path": SENTINELS["artifact_folder"] + r"\operation_log.json",
             "kind": "json"},
            {"name": "https://docs.google.com/spreadsheets/d/1AbC-SECRET-ID/edit?usp=sharing&token=xyz",
             "path": "https://docs.google.com/spreadsheets/d/1AbC-SECRET-ID/edit",
             "kind": "browser"},
            {"name": "notebooklm.google.com", "path": "notebooklm.google.com",
             "kind": "browser"}],
        "errors": [], "spend_usd": 0.1234, "cost_ceiling_usd": 1.0,
        "reconciliation": "ok", "source_titles": ["A"],
        "research_approved": True, "research_declined": False,
        "proposed_memory_updates": [{"kind": "fact", "payload": {"fact": "secret"}}],
        "needs_input": [{"question": "Which chamber?"}],
        "receipt": SENTINELS["receipt"] + " " + SECRETS["smtp_password"],
        "awaiting_input": False, "project_id": "proj_a", "background": False,
        **extra},
        {"id": "op_2", "command": COMMAND_TEXT, "intent": "planner",
         "artifact_folder": SENTINELS["artifact_folder"],
         "started_at": "2026-07-20T21:00:42+00:00",
         "completed_at": "2026-07-20T16:01:01",     # naive stamp on a PARKED run
         "status": "awaiting_input", "steps": [], "tools_used": [],
         "sources_count": 0, "artifacts": [], "errors": [], "spend_usd": 0.2,
         "needs_input": [{"question": "Which Marcus?"}], "receipt": "",
         "awaiting_input": True, "project_id": "", "background": True},
        {"id": "op_3", "command": "Older parked run", "intent": "planner",
         "started_at": "not a timestamp", "completed_at": "",
         "status": "awaiting_input", "steps": [], "tools_used": [],
         "artifacts": [], "errors": [], "needs_input": [], "receipt": "",
         "awaiting_input": True, "project_id": "", "background": False}])


def _walk(node, where="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield (f"{where}.{k}", k, v)
            yield from _walk(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{where}[{i}]")


def _keys(doc):
    return {k for _, k, _v in _walk(doc)}


def _local_to_z(naive: str) -> str:
    """What 'read as this PC's local time, express in UTC' must produce."""
    return dt.datetime.fromisoformat(naive).astimezone().astimezone(UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Envelope + counts
# --------------------------------------------------------------------------

def test_envelope_and_summary_counts():
    _seed()
    doc = svc.build_snapshot(version="0.9.8")
    assert doc["schema"] == "ridian-operator-snapshot" and doc["version"] == 1
    assert Z_STAMP.match(doc["generatedAt"])
    assert doc["source"] == {"application": "Ridian Operator", "version": "0.9.8",
                             "localUtcOffset": doc["source"]["localUtcOffset"]}
    assert re.fullmatch(r"[+-]\d\d:\d\d", doc["source"]["localUtcOffset"])
    assert doc["summary"] == {
        "recentWork": 3, "operationsAwaitingInput": 2, "projects": 1,
        "obligations": 1, "obligationsDue": 1, "approvalsPending": 1,
        "approvalsStale": 1, "contacts": 1, "deals": 2, "dealsActive": 1,
        "followUpsOpen": 2}
    assert "legacyProjects" not in doc and "legacyProjects" not in doc["summary"]
    ob = doc["obligations"][0]
    assert ob["cadence"] == {"kind": "monthly_day", "day": "1", "weekday": "", "date": ""}
    assert ob["due"]["status"] == "overdue" and ob["due"]["dueDate"] == "2026-09-01"
    assert ob["nextDue"] == "2026-10-01"
    assert doc["recentWork"][0]["openQuestions"] == 1
    assert doc["approvals"][0]["optionCount"] == 1 and doc["approvals"][0]["stale"] is True
    assert doc["deals"][0]["touchCount"] == 1 and doc["deals"][0]["active"] is True
    assert doc["morningBrief"]["available"] is True
    assert doc["morningBrief"]["sections"]["unpaid_invoices"] == {
        "count": 1, "empty": False, "unavailable": False}


def test_empty_stores_yield_a_complete_document():
    doc = svc.build_snapshot(version="dev")
    assert all(doc[k] == [] for k in ("recentWork", "projects", "obligations",
                                     "approvals", "contacts", "deals", "followUps"))
    assert set(doc["summary"].values()) == {0}
    jsonschema.Draft202012Validator(svc.schema_document()).validate(doc)


# --------------------------------------------------------------------------
# (d) Schema round-trip
# --------------------------------------------------------------------------

def test_committed_schema_matches_the_models_and_the_document_validates():
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == svc.schema_document(), \
        "docs/owner-snapshot-v1.schema.json is stale — regenerate from schema_document()"
    validator = jsonschema.Draft202012Validator(
        committed, format_checker=jsonschema.FormatChecker())
    _seed()
    doc = svc.build_snapshot(version="0.9.8")
    round_tripped = json.loads(json.dumps(doc))
    assert round_tripped == doc
    validator.validate(round_tripped)
    svc.OwnerSnapshotV1.model_validate(round_tripped)
    # Strictness cuts both ways: an unknown key anywhere is a rejection.
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**doc, "apiToken": "x"})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**doc, "contacts": [{**doc["contacts"][0], "email": "x"}]})
    with pytest.raises(pydantic.ValidationError):
        svc.OwnerSnapshotV1.model_validate({**doc, "version": 2})
    assert "legacyProjects" not in committed["properties"]


def test_schema_pins_timestamps_as_date_time_with_a_required_z():
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    recent = committed["$defs"]["RecentWork"]["properties"]
    started = [alt for alt in recent["startedAt"]["anyOf"] if alt.get("type") == "string"][0]
    assert started["format"] == "date-time"
    assert started["pattern"] == svc.TIMESTAMP_PATTERN
    assert {"type": "null"} in recent["startedAt"]["anyOf"]
    assert committed["properties"]["generatedAt"]["format"] == "date-time"
    # An offset other than Z, or a naive stamp, is rejected by the schema itself.
    validator = jsonschema.Draft202012Validator(committed)
    _seed()
    doc = svc.build_snapshot()
    for bad in ("2026-07-21T16:28:01+00:00", "2026-07-21T16:28:01", "2026-07-21"):
        broken = json.loads(json.dumps(doc))
        broken["recentWork"][0]["startedAt"] = bad
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(broken)
    # followUps[].dueAt is documented as free text, never a date.
    due_at = committed["$defs"]["FollowUp"]["properties"]["dueAt"]
    assert due_at["type"] == "string" and "format" not in due_at and "pattern" not in due_at
    assert "Never parsed as a date" in due_at["description"]


# --------------------------------------------------------------------------
# (h) Timestamps: UTC RFC 3339 with Z, null when not finished / unparseable
# --------------------------------------------------------------------------

def test_timestamps_are_utc_z_and_naive_local_inputs_are_converted():
    _seed()
    doc = svc.build_snapshot()
    op1, op2, op3 = doc["recentWork"]
    # Explicit offset: exact instant.
    assert op1["startedAt"] == "2026-07-21T16:27:46Z"
    # Naive local completed_at on a TERMINAL run: read as local, expressed in UTC.
    assert op1["completedAt"] == _local_to_z(NAIVE_COMPLETED)
    assert Z_STAMP.match(op1["completedAt"])
    local_offset = dt.datetime.now().astimezone().utcoffset()
    if local_offset and local_offset.total_seconds() != 0:
        # It was NOT passed through as if it were already UTC.
        assert op1["completedAt"] != NAIVE_COMPLETED + "Z"
    # Parked run: the source has a stamp, the export says "not finished".
    assert op2["status"] == "awaiting_input" and op2["completedAt"] is None
    assert op2["startedAt"] == "2026-07-20T21:00:42Z"
    # Unparseable / blank: null, never a fabricated value.
    assert op3["startedAt"] is None and op3["completedAt"] is None
    # Naive stamps from the approvals / deals writers convert the same way.
    assert doc["approvals"][0]["stagedAt"] == _local_to_z("2026-08-20T09:00:00")
    assert doc["deals"][0]["lastTouchAt"] == _local_to_z("2026-09-05T10:00:00")
    assert doc["deals"][1]["lastTouchAt"] is None
    # Date-only last contact: local midnight, in UTC.
    assert doc["contacts"][0]["lastContactAt"] == _local_to_z("2026-09-01T00:00:00")
    # Every timestamp-kind value in the document is Z or null.
    for _, key, value in _walk(doc):
        if key.endswith("At") and key != "dueAt":
            assert value is None or Z_STAMP.match(value), (key, value)
    # Date fields stay dates; dueAt stays free text, verbatim.
    assert doc["deals"][0]["nextActionDate"] == "2026-09-12"
    assert doc["followUps"][0]["dueAt"] == "Next available business day"
    assert doc["followUps"][1]["dueAt"] == "2026-03-01"


@pytest.mark.parametrize("value,expected", [
    ("2026-07-21T16:27:46+00:00", "2026-07-21T16:27:46Z"),
    ("2026-07-21T11:27:46-05:00", "2026-07-21T16:27:46Z"),
    ("2026-07-21T16:27:46Z", "2026-07-21T16:27:46Z"),
    ("2026-07-21T16:27:46.123456+00:00", "2026-07-21T16:27:46Z"),
    ("", None), (None, None), ("garbage", None), ("2026-13-45", None),
])
def test_to_utc_z_table(value, expected):
    assert svc.to_utc_z(value) == expected


# --------------------------------------------------------------------------
# (g) Free-text scrub: emails and phones never travel; a broken scrub refuses
# --------------------------------------------------------------------------

def test_free_text_fields_have_emails_and_phones_replaced():
    _seed()
    doc = svc.build_snapshot()
    command = doc["recentWork"][1]["command"]
    question = doc["approvals"][0]["question"]
    what = doc["followUps"][0]["what"]
    assert command == ("Draft a check-in to Marcus Delacroix at [email] "
                       "or call [phone] before Friday")
    assert question == ("Send this to [email] and cc the office at [phone]? "
                        "Total $1,000.00. Create it?")
    assert what == "Call Greg at [phone] or email [email] about the Navigator review"
    text = json.dumps(doc)
    for leaked in (FREE_TEXT_EMAIL, "sarah.chen@chenbakery.com",
                   "greg@gulfcoastchamber.org", FREE_TEXT_PHONE_A,
                   FREE_TEXT_PHONE_B, FREE_TEXT_PHONE_C, "555-0142", "5550199"):
        assert leaked not in text, leaked
    assert "@" not in text


def _machine_zone_is_chicago() -> bool:
    """True when this PC's local zone agrees with America/Chicago on both a
    January and a July instant — the DST pair the test below depends on."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    for naive in (dt.datetime(2026, 1, 15, 9), dt.datetime(2026, 7, 15, 9)):
        if naive.astimezone().utcoffset() != naive.replace(tzinfo=chicago).utcoffset():
            return False
    return True


@pytest.mark.skipif(not _machine_zone_is_chicago(),
                    reason="pins the America/Chicago DST pair; this PC is elsewhere")
def test_naive_stamps_follow_the_machine_zone_with_dst_not_a_fixed_offset():
    """A January naive stamp converts at -06:00 and a July one at -05:00,
    checked against zoneinfo — NOT against astimezone(), which is what the
    service itself uses. localUtcOffset must be the offset AT generatedAt,
    so a January export says -06:00 and a July export says -05:00."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    # Independent oracle: the same naive wall time pinned to Chicago.
    jan_expected = dt.datetime(2026, 1, 15, 9, 0, 0, tzinfo=chicago).astimezone(UTC)
    jul_expected = dt.datetime(2026, 7, 15, 9, 0, 0, tzinfo=chicago).astimezone(UTC)
    assert svc.to_utc_z("2026-01-15T09:00:00") == "2026-01-15T15:00:00Z"   # -06:00
    assert svc.to_utc_z("2026-07-15T09:00:00") == "2026-07-15T14:00:00Z"   # -05:00
    assert svc.to_utc_z("2026-01-15T09:00:00") == jan_expected.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert svc.to_utc_z("2026-07-15T09:00:00") == jul_expected.strftime("%Y-%m-%dT%H:%M:%SZ")
    # The envelope offset tracks generatedAt, not "now" and not a constant.
    _seed()
    jan_doc = svc.build_snapshot(now=dt.datetime(2026, 1, 15, 15, 0, 0, tzinfo=UTC))
    jul_doc = svc.build_snapshot(now=dt.datetime(2026, 7, 15, 14, 0, 0, tzinfo=UTC))
    assert jan_doc["generatedAt"] == "2026-01-15T15:00:00Z"
    assert jan_doc["source"]["localUtcOffset"] == "-06:00"
    assert jul_doc["source"]["localUtcOffset"] == "-05:00"


def test_service_never_hard_codes_an_offset():
    """Mutation guard for the test above, on the MECHANISM: the only zone
    the service may use is the machine's own (astimezone() with no
    argument) — never a constructed fixed offset and never a named zone.
    Field descriptions may mention an offset as an example; code may not."""
    import inspect
    src = SERVICE_PATH.read_text(encoding="utf-8")
    assert "timezone(_dt.timedelta" not in src and "timezone(timedelta" not in src
    assert "ZoneInfo(" not in src and "FixedOffset" not in src
    conv = inspect.getsource(svc.to_utc_z)
    assert ".astimezone()" in conv                       # naive -> machine zone
    assert "astimezone(_dt.timezone.utc)" in conv        # then -> UTC
    assert "timedelta" not in conv
    off = inspect.getsource(svc._local_utc_offset)
    assert "%z" in off and "timedelta" not in off        # offset read, not set
    # Only 'timezone.utc' may be referenced as a timezone object anywhere.
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        if re.search(r"(?<![A-Za-z_.])timezone\(", code):   # not astimezone(
            raise AssertionError(f"fixed timezone constructed: {line.strip()}")


def test_scrub_never_touches_dates_ids_money_or_timestamps():
    _seed()
    doc = svc.build_snapshot()
    assert doc["followUps"][1]["dueAt"] == "2026-03-01"
    assert doc["deals"][0]["nextActionDate"] == "2026-09-12"
    assert doc["approvals"][0]["question"].endswith("Total $1,000.00. Create it?")
    assert doc["obligations"][0]["due"]["dueDate"] == "2026-09-01"
    assert "[phone]" not in json.dumps(doc["summary"]) + json.dumps(doc["obligations"])


@pytest.mark.parametrize("mutation", [
    "identity_scrub", "email_regex_disabled", "phone_regex_disabled",
    "command_kind_downgraded",
])
def test_a_mutated_scrub_is_a_refused_export_not_a_leak(monkeypatch, tmp_path, mutation):
    _seed()
    if mutation == "identity_scrub":
        monkeypatch.setattr(svc, "_scrub_contact_details", lambda text: text)
    elif mutation == "email_regex_disabled":
        monkeypatch.setattr(svc, "_EMAIL_RE", re.compile(r"(?!x)x"))
    elif mutation == "phone_regex_disabled":
        monkeypatch.setattr(svc, "_PHONE_RE", re.compile(r"(?!x)x"))
    elif mutation == "command_kind_downgraded":
        table = tuple((s, d, "str" if d == "command" else k)
                      for s, d, k in svc.RECENT_WORK_FIELDS)
        monkeypatch.setattr(svc, "RECENT_WORK_FIELDS", table)
    with pytest.raises(svc.SnapshotPolicyError):
        svc.build_snapshot()
    with pytest.raises(svc.SnapshotPolicyError):
        svc.export_snapshot()
    assert not (tmp_path / "exports").exists()


# --------------------------------------------------------------------------
# Artifacts: filenames only, no ledger file; browser targets as hosts only
# --------------------------------------------------------------------------

def test_artifact_names_exclude_the_ledger_and_browser_targets_become_hosts():
    _seed()
    doc = svc.build_snapshot()
    op1 = doc["recentWork"][0]
    assert op1["artifactNames"] == ["research_summary.md"]
    assert op1["urlsOpened"] == ["docs.google.com", "notebooklm.google.com"]
    text = json.dumps(doc)
    assert "operation_log.json" not in text
    assert "1AbC-SECRET-ID" not in text and "usp=sharing" not in text and "token=xyz" not in text
    assert doc["recentWork"][1]["urlsOpened"] == []


def test_hostname_extraction_is_conservative():
    assert svc._hostname("notebooklm.google.com") == "notebooklm.google.com"
    assert svc._hostname("https://Docs.Google.com/spreadsheets/d/x?y=1") == "docs.google.com"
    assert svc._hostname("http://user:pw@example.test:8080/p") == "example.test"
    assert svc._hostname("") == ""


# --------------------------------------------------------------------------
# (a) (b) (c) (e) Content pins
# --------------------------------------------------------------------------

def test_no_output_key_matches_the_forbidden_pattern():
    _seed()
    doc = svc.build_snapshot()
    offenders = [k for k in _keys(doc) if svc.FORBIDDEN_KEY_RE.search(k)]
    assert offenders == []
    # The pattern the website importer enforces, verbatim.
    assert svc.FORBIDDEN_KEY_RE.pattern == \
        r"token|secret|key|password|credential|cookie|auth"


def test_no_configured_secret_value_leaks(tmp_path):
    settings_service.save_settings({**SECRETS, "operator_name": "Ryan"})
    (tmp_path / "google_token.json").write_text(
        json.dumps({"refresh_token": TOKEN_SECRET}), encoding="utf-8")
    (tmp_path / "quickbooks_token.json").write_text(
        json.dumps({"refresh_token": TOKEN_SECRET}), encoding="utf-8")
    _seed()
    stored = settings_service.load_settings()
    configured = [stored[k] for k in settings_service.SECRET_KEYS if stored.get(k)]
    assert sorted(configured) == sorted(SECRETS.values()), "fixture did not configure secrets"
    doc = svc.build_snapshot()
    text = json.dumps(doc)
    for secret in list(SECRETS.values()) + [TOKEN_SECRET]:
        assert secret not in text, secret
    for _, _k, value in _walk(doc):
        if isinstance(value, str):
            assert value not in configured and value != TOKEN_SECRET


def test_no_local_path_or_data_dir_leaks(tmp_path):
    _seed()
    doc = svc.build_snapshot()
    text = json.dumps(doc)
    assert str(tmp_path) not in text
    assert str(tmp_path).replace("\\", "/") not in text
    assert DRIVE_LETTER.search(text) is None, DRIVE_LETTER.search(text)
    assert "AppData" not in text and "\\\\" not in text
    assert SENTINELS["artifact_folder"] not in text
    # Free text that carried a path is kept — with the path removed.
    assert doc["recentWork"][0]["command"] == \
        f"Summarize {svc.PATH_PLACEHOLDER} for the chamber"


def test_denied_fields_never_appear():
    _seed()
    doc = svc.build_snapshot()
    text = json.dumps(doc)
    for name, sentinel in SENTINELS.items():
        assert sentinel not in text, name
    for name in SENTINELS:
        assert name not in _keys(doc), name
    for name in ("written_by", "source_op", "source_run", "needs_input",
                 "proposed_memory_updates", "cost_ceiling_usd", "user_provided_emails",
                 "legacyProjects", "artifact_folder", "mtime_iso"):
        assert name not in _keys(doc), name
    for row in BRIEF_ROW_SENTINELS:
        assert row not in text, row
    # Every sensitive source name in this test is on the deny list itself.
    assert set(SENTINELS) <= svc.DENY_SOURCE_FIELDS


def test_unexpected_source_fields_are_dropped():
    poison = {"refresh_token": "RT-POISON", "password": "PW-POISON",
              "api_key": "KEY-POISON", "cookie": "CK-POISON", "author": "AU-POISON"}
    _seed(extra=poison)
    doc = svc.build_snapshot()
    text = json.dumps(doc)
    for k, v in poison.items():
        assert k not in _keys(doc) and v not in text, k


# --------------------------------------------------------------------------
# (f) Mutation: a changed allowlist is a REFUSED export, never a leak
# --------------------------------------------------------------------------

@pytest.mark.parametrize("table,mutation,message", [
    ("CONTACT_FIELDS", ("email", "email", "str"), "deny list"),
    ("RECENT_WORK_FIELDS", ("artifact_folder", "folder", "str"), "deny list"),
    ("DEAL_FIELDS", ("value_usd", "value", "str"), "deny list"),
    ("RECENT_WORK_FIELDS", ("id", "apiKey", "str"), "forbidden pattern"),
    ("APPROVAL_FIELDS", ("id", "authorityLevel", "str"), "forbidden pattern"),
    ("CONTACT_FIELDS", ("name", "name", "raw"), "unknown kind"),
])
def test_mutated_allowlist_is_refused_before_anything_is_written(
        monkeypatch, tmp_path, table, mutation, message):
    _seed()
    monkeypatch.setattr(svc, table, getattr(svc, table) + (mutation,))
    with pytest.raises(svc.SnapshotPolicyError, match=message):
        svc.build_snapshot()
    with pytest.raises(svc.SnapshotPolicyError):
        svc.export_snapshot()
    assert not (tmp_path / "exports").exists()


def test_mutated_brief_section_name_is_refused(monkeypatch):
    _seed()
    monkeypatch.setattr(svc, "BRIEF_SECTIONS", svc.BRIEF_SECTIONS + ("auth_state",))
    with pytest.raises(svc.SnapshotPolicyError, match="forbidden pattern"):
        svc.build_snapshot()


def test_verify_document_catches_what_the_tables_cannot(tmp_path):
    needles = (str(tmp_path),)
    with pytest.raises(svc.SnapshotPolicyError, match="forbidden key"):
        svc.verify_document({"summary": {"sessionToken": "x"}}, needles)
    with pytest.raises(svc.SnapshotPolicyError, match="local path"):
        svc.verify_document({"a": [r"see D:\share\x.txt"]}, needles)
    with pytest.raises(svc.SnapshotPolicyError, match="local path"):
        svc.verify_document({"a": r"\\server\share"}, needles)
    with pytest.raises(svc.SnapshotPolicyError, match="data directory"):
        svc.verify_document({"a": f"in {tmp_path} now"}, needles)
    with pytest.raises(svc.SnapshotPolicyError, match="email address"):
        svc.verify_document({"a": ["mail ryan@example.test now"]}, needles)
    svc.verify_document({"ok": ["plain", 1, True, None, {"n": "x"}]}, needles)


# --------------------------------------------------------------------------
# 1. SOURCES: imports + attributes pinned by AST
# --------------------------------------------------------------------------

_ALLOWED_STDLIB = {"__future__", "datetime", "logging", "os", "re", "pathlib",
                   "typing", "json", "urllib"}
_ALLOWED_THIRD_PARTY = {"pydantic"}
_ALLOWED_SERVICE_ATTRS = {
    "approval_inbox_service": {"list_pending"},
    "brief_service": {"build_brief"},
    "dashboard_service": {"build_dashboard"},
    "memory_service": {"list_contacts"},
    "obligations_service": {"list_obligations", "next_due", "due_status"},
    "operation_log_service": {"list_recent", "list_projects"},
    "pipeline_service": {"list_deals", "ACTIVE_STAGES"},
}
_FORBIDDEN_ATTR_FRAGMENTS = ("save", "create", "delete", "update", "add_",
                             "send", "restore", "load", "open", "upload",
                             "unlink", "rmtree", "remove", "read_text",
                             "read_bytes")


def _tree():
    return ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))


def test_import_allowlist_is_exact():
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top in _ALLOWED_STDLIB | _ALLOWED_THIRD_PARTY, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                if node.module is None:
                    for alias in node.names:
                        assert alias.name in _ALLOWED_SERVICE_ATTRS, alias.name
                else:
                    assert node.module == "runtime_paths", node.module
                    assert {a.name for a in node.names} == {"data_dir"}
            else:
                top = (node.module or "").split(".")[0]
                assert top in _ALLOWED_STDLIB | _ALLOWED_THIRD_PARTY, node.module
    src = SERVICE_PATH.read_text(encoding="utf-8")
    assert "project_service" not in src, "legacy run folders no longer travel"


def test_only_the_named_read_functions_are_touched_and_no_writer_is_callable():
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name) and base.id in _ALLOWED_SERVICE_ATTRS:
                assert node.attr in _ALLOWED_SERVICE_ATTRS[base.id], \
                    f"{base.id}.{node.attr}"
            assert not any(f in node.attr for f in _FORBIDDEN_ATTR_FRAGMENTS), node.attr
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "eval", "exec", "__import__"}, node.func.id


# --------------------------------------------------------------------------
# 3. NO WRITES; exactly one file, under exports/
# --------------------------------------------------------------------------

def _state_bytes():
    root = Path(state_store.STATE_DIR)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def test_export_writes_exactly_one_file_and_touches_no_state(tmp_path):
    _seed()
    before_state = _state_bytes()
    before_backups = state_store.list_snapshots()
    before_top = sorted(p.name for p in tmp_path.iterdir())
    result = svc.export_snapshot(version="0.9.8")
    assert _state_bytes() == before_state
    assert state_store.list_snapshots() == before_backups
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(before_top + ["exports"])
    files = list((tmp_path / "exports").iterdir())
    assert len(files) == 1 and files[0].name == result["fileName"]
    assert re.fullmatch(r"owner-snapshot-\d{8}-\d{6}(-\d+)?\.json", files[0].name)
    assert Path(result["path"]) == files[0]
    on_disk = json.loads(files[0].read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(svc.schema_document()).validate(on_disk)
    assert on_disk["summary"] == result["summary"]
    # A second export in the same second gets a suffix, never an overwrite.
    second = svc.export_snapshot(version="0.9.8")
    assert second["fileName"] != result["fileName"]
    assert len(list((tmp_path / "exports").iterdir())) == 2


def test_loopback_route_exports_and_reports_the_path(tmp_path):
    _seed()
    r = TestClient(app, client=("127.0.0.1", 50000)).post("/owner-snapshot/export")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert Path(body["path"]).exists() and Path(body["path"]).parent == tmp_path / "exports"
    on_disk = json.loads(Path(body["path"]).read_text(encoding="utf-8"))
    assert on_disk["source"]["version"] == main_module.app_version()
    assert body["summary"]["approvalsPending"] == 1


def test_route_reports_a_refused_export_as_an_error(monkeypatch, tmp_path):
    _seed()
    monkeypatch.setattr(svc, "CONTACT_FIELDS",
                        svc.CONTACT_FIELDS + (("email", "email", "str"),))
    r = TestClient(app, client=("127.0.0.1", 50000)).post("/owner-snapshot/export")
    assert r.status_code == 500 and "refused" in r.json()["detail"]
    assert not (tmp_path / "exports").exists()


# --------------------------------------------------------------------------
# Morning brief: the REAL builder, offline, reduces to counts only
# --------------------------------------------------------------------------

def test_real_brief_reduces_to_counts_and_degrades_honestly(monkeypatch):
    monkeypatch.setattr(brief_service, "build_brief", _REAL_BUILD_BRIEF)
    _seed()
    doc = svc.build_snapshot()
    brief = doc["morningBrief"]
    assert brief["available"] is True and brief["generatedFor"]
    assert set(brief["sections"]) == set(svc.BRIEF_SECTIONS)
    for sec in brief["sections"].values():
        assert set(sec) == {"count", "empty", "unavailable"}
    # Offline: the cloud-backed sections say so instead of claiming zero.
    for name in ("unpaid_invoices", "needs_reply", "today_events"):
        assert brief["sections"][name]["unavailable"] is True
    # Local sections are real, and the two "awaiting" numbers are distinct.
    assert brief["sections"]["obligations_due"]["count"] == 1
    assert brief["sections"]["awaiting_approval"]["count"] == 2
    assert doc["summary"]["operationsAwaitingInput"] == 2
    assert doc["summary"]["approvalsPending"] == 1
    assert "48000" not in json.dumps(doc)
