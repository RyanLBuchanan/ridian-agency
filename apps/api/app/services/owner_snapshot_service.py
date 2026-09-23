"""Ridian Owner Snapshot v1 — a local, read-only, allowlisted export.

WHY THIS EXISTS. The Owner Workspace on ridiantechnologies.com needs
authentic Operator context (recent work, projects, obligations, approvals,
the shape of the morning brief) to populate its read-only home — without the
website ever gaining execution authority over this PC, and without a single
credential, transcript, memory record, audit row, companion record, local
path, email address or phone number leaving the machine. This module writes
that file. Nothing here uploads; the operator inspects the file and moves it
by hand.

THREE PINS (each has a test in tests/test_owner_snapshot.py):

  1. SOURCES — only the named service functions are consulted. An AST
     allowlist pins both this module's imports and every attribute it
     touches on them. No JSON file is opened here; the services own their
     stores, and this module cannot name a writer.
  2. FIELDS — every record type has an explicit (source field, output key,
     kind) table. A field not named is a field not exported. DENY_SOURCE_FIELDS
     lists what must never be mapped (emails, phones, notes, dollar values,
     steps, receipts, paths, planner kwargs, provenance stamps, ...);
     build_snapshot() refuses to run if a table ever names one of them, and
     refuses to emit any key matching FORBIDDEN_KEY_RE, any string carrying
     a drive-letter / UNC / home path or the data directory, or any string
     carrying an email address. Free-text fields (kind "text") additionally
     have email addresses and phone numbers replaced with [email] / [phone]
     and refuse themselves if anything survives the scrub.
  3. NO WRITES except the export file itself, under <data_dir>/exports/.
     The state store is byte-identical before and after an export.

TIMESTAMPS. Every exported timestamp (kind "timestamp") is UTC RFC 3339
with a trailing Z, or null when the source is blank or unparseable. Sources
that write naive local time (approvals, deals, a parked run's completed_at)
are interpreted in this PC's local zone. Date-only fields (kind "date") stay
YYYY-MM-DD or "". ``followUps[].dueAt`` is free text as entered and is never
parsed as a date.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from . import (approval_inbox_service, brief_service, dashboard_service,
               memory_service, obligations_service, operation_log_service,
               pipeline_service)
from .runtime_paths import data_dir

log = logging.getLogger("ridian.owner_snapshot")

SCHEMA = "ridian-operator-snapshot"
VERSION = 1
APPLICATION = "Ridian Operator"
SCHEMA_ID = "https://ridiantechnologies.com/schemas/owner-snapshot-v1.schema.json"
EXPORTS_DIRNAME = "exports"
FILE_PREFIX = "owner-snapshot-"

RECENT_WORK_LIMIT = 25
OPERATIONS_SCAN_LIMIT = 500   # the store itself caps at 500 entries
LIST_LIMIT = 200              # contacts, deals, obligations, follow-ups, approvals
TEXT_MAX = 2000
LIST_ITEM_MAX = 50
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

# Output keys may never match this — the website importer enforces the same
# pattern, so a key that slipped past here would be rejected there too.
FORBIDDEN_KEY_RE = re.compile(
    r"token|secret|key|password|credential|cookie|auth", re.IGNORECASE)

PATH_PLACEHOLDER = "[local path removed]"
EMAIL_PLACEHOLDER = "[email]"
PHONE_PLACEHOLDER = "[phone]"
_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|]*")
_UNC_PATH_RE = re.compile(r"\\\\[^\s\"'<>|]+")
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[^\s\"'<>|]*")

# Contact-detail scrub (free text only). Phone shape is deliberately strict —
# three/three/four digit groups with optional country code — so ISO dates,
# times, ids and money never match.
_EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_PHONE_PATTERN = (r"(?<![\w.-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})"
                  r"[\s.-]?\d{3}[\s.-]?\d{4}(?![\w-])")
_EMAIL_RE = re.compile(_EMAIL_PATTERN)
_PHONE_RE = re.compile(_PHONE_PATTERN)
# Independent leak detectors: compiled separately so a broken scrub regex is
# a refused export, never a leak.
_LEAK_EMAIL_RE = re.compile(_EMAIL_PATTERN)
_LEAK_PHONE_RE = re.compile(_PHONE_PATTERN)

_TS_OUT = "%Y-%m-%dT%H:%M:%SZ"
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
DATE_PATTERN = r"^(\d{4}-\d{2}-\d{2})?$"
_DATE_RE = re.compile(DATE_PATTERN)

# Source fields that no table may ever name. The intersection check in
# check_policy() is what makes a mutated table fail loudly instead of
# silently exporting a phone number.
DENY_SOURCE_FIELDS: frozenset[str] = frozenset({
    # contact / deal / follow-up detail that is not the website's business
    "email", "phone", "notes", "source", "value_usd", "touches",
    # operation internals: transcripts, planner state, provenance, money fences
    "steps", "receipt", "needs_input", "proposed_memory_updates",
    "source_titles", "errors", "artifacts", "artifact_folder", "path",
    "reconciliation", "research_approved", "research_declined",
    # v7.0: texts sent by a run — carries the recipient's number.
    "sms_messages",
    # v7.1: the Owner Workspace sync credential and the code that obtains
    # it. No table may ever map them, even under an innocuous output key.
    "device_token", "pairing_code",
    "audio_generated", "audio_duration_seconds", "cost_ceiling_usd",
    # approval internals: planner kwargs, offered options, gate evidence
    "kwargs", "options", "gate_flags", "user_stated_numbers",
    "user_provided_emails", "folder", "answered_at", "outcome",
    # obligation internals: the runnable command and dismissal bookkeeping
    "task", "dismissed_period",
    # provenance stamps stay on the PC
    "written_by", "source_op", "source_run",
})

# Kinds: str = label/id (path-scrubbed); text = human prose (path + contact
# scrub, self-checked); timestamp = UTC Z or null; date = YYYY-MM-DD or "".
_STR, _TEXT, _TS, _DATE = "str", "text", "timestamp", "date"
_BOOL, _INT, _FLOAT, _STRLIST = "bool", "int", "float", "strlist"
_KINDS = frozenset({_STR, _TEXT, _TS, _DATE, _BOOL, _INT, _FLOAT, _STRLIST})

# (source field, output key, kind) — the complete export for each record.
RECENT_WORK_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("command", "command", _TEXT), ("intent", "intent", _STR),
    ("status", "status", _STR), ("started_at", "startedAt", _TS),
    ("completed_at", "completedAt", _TS), ("spend_usd", "spendUsd", _FLOAT),
    ("tools_used", "toolsUsed", _STRLIST), ("project_id", "projectId", _STR),
    ("background", "background", _BOOL), ("awaiting_input", "awaitingInput", _BOOL),
    ("sources_count", "sourcesCount", _INT),
)
PROJECT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("name", "name", _TEXT), ("created_at", "createdAt", _TS),
    ("parent_id", "parentId", _STR),
)
OBLIGATION_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("name", "name", _TEXT),
    ("last_completed_period", "lastCompletedPeriod", _DATE),
    ("created_iso", "createdAt", _TS), ("updated_iso", "updatedAt", _TS),
)
CADENCE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("kind", "kind", _STR), ("day", "day", _STR), ("weekday", "weekday", _STR),
    ("date", "date", _DATE),
)
DUE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("due_date", "dueDate", _DATE), ("status", "status", _STR),
    ("days_overdue", "daysOverdue", _INT), ("missed_periods", "missedPeriods", _INT),
)
APPROVAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("operation_id", "operationId", _STR),
    ("command", "command", _TEXT), ("tool", "tool", _STR),
    ("reason", "reason", _STR), ("question", "question", _TEXT),
    ("staged_at", "stagedAt", _TS), ("status", "status", _STR),
    ("stale", "stale", _BOOL),
)
CONTACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("name", "name", _TEXT), ("role", "role", _TEXT),
    ("company", "company", _TEXT), ("last_contact_iso", "lastContactAt", _TS),
    ("created_iso", "createdAt", _TS), ("updated_iso", "updatedAt", _TS),
)
DEAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("title", "title", _TEXT), ("stage", "stage", _STR),
    ("contact_id", "contactId", _STR), ("contact_name", "contactName", _TEXT),
    ("next_action", "nextAction", _TEXT), ("next_action_date", "nextActionDate", _DATE),
    ("created_iso", "createdAt", _TS), ("updated_iso", "updatedAt", _TS),
    ("last_touch_iso", "lastTouchAt", _TS),
)
FOLLOW_UP_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("id", "id", _STR), ("what", "what", _TEXT), ("who", "who", _TEXT),
    ("due_iso", "dueAt", _STR), ("status", "status", _STR),
    ("created_iso", "createdAt", _TS), ("updated_iso", "updatedAt", _TS),
)
# Morning-brief sections are exported as counts and availability only —
# never their rows (those carry invoice balances, thread subjects, senders).
BRIEF_SECTIONS: tuple[str, ...] = (
    "obligations_due", "today_events", "needs_reply", "due_today",
    "due_this_week", "stale_deals", "unpaid_invoices", "awaiting_approval",
    "ridian_noticed",
)


def _tables() -> dict[str, tuple[tuple[str, str, str], ...]]:
    """Read at call time so a mutated table is seen by check_policy()."""
    return {
        "recentWork": RECENT_WORK_FIELDS, "projects": PROJECT_FIELDS,
        "obligations": OBLIGATION_FIELDS, "cadence": CADENCE_FIELDS,
        "due": DUE_FIELDS, "approvals": APPROVAL_FIELDS,
        "contacts": CONTACT_FIELDS, "deals": DEAL_FIELDS,
        "followUps": FOLLOW_UP_FIELDS,
    }


class SnapshotPolicyError(RuntimeError):
    """The export refused itself: a table names a denied field, an output
    key matches the forbidden pattern, a value carries a local path or a
    contact detail, or a scrub failed to remove what it was asked to."""


# ---------------------------------------------------------------------------
# Scrubbing + coercion
# ---------------------------------------------------------------------------

def _scrub_paths(text: str, place_needles: tuple[str, ...]) -> str:
    for needle in place_needles:
        if needle:
            text = re.sub(re.escape(needle), PATH_PLACEHOLDER, text,
                          flags=re.IGNORECASE)
    text = _DRIVE_PATH_RE.sub(PATH_PLACEHOLDER, text)
    text = _UNC_PATH_RE.sub(PATH_PLACEHOLDER, text)
    text = _HOME_PATH_RE.sub(PATH_PLACEHOLDER, text)
    return text


def _scrub_contact_details(text: str) -> str:
    """Free text only: email addresses and phone numbers become placeholders."""
    text = _EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)
    text = _PHONE_RE.sub(PHONE_PLACEHOLDER, text)
    return text


def _place_needles() -> tuple[str, ...]:
    """Every spelling of the data directory that could appear in free text."""
    d = str(data_dir())
    return tuple({d, d.replace("\\", "/"), d.replace("/", "\\")})


def to_utc_z(value: Any) -> Optional[str]:
    """ISO 8601 in → RFC 3339 UTC with Z out, or None. Aware values are
    converted; naive values are read as this PC's local time (that is what
    the Operator's naive writers mean); date-only values are local midnight."""
    if value is None:
        return None
    raw = value if isinstance(value, str) else str(value)
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()          # naive == local on this PC
    return parsed.astimezone(_dt.timezone.utc).strftime(_TS_OUT)


