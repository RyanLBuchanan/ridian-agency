# Ridian Owner Snapshot v1

A local, read-only, allowlisted export of Operator context for the private
Owner Workspace at ridiantechnologies.com/owner. The Operator writes a file;
Ryan inspects it and moves it by hand. Nothing is uploaded, no network path
exists, and the website gains no authority over this PC.

**Since v7.1** the same document can also travel automatically: when the
Owner Workspace is connected in Settings, `sync_service` pushes it to the
site over HTTPS, outbound only (see `owner-workspace-sync.md`). The exporter
itself still only builds the document and writes the local file, and the
website still gains no authority over this PC.

## Contract

- **Schema:** `docs/owner-snapshot-v1.schema.json` (JSON Schema 2020-12,
  generated from the pydantic models in
  `apps/api/app/services/owner_snapshot_service.py`; a test fails if the two
  drift).
- **Envelope:** `schema` is the constant `ridian-operator-snapshot`,
  `version` is the constant `1`, `generatedAt` is UTC, `source` names the
  application, the installed version (`RIDIAN_APP_VERSION`, or `dev` from the
  venv) and the PC's local UTC offset, and `summary` carries counts.
- **Sections:** `recentWork`, `projects`, `obligations`, `approvals`,
  `contacts`, `deals`, `followUps`, `morningBrief`.
- **Strictness:** every object forbids unknown keys, every key is required,
  and every value has a stable type. Missing source values become `""`, `0`,
  `false`, `[]`, or `null` for timestamps, never an absent key, so the
  importer can rely on shape.

### Timestamps and dates

- Every field ending in `At` (except `followUps[].dueAt`) is a **UTC RFC 3339
  timestamp with second precision and a trailing `Z`**, or `null`. The schema
  declares `format: date-time` and a pattern that requires the `Z`; an offset
  other than `Z`, or a naive stamp, fails validation.
- The Operator's stores mix aware UTC stamps (operations, contacts, projects)
  with naive local-time stamps (approvals, deals, a parked run's
  `completed_at`). The exporter reads naive stamps as this PC's local time and
  converts; `source.localUtcOffset` records what that offset was.
- `recentWork[].completedAt` is `null` unless `status` is terminal
  (`completed`, `failed`, `cancelled`). A parked run keeps whatever stamp the
  Operator wrote, but it is not a completion and is not exported as one.
- Unparseable or blank source stamps become `null`, never a fabricated value.
- Date-only fields (`nextActionDate`, `nextDue`, `due.dueDate`,
  `lastCompletedPeriod`, `cadence.date`, `morningBrief.generatedFor`) stay
  `YYYY-MM-DD` as recorded, or `""`.
- **`followUps[].dueAt` is free text exactly as entered** (`"This week"`,
  `"Next available business day"`, or a date someone typed). It is never
  parsed, never normalized, and must be rendered verbatim.

### What each section carries

| Section | Fields | Source |
|---|---|---|
| `recentWork` (max 25, newest first) | id, command, intent, status, startedAt, completedAt, spendUsd, toolsUsed, projectId, background, awaitingInput, sourcesCount, artifactNames, urlsOpened, openQuestions, errorCount | `operation_log_service.list_recent` |
| `projects` | id, name, createdAt, parentId | `operation_log_service.list_projects` |
| `obligations` | id, name, lastCompletedPeriod, createdAt, updatedAt, cadence{kind, day, weekday, date}, nextDue, due{dueDate, status, daysOverdue, missedPeriods} or null | `obligations_service.list_obligations`, `next_due`, `due_status` |
| `approvals` (pending only) | id, operationId, command, tool, reason, question, stagedAt, status, stale, optionCount | `approval_inbox_service.list_pending` |
| `contacts` | id, name, role, company, lastContactAt, createdAt, updatedAt | `memory_service.list_contacts` |
| `deals` | id, title, stage, contactId, contactName, nextAction, nextActionDate, createdAt, updatedAt, lastTouchAt, touchCount, active | `pipeline_service.list_deals` |
| `followUps` (open only) | id, what, who, dueAt, status, createdAt, updatedAt | `dashboard_service.build_dashboard` |
| `morningBrief` | available, generatedFor, and for each of the nine brief sections only `count`, `empty`, `unavailable` | `brief_service.build_brief` |

`artifactNames` carries deliverable filenames only. The run's own
`operation_log.json` ledger is excluded, and browser targets recorded by
`open_browser` are moved to `urlsOpened` as **hostnames only**: a Drive or
Sheets URL carries a document id in its path and sometimes a sharing token in
its query string, so neither the path nor the query travels. `"notebooklm.google.com"`
tells the Owner Workspace what was opened without telling it which document.

### Two counts that are not the same number

- `summary.approvalsPending` counts **staged gate approvals** (invoice,
  proposal, research plan, contact merge, restore) still waiting for an
  answer. `summary.approvalsStale` is the subset older than seven days.
- `summary.operationsAwaitingInput` counts **operations parked in
  `awaiting_input`** across the whole store, which is also what the morning
  brief's `awaiting_approval` section reports. The brief's section keys are
  exported verbatim, so `morningBrief.sections.awaiting_approval.count` equals
  `summary.operationsAwaitingInput`, not `summary.approvalsPending`.

`summary.recentWork` is the number of rows exported (capped at 25);
`operationsAwaitingInput` scans every stored operation, not only those rows.

## What is excluded, and why

The exporter is an allowlist. A field that is not named in a table is not
exported, and a deny list of source fields makes it impossible to name one
by accident: `build_snapshot()` refuses to run if any table names a denied
field, emits a key matching `/token|secret|key|password|credential|cookie|auth/i`,
or produces a string that carries a drive-letter, UNC or home-directory path,
the data directory, or an email address.

