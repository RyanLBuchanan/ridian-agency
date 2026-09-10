# Ridian Owner Snapshot v1

A local, read-only, allowlisted export of Operator context for the private
Owner Workspace at ridiantechnologies.com/owner. The Operator writes a file;
Ryan inspects it and moves it by hand. Nothing is uploaded, no network path
exists, and the website gains no authority over this PC.

## Contract

- **Schema:** `docs/owner-snapshot-v1.schema.json` (JSON Schema 2020-12,
  generated from the pydantic models in
  `apps/api/app/services/owner_snapshot_service.py`; a test fails if the two
  drift).
- **Envelope:** `schema` is the constant `ridian-operator-snapshot`,
  `version` is the constant `1`, `generatedAt` is UTC (`...Z`), `source` names
  the application, the installed version (`RIDIAN_APP_VERSION`, or `dev` from
  the venv) and the PC's local UTC offset, and `summary` carries counts.
- **Sections:** `recentWork`, `projects`, `legacyProjects`, `obligations`,
  `approvals`, `contacts`, `deals`, `followUps`, `morningBrief`.
- **Strictness:** every object forbids unknown keys, every key is required,
  and every value has a stable type. Missing source values become `""`, `0`,
  `false`, or `[]`, never an absent key, so the importer can rely on shape.
- **Timestamps:** record timestamps are the Operator's own naive local-time
  ISO 8601 strings. `source.localUtcOffset` (for example `-05:00`) lets the
  importer interpret them. `generatedAt` alone is UTC.

What each section carries:

| Section | Fields | Source |
|---|---|---|
| `recentWork` (max 25) | id, command, intent, status, startedAt, completedAt, spendUsd, toolsUsed, projectId, background, awaitingInput, sourcesCount, artifactNames, openQuestions, errorCount | `operation_log_service.list_recent` |
| `projects` | id, name, createdAt, parentId | `operation_log_service.list_projects` |
| `legacyProjects` (max 30) | name, workflow, channel, modifiedAt, pinned | `project_service.list_recent_projects` |
| `obligations` | id, name, lastCompletedPeriod, createdAt, updatedAt, cadence{kind, day, weekday, date}, nextDue, due{dueDate, status, daysOverdue, missedPeriods} or null | `obligations_service.list_obligations`, `next_due`, `due_status` |
| `approvals` (pending only) | id, operationId, command, tool, reason, question, stagedAt, status, stale, optionCount | `approval_inbox_service.list_pending` |
| `contacts` | id, name, role, company, lastContactAt, createdAt, updatedAt | `memory_service.list_contacts` |
| `deals` | id, title, stage, contactId, contactName, nextAction, nextActionDate, createdAt, updatedAt, lastTouchAt, touchCount, active | `pipeline_service.list_deals` |
| `followUps` (open only) | id, what, who, dueAt, status, createdAt, updatedAt | `dashboard_service.build_dashboard` |
| `morningBrief` | available, generatedFor, and for each of the nine brief sections only `count`, `empty`, `unavailable` | `brief_service.build_brief` |

## What is excluded, and why

The exporter is an allowlist. A field that is not named in a table is not
exported, and a deny list of source fields makes it impossible to name one
by accident: `build_snapshot()` refuses to run if any table names a denied
field, emits a key matching `/token|secret|key|password|credential|cookie|auth/i`,
or produces a string that carries a drive-letter, UNC or home-directory path
or the data directory. Free text that contained a path keeps the text with the
path replaced by `[local path removed]`.

Never exported:

- **Credentials of any kind.** The exporter never reads `local_settings.json`,
  `google_credentials.json`, `google_token.json`, `quickbooks_token.json`,
  `companion_push_vapid.bin`, `.env`, or environment variables. A test
  configures every secret the Operator knows and asserts none of the values
  appear.
- **Local paths.** `artifact_folder`, artifact `path`, approval `folder`, the
  outputs tree, the data directory. Artifacts travel as filenames only.
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
   rejected; require `schema == "ridian-operator-snapshot"` and `version == 1`.
3. Walk every key and reject any that matches
   `/token|secret|key|password|credential|cookie|auth/i`, and every string value
   and reject any that matches a drive-letter (`[A-Za-z]:[\\/]`), UNC (`\\\\`)
   or home path — the same rules the exporter enforces, applied independently.
4. Store the document as an opaque, versioned, revocable import attributed to
   the authenticated Owner principal, with `generatedAt` and `source.version`
   indexed. Never execute, never merge into canonical tables in v1.
5. Render read-only: Needs your attention from `approvals` and `obligations`
   with `due`, Today from `followUps` and `obligations.nextDue`, Recent
   Operator work from `recentWork`, Projects from `projects` and
   `legacyProjects`, Morning Brief from `morningBrief` counts.

The importer is deliberately not built in this phase.

## Tests

`apps/api/tests/test_owner_snapshot.py` pins: forbidden-key pattern, configured
secrets never appear, no local path or data directory appears, JSON Schema
round-trip against the committed file, denied fields never appear even when
source records carry them, a mutated allowlist is refused before anything is
written, an AST allowlist on the module's imports and on every attribute it
touches, no state writes, exactly one file under `exports/`, and the loopback
route. `test_companion_access.py` pins the 403 for a paired phone.