class _Coercer:
    def __init__(self) -> None:
        self._needles = _place_needles()

    def text(self, value: Any) -> str:
        """Labels and ids: path-scrubbed, bounded."""
        if value is None:
            return ""
        s = value if isinstance(value, str) else str(value)
        return _scrub_paths(s[:TEXT_MAX], self._needles)

    def freetext(self, value: Any) -> str:
        """Human prose: path-scrubbed, contact details replaced, and
        self-checked with the independent leak detectors — a scrub that
        left an email or phone behind is a refused export."""
        out = _scrub_contact_details(self.text(value))
        if _LEAK_EMAIL_RE.search(out) or _LEAK_PHONE_RE.search(out):
            raise SnapshotPolicyError("contact detail survived the free-text scrub")
        return out

    def coerce(self, value: Any, kind: str) -> Any:
        if kind == _STR:
            return self.text(value)
        if kind == _TEXT:
            return self.freetext(value)
        if kind == _TS:
            return to_utc_z(value)
        if kind == _DATE:
            s = self.text(value)
            return s if _DATE_RE.fullmatch(s) else ""
        if kind == _BOOL:
            return bool(value)
        if kind == _INT:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0
        if kind == _FLOAT:
            try:
                return round(float(value or 0.0), 4)
            except (TypeError, ValueError):
                return 0.0
        if kind == _STRLIST:
            if not isinstance(value, list):
                return []
            return [self.text(x) for x in value
                    if isinstance(x, (str, int, float))][:LIST_ITEM_MAX]
        raise SnapshotPolicyError(f"unknown field kind {kind!r}")

    def pick(self, record: Any, table: tuple[tuple[str, str, str], ...]) -> dict:
        src = record if isinstance(record, dict) else {}
        return {dst: self.coerce(src.get(field), kind) for field, dst, kind in table}