**Free text is scrubbed.** Fields of kind `text` (`recentWork[].command`,
`approvals[].command` and `question`, `followUps[].what` and `who`, deal
titles and next actions, contact names, roles and companies, project and
obligation names) have email addresses replaced with `[email]` and phone
numbers with `[phone]`, and paths with `[local path removed]`. The phone
pattern is deliberately strict (three/three/four digit groups with an
optional country code) so dates, times, ids and money are never touched. Two
independent leak detectors re-check every scrubbed field; if a scrub ever
fails to remove what it was asked to, the export refuses itself rather than
leaking. Ids, timestamps, dates and `dueAt` are not free text and are not
scrubbed.

Never exported:

- **Credentials of any kind.** The exporter never reads `local_settings.json`,
  `google_credentials.json`, `google_token.json`, `quickbooks_token.json`,
  `companion_push_vapid.bin`, `.env`, or environment variables. A test
  configures every secret the Operator knows and asserts none of the values
  appear.
- **Local paths.** `artifact_folder`, artifact `path`, approval `folder`, the
  outputs tree, the data directory. Artifacts travel as filenames only.
- **Legacy run folders.** The `outputs/` folder listing (the pre-Operator
  "projects" notion) was removed from the contract; only Operator projects
  travel.
- **Contact emails and phone numbers, notes, and `source`.** Orientation only:
  name, role, company, last contact.
- **Deal `value_usd`, `notes`, and the touch log.** Stage and dates travel;
  money and narrative stay.
- **Operation `steps`, `receipt`, `needs_input` text, `proposed_memory_updates`,
  `source_titles`, `errors`, cost fences, audio flags.** Transcripts and planner
  state are the PC's; only counts and status travel.
- **Approval `kwargs`, `options`, gate evidence, stated numbers, provided
  emails, outcomes.** The website may display that an approval is waiting; it
  may not answer it, so it needs none of the evidence.
- **Obligation `task` (the runnable command) and dismissal bookkeeping.**
- **Morning-brief rows.** Invoice balances, thread subjects and senders,
  calendar summaries, deal values. Only headings, counts, and whether the
  source was reachable.
- **Memory (facts, decisions, brand, profile), the audit log, companion
  devices and push ledger, settings.** Out of scope for v1 by design.
- **Provenance stamps** (`written_by`, `source_op`, `source_run`).

## How to trigger

- **Desktop:** Settings → *Owner snapshot* → **Export**. The status line shows
  the exact file written and the summary counts. Nothing is uploaded.
- **HTTP (loopback only):** `POST http://127.0.0.1:8000/owner-snapshot/export`
  → `{ ok, path, fileName, generatedAt, summary }`. The route calls
  `_require_loopback` and is absent from the companion allowlist, so a paired
  phone receives 403 (`test_companion_access.py`).
- **From the venv (dev data dir):**

  ```powershell
  cd apps\api
  ..\..\.venv\Scripts\python.exe -c "from app.services import owner_snapshot_service as s; print(s.export_snapshot())"
  ```

Files land in `<data_dir>/exports/owner-snapshot-YYYYMMDD-HHMMSS.json`:
`apps/api/exports/` in dev (git-ignored) and
`%APPDATA%\Ridian Operator\exports\` for the installed app. Every export is a
new file; nothing is overwritten. The state store is byte-identical before and
after (pinned by test).

`brief_service.build_brief` is consulted for the `morningBrief` counts. It
reads QuickBooks, Gmail and Calendar when those are connected, the same as
opening the Morning Brief view does; offline or disconnected, the affected
sections report `unavailable: true` rather than a false zero.

## How the website importer will validate it

The future manual importer in ridian-technologies-site must, in this order:

1. Parse as JSON; reject anything over a fixed size (1 MB is generous).
2. Validate against `docs/owner-snapshot-v1.schema.json` with unknown keys
   rejected and format checking on; require `schema == "ridian-operator-snapshot"`
   and `version == 1`.
3. Walk every key and reject any that matches
   `/token|secret|key|password|credential|cookie|auth/i`; walk every string
   value and reject any that matches a drive-letter (`[A-Za-z]:[\\/]`), UNC
   (`\\\\`) or home path, or an email address — the same rules the exporter
   enforces, applied independently.
4. Store the document as an opaque, versioned, revocable import attributed to
   the authenticated Owner principal, with `generatedAt` and `source.version`
   indexed. Never execute, never merge into canonical tables in v1.
5. Render read-only: Needs your attention from `approvals` and `obligations`
   with `due`, Today from `followUps` (render `dueAt` verbatim) and
   `obligations.nextDue`, Recent Operator work from `recentWork`, Projects
   from `projects`, Morning Brief from `morningBrief` counts. Display every
   timestamp in the viewer's zone; they are all UTC.

The importer is deliberately not built in this phase.

## Tests

`apps/api/tests/test_owner_snapshot.py` pins: forbidden-key pattern, configured
secrets never appear, no local path or data directory appears, JSON Schema
round-trip against the committed file (positive and negative, including the
required-`Z` timestamp pattern and `dueAt` staying free text), denied fields
never appear even when source records carry them, free-text emails and phones
are replaced in commands, questions and follow-ups while dates, ids and money
are untouched, a mutated scrub (identity scrub, disabled regex, downgraded
field kind) is refused before anything is written, a mutated allowlist is
refused, naive local timestamps convert to UTC and parked runs export a null
`completedAt`, the ledger file and browser targets never appear in
`artifactNames`, an AST allowlist on the module's imports and on every
attribute it touches, no state writes, exactly one file under `exports/`, and
the loopback route. `test_companion_access.py` pins the 403 for a paired phone.