def _hostname(value: str) -> str:
    """A browser artifact's name is a URL or bare host; keep the host only.
    Paths and query strings can carry document ids and tokens."""
    raw = value.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        return (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Record builders — one per section, reading ONLY the named services
# ---------------------------------------------------------------------------

def _operations() -> list[dict]:
    return [op for op in operation_log_service.list_recent(limit=OPERATIONS_SCAN_LIMIT)
            if isinstance(op, dict)]


def scrub_free_text(value: Any) -> str:
    """The exporter's free-text scrub, for callers outside the snapshot
    (v7.3: a Ridian Jobs result's replyText). Local paths and the data
    directory removed, email addresses and phone numbers replaced, then
    self-checked; raises SnapshotPolicyError when a contact detail survives."""
    return _Coercer().freetext(value)


def deliverable_names(op: Any, c: Optional[_Coercer] = None) -> list[str]:
    """recentWork.artifactNames for one operation: deliverable FILENAMES
    only (the local path is never read), browser targets and the run's own
    operation_log.json left out, path-scrubbed, capped. Shared with the
    Ridian Jobs result so both report deliverables the same way."""
    c = c or _Coercer()
    artifacts = op.get("artifacts") if isinstance(op, dict) and isinstance(op.get("artifacts"), list) else []
    names: list[str] = []
    for a in artifacts:
        if not isinstance(a, dict) or not a.get("name") or a.get("kind") == "browser":
            continue
        name = os.path.basename(str(a.get("name")))
        if name and name != "operation_log.json":
            names.append(c.text(name))
    return names[:LIST_ITEM_MAX]


def _recent_work(c: _Coercer, operations: list[dict]) -> list[dict]:
    out = []
    for op in operations[:RECENT_WORK_LIMIT]:
        row = c.pick(op, RECENT_WORK_FIELDS)
        if row["status"] not in TERMINAL_STATUSES:
            row["completedAt"] = None            # parked or running: not finished
        artifacts = op.get("artifacts") if isinstance(op.get("artifacts"), list) else []
        hosts: list[str] = []
        for a in artifacts:
            if isinstance(a, dict) and a.get("name") and a.get("kind") == "browser":
                host = _hostname(str(a.get("name")))
                if host and host not in hosts:
                    hosts.append(host)
        # Filenames only — the artifact's local path is never read — and the
        # run's own ledger file is bookkeeping, not a deliverable.
        row["artifactNames"] = deliverable_names(op, c)
        row["urlsOpened"] = hosts[:LIST_ITEM_MAX]
        needs = op.get("needs_input")
        row["openQuestions"] = len(needs) if isinstance(needs, list) else 0
        errors = op.get("errors")
        row["errorCount"] = len(errors) if isinstance(errors, list) else 0
        out.append(row)
    return out


def _projects(c: _Coercer) -> list[dict]:
    return [c.pick(p, PROJECT_FIELDS)
            for p in operation_log_service.list_projects()[:LIST_LIMIT]
            if isinstance(p, dict)]


def _obligations(c: _Coercer) -> list[dict]:
    out = []
    for ob in obligations_service.list_obligations()[:LIST_LIMIT]:
        if not isinstance(ob, dict):
            continue
        row = c.pick(ob, OBLIGATION_FIELDS)
        row["cadence"] = c.pick(ob.get("cadence"), CADENCE_FIELDS)
        try:
            row["nextDue"] = c.coerce(obligations_service.next_due(ob), _DATE)
        except Exception:  # noqa: BLE001 — malformed cadence: honest blank
            row["nextDue"] = ""
        try:
            due = obligations_service.due_status(ob)
        except Exception:  # noqa: BLE001
            due = None
        row["due"] = c.pick(due, DUE_FIELDS) if isinstance(due, dict) else None
        out.append(row)
    return out


def _approvals(c: _Coercer) -> list[dict]:
    out = []
    for a in approval_inbox_service.list_pending()[:LIST_LIMIT]:
        if not isinstance(a, dict):
            continue
        row = c.pick(a, APPROVAL_FIELDS)
        options = a.get("options")
        row["optionCount"] = len(options) if isinstance(options, list) else 0
        out.append(row)
    return out


def _contacts(c: _Coercer) -> list[dict]:
    return [c.pick(r, CONTACT_FIELDS)
            for r in memory_service.list_contacts()[:LIST_LIMIT]
            if isinstance(r, dict)]


def _deals(c: _Coercer) -> list[dict]:
    out = []
    for d in pipeline_service.list_deals()[:LIST_LIMIT]:
        if not isinstance(d, dict):
            continue
        row = c.pick(d, DEAL_FIELDS)
        touches = d.get("touches")
        row["touchCount"] = len(touches) if isinstance(touches, list) else 0
        row["active"] = d.get("stage") in pipeline_service.ACTIVE_STAGES
        out.append(row)
    return out


def _follow_ups(c: _Coercer) -> list[dict]:
    dashboard = dashboard_service.build_dashboard(recent_limit=1)
    items = dashboard.get("open_follow_ups") if isinstance(dashboard, dict) else []
    return [c.pick(f, FOLLOW_UP_FIELDS)
            for f in (items or [])[:LIST_LIMIT] if isinstance(f, dict)]


def _empty_sections() -> dict:
    return {name: {"count": 0, "empty": True, "unavailable": True}
            for name in BRIEF_SECTIONS}


def _morning_brief(c: _Coercer) -> dict:
    """Headings and counts only. The brief's rows carry invoice balances,
    thread subjects and senders, and deal values — none of that travels.
    NOTE: the brief's awaiting_approval section counts operations parked in
    awaiting_input; summary.approvalsPending counts staged gate approvals.
    They are different things and are exported as different numbers."""
    try:
        brief = brief_service.build_brief()
    except Exception as exc:  # noqa: BLE001 — honest unavailability
        log.info("owner_snapshot.brief_unavailable %s", type(exc).__name__)
        return {"available": False, "generatedFor": "", "sections": _empty_sections()}
    sections_in = brief.get("sections") if isinstance(brief, dict) else {}
    sections = _empty_sections()
    for name in BRIEF_SECTIONS:
        sec = (sections_in or {}).get(name)
        if not isinstance(sec, dict):
            continue
        items = sec.get("items")
        sections[name] = {
            "count": len(items) if isinstance(items, list) else 0,
            "empty": bool(sec.get("empty", not items)),
            "unavailable": bool(sec.get("unavailable", False)),
        }
    return {"available": True,
            "generatedFor": c.coerce((brief or {}).get("generated_for"), _DATE),
            "sections": sections}


# ---------------------------------------------------------------------------
# The contract (pydantic is the single source of truth for the JSON Schema)
# ---------------------------------------------------------------------------

Timestamp = Annotated[str, Field(
    pattern=TIMESTAMP_PATTERN, json_schema_extra={"format": "date-time"},
    description="UTC, RFC 3339, second precision, trailing Z. Null when the "
                "source value was blank or unparseable.")]
DateOrEmpty = Annotated[str, Field(
    pattern=DATE_PATTERN,
    description="YYYY-MM-DD as recorded (no timezone), or empty.")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Source(_Strict):
    application: str
    version: str
    localUtcOffset: str = Field(
        description="The exporting PC's local offset at export time, e.g. -05:00. "
                    "Every timestamp in this document is already UTC; this is "
                    "context only.")


class Summary(_Strict):
    recentWork: int = Field(description="Rows in recentWork (capped at 25).")
    operationsAwaitingInput: int = Field(
        description="Operations parked in awaiting_input across the whole "
                    "store, not only the recentWork rows. This is the number "
                    "the morning brief's awaiting_approval section reports; "
                    "it is NOT approvalsPending.")
    projects: int
    obligations: int
    obligationsDue: int
    approvalsPending: int = Field(
        description="Staged gate approvals (invoice, proposal, research plan, "
                    "...) still waiting for an answer. Distinct from "
                    "operationsAwaitingInput.")
    approvalsStale: int
    contacts: int
    deals: int
    dealsActive: int
    followUpsOpen: int


class RecentWork(_Strict):
    id: str
    command: str
    intent: str
    status: str
    startedAt: Optional[Timestamp]
    completedAt: Optional[Timestamp] = Field(
        description="Null unless status is terminal (completed, failed, cancelled).")
    spendUsd: float
    toolsUsed: list[str]
    projectId: str
    background: bool
    awaitingInput: bool
    sourcesCount: int
    artifactNames: list[str] = Field(
        description="Deliverable filenames only: no paths, no operation_log.json, "
                    "no browser targets.")
    urlsOpened: list[str] = Field(
        description="Hostnames the run opened in the browser (open_browser "
                    "artifacts), host only — never a path or query string.")
    openQuestions: int
    errorCount: int


class Project(_Strict):
    id: str
    name: str
    createdAt: Optional[Timestamp]
    parentId: str


class Cadence(_Strict):
    kind: str
    day: str
    weekday: str
    date: DateOrEmpty


class Due(_Strict):
    dueDate: DateOrEmpty
    status: str
    daysOverdue: int
    missedPeriods: int


class Obligation(_Strict):
    id: str
    name: str
    lastCompletedPeriod: DateOrEmpty
    createdAt: Optional[Timestamp]
    updatedAt: Optional[Timestamp]
    cadence: Cadence
    nextDue: DateOrEmpty
    due: Optional[Due]


class Approval(_Strict):
    id: str
    operationId: str
    command: str
    tool: str
    reason: str
    question: str
    stagedAt: Optional[Timestamp]
    status: str
    stale: bool
    optionCount: int


class Contact(_Strict):
    id: str
    name: str
    role: str
    company: str
    lastContactAt: Optional[Timestamp]
    createdAt: Optional[Timestamp]
    updatedAt: Optional[Timestamp]


class Deal(_Strict):
    id: str
    title: str
    stage: str
    contactId: str
    contactName: str
    nextAction: str
    nextActionDate: DateOrEmpty
    createdAt: Optional[Timestamp]
    updatedAt: Optional[Timestamp]
    lastTouchAt: Optional[Timestamp]
    touchCount: int
    active: bool


class FollowUp(_Strict):
    id: str
    what: str
    who: str
    dueAt: str = Field(
        description="Free text exactly as entered (for example 'This week', "
                    "'Next available business day', or a date typed by hand). "
                    "Never parsed as a date; render it verbatim.")
    status: str
    createdAt: Optional[Timestamp]
    updatedAt: Optional[Timestamp]


class SectionCount(_Strict):
    count: int
    empty: bool
    unavailable: bool


class BriefSections(_Strict):
    obligations_due: SectionCount
    today_events: SectionCount
    needs_reply: SectionCount
    due_today: SectionCount
    due_this_week: SectionCount
    stale_deals: SectionCount
    unpaid_invoices: SectionCount
    awaiting_approval: SectionCount = Field(
        description="Operations parked in awaiting_input (same number as "
                    "summary.operationsAwaitingInput). Not staged gate approvals.")
    ridian_noticed: SectionCount


class MorningBrief(_Strict):
    available: bool
    generatedFor: DateOrEmpty
    sections: BriefSections


class OwnerSnapshotV1(_Strict):
    schema_: Literal["ridian-operator-snapshot"] = Field(alias="schema")
    version: Literal[1]
    generatedAt: Timestamp
    source: Source
    summary: Summary
    recentWork: list[RecentWork]
    projects: list[Project]
    obligations: list[Obligation]
    approvals: list[Approval]
    contacts: list[Contact]
    deals: list[Deal]
    followUps: list[FollowUp]
    morningBrief: MorningBrief


def schema_document() -> dict:
    """The JSON Schema committed at docs/owner-snapshot-v1.schema.json —
    generated from the models above so the two cannot drift silently."""
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_ID,
            **OwnerSnapshotV1.model_json_schema(by_alias=True)}


# ---------------------------------------------------------------------------
# Policy checks — refuse-before-the-artifact, like every other gate
# ---------------------------------------------------------------------------

def check_policy() -> None:
    """Refuse if any table names a denied source field or produces a
    forbidden output key. Runs on every build, so a mutated table is a
    refused export, not a leak."""
    for section, table in _tables().items():
        for src, dst, kind in table:
            if src in DENY_SOURCE_FIELDS:
                raise SnapshotPolicyError(
                    f"{section}: source field {src!r} is on the deny list")
            if FORBIDDEN_KEY_RE.search(dst):
                raise SnapshotPolicyError(
                    f"{section}: output key {dst!r} matches the forbidden pattern")
            if kind not in _KINDS:
                raise SnapshotPolicyError(f"{section}: unknown kind {kind!r}")
    for name in BRIEF_SECTIONS:
        if FORBIDDEN_KEY_RE.search(name):
            raise SnapshotPolicyError(
                f"brief section {name!r} matches the forbidden pattern")


def verify_document(document: Any, needles: Optional[tuple[str, ...]] = None,
                    _where: str = "$") -> None:
    """Walk the finished document: no key may match FORBIDDEN_KEY_RE and no
    string may carry a local path, the data directory, or an email address."""
    needles = _place_needles() if needles is None else needles
    if isinstance(document, dict):
        for key, value in document.items():
            if FORBIDDEN_KEY_RE.search(str(key)):
                raise SnapshotPolicyError(f"forbidden key {key!r} at {_where}")
            verify_document(value, needles, f"{_where}.{key}")
    elif isinstance(document, list):
        for i, item in enumerate(document):
            verify_document(item, needles, f"{_where}[{i}]")
    elif isinstance(document, str):
        lowered = document.lower()
        for needle in needles:
            if needle and needle.lower() in lowered:
                raise SnapshotPolicyError(f"data directory leaked at {_where}")
        if (_DRIVE_PATH_RE.search(document) or _UNC_PATH_RE.search(document)
                or _HOME_PATH_RE.search(document)):
            raise SnapshotPolicyError(f"local path leaked at {_where}")
        if _LEAK_EMAIL_RE.search(document):
            raise SnapshotPolicyError(f"email address leaked at {_where}")


# ---------------------------------------------------------------------------
# Build + export
# ---------------------------------------------------------------------------

def _app_version() -> str:
    """Mirrors main.app_version(): the installer version stamped by the
    desktop supervisor, or "dev" when started from the venv."""
    return (os.environ.get("RIDIAN_APP_VERSION") or "").strip() or "dev"


def _local_utc_offset(now_local: _dt.datetime) -> str:
    raw = now_local.strftime("%z") or "+0000"
    return f"{raw[:3]}:{raw[3:]}"


def build_snapshot(version: Optional[str] = None,
                   now: Optional[_dt.datetime] = None) -> dict:
    """Assemble, validate against the contract, and policy-check the
    document. Pure with respect to the state store: reads only."""
    check_policy()
    c = _Coercer()
    now_utc = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    now_local = now_utc.astimezone()

    operations = _operations()
    recent_work = _recent_work(c, operations)
    projects = _projects(c)
    obligations = _obligations(c)
    approvals = _approvals(c)
    contacts = _contacts(c)
    deals = _deals(c)
    follow_ups = _follow_ups(c)
    morning_brief = _morning_brief(c)

    document = {
        "schema": SCHEMA,
        "version": VERSION,
        "generatedAt": now_utc.strftime(_TS_OUT),
        "source": {
            "application": APPLICATION,
            "version": c.text(version if version is not None else _app_version()),
            "localUtcOffset": _local_utc_offset(now_local),
        },
        "summary": {
            "recentWork": len(recent_work),
            "operationsAwaitingInput": sum(
                1 for op in operations if op.get("status") == "awaiting_input"),
            "projects": len(projects),
            "obligations": len(obligations),
            "obligationsDue": sum(1 for o in obligations if o["due"] is not None),
            "approvalsPending": len(approvals),
            "approvalsStale": sum(1 for a in approvals if a["stale"]),
            "contacts": len(contacts),
            "deals": len(deals),
            "dealsActive": sum(1 for d in deals if d["active"]),
            "followUpsOpen": len(follow_ups),
        },
        "recentWork": recent_work,
        "projects": projects,
        "obligations": obligations,
        "approvals": approvals,
        "contacts": contacts,
        "deals": deals,
        "followUps": follow_ups,
        "morningBrief": morning_brief,
    }
    # Contract first (shape), then policy (content). Both refuse-before-write.
    OwnerSnapshotV1.model_validate(document)
    verify_document(document)
    return document


def exports_dir() -> Path:
    return data_dir() / EXPORTS_DIRNAME


def export_snapshot(version: Optional[str] = None,
                    now: Optional[_dt.datetime] = None) -> dict:
    """Write <data_dir>/exports/owner-snapshot-<local stamp>.json atomically.
    Returns the path and the summary so the caller can show exactly what
    was written and where. Never uploads."""
    import json  # local import keeps the module's import surface minimal

    document = build_snapshot(version=version, now=now)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = exports_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{FILE_PREFIX}{stamp}.json"
    n = 0
    while final.exists():
        n += 1
        final = target_dir / f"{FILE_PREFIX}{stamp}-{n}.json"
    tmp = target_dir / f".{final.name}.tmp"
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, final)
    log.info("owner_snapshot.exported file=%s recent=%d approvals=%d",
             final.name, document["summary"]["recentWork"],
             document["summary"]["approvalsPending"])
    return {"path": str(final), "fileName": final.name,
            "generatedAt": document["generatedAt"],
            "summary": document["summary"]}
