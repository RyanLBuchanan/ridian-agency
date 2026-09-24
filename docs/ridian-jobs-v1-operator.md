# Ridian Jobs v1 — the Operator side

The owner types a command on the Owner Workspace
(ridiantechnologies.com/owner). Ridian Operator on this PC picks it up,
runs it, and reports back. The site never executes anything; this PC does,
under the same rules as a command typed here.

- The contract is ridian-technologies-site `docs/ridian-jobs-v1.md`, at
  d20e325 or later. It defines the endpoints, statuses, the state machine
  and the result allowlist, and this side follows it exactly.
- Code: `apps/api/app/services/jobs_service.py`, plus a stamp and a resume
  hook in `operator_service.py`.
- Tests: `apps/api/tests/test_owner_jobs.py`.

## Turning it on

1. Connect this PC under **Settings → Owner Workspace → Connect** (see
   `owner-workspace-sync.md`).
2. On the Owner Workspace, open **Devices**. Turn on **Allow this device to
   run commands** for this PC.

The Owner Workspace block in Settings shows one of these lines:

| Line | Meaning |
| --- | --- |
| Accepting commands from the Owner Workspace | Allowed; checking every 15 seconds. |
| Running a command from the Owner Workspace | A job is in progress here. |
| Owner Workspace has not allowed this PC to run commands | The site answered 403 `jobs_not_allowed` or a push said `allowJobs: false`; checking every 60 seconds. |
| Checking the Owner Workspace for commands | Connected, not asked yet. |

Nothing shows while the PC is disconnected.

## Polling

While connected, the engine sends `POST /api/jobs/claim` with the device
token. The first claim goes out 15 seconds after startup.

| Site answer | Next claim |
| --- | --- |
| 204 (nothing queued) | in 15 s |
| 200 (a job) | 15 s after that job is finished here |
| 403 `jobs_not_allowed` | in 60 s, and Settings says so |
| 429 | after `Retry-After` |
| 5xx, a redirect, or a network failure | backs off from 15 s, doubling to a 5-minute cap |
| 401 | never: the token is dead, so the Owner Workspace connection is dropped exactly as a push 401 drops it |

**The switch takes effect at once (v7.4).** The site reports `allowJobs`
(true or false) on every authenticated snapshot push answer and in the claim
403 body (site 47484f6 or later).

- A push that says `true` while this PC thought it was not allowed makes
  the next claim go out immediately, with no 60-second wait.
- A push that says `false` stops claiming at once.
- A 403 is always "not allowed", whatever its body says, so a
  contradictory body can never start a claim loop.

**One job at a time.** Nothing is claimed while a job is non-terminal on
this PC: running, parked on an approval, or waiting to report its result.

**Disconnected, nothing happens.** No claim and no report is sent, and no
job starts.

## How a job runs

A claimed job's command runs through `operator_service.run_operation`, the
same call the command bar makes. It gets the same planner and tools, the
same approval gates, cost ceilings and recipient allowlists, and it runs in
the foreground (not background mode), exactly as a typed command would.

The only difference is a stamp on the operation record:
`source: "owner-workspace"` and `job_id`. Only `jobs_service` can set the
stamp. Neither the command bar nor the phone's `/operations/run` has a
field for it.

The sidebar thread shows **From Owner Workspace** under the command. When
the run is opened, its command is labelled **From Owner Workspace** instead
of **You**.

### Refused before running

These jobs are reported `failed`, and nothing runs:

| `result.status` | When |
| --- | --- |
| `stale` | `createdAt` is more than 1 hour before this PC's clock, even if the site handed the job out. |
| `too_long` | The command is over 2000 characters. |
| `invalid` | No readable `createdAt`, or an empty command. |

If the run could not start at all (for example, no Anthropic key), the job
is reported `failed` with `not_started` and the reason.

## What is reported

| On this PC | Reported to the site |
| --- | --- |
| The run starts | `running`, with `operationId` |
| The run parks on a gate approval (the gate staged one in the inbox) | `awaiting_approval` |
| The run parks on a question (`request_missing_info`, or any needs-input with nothing staged) | `awaiting_input` (v7.5); `awaiting_approval` to a site that does not have it yet |
| An in-thread answer resumes it | `running` |
| It finishes: `completed` or `partial` | result `completed` |
| It finishes: `failed` | result `failed` |
| It finishes: `cancelled`, including Dismiss from the pending strip | result `cancelled` |

Reports go out in order from an outbox kept in `<data_dir>/owner_jobs.json`,
beside `owner_workspace.json`. After a result is accepted, an Owner
Workspace sync is triggered so the snapshot catches up.

### Approvals

Approvals are answered exactly as today:

- in this PC's thread;
- in this PC's approval inbox;
- in the phone companion's inbox.

Nothing new is exposed, and nothing on the site can answer one. An inbox
answer (PC or phone) executes the staged action and ends the run without
resuming the planner. The job then reports its result.

A **question** (`request_missing_info`) stages nothing in the inbox. It is
answered in the run itself, on this PC. The Approvals page lists it under
"Waiting for your answer" with a button that opens the run (v7.5). Dismiss
cancels it, as for any parked run.

## Seeing a job run on this PC (v7.5)

**Why:** on 2026-09-24 a job run (`op_3bb0dfb95e13`) parked at 10:34 on a
`request_missing_info` question, and nothing on the PC showed it:

- the Approvals page listed only staged gate approvals, and a question
  stages none;
- the run was not open;
- the Operator reported `awaiting_approval` to the site.

The phone push did fire, but the paired phone had no push subscription, so
nothing was delivered.

**What happens now**, for every run started from a job:

| Moment | On this PC |
| --- | --- |
| The job is claimed | A Windows notification "Ridian is working on: <command>". The run is pinned at the top of the Operations list (whatever project is selected), scrolled into view and highlighted. When nothing else is going on in the chat pane (no run in flight, no question being answered, no Settings/Brief/Approvals page open), the run opens there live. |
| It parks on a gate approval | A Windows notification "Ridian needs you: <command>" and the Approvals badge. The approval stays answerable on the Approvals page as before. |
| It parks on a question | The same notification and the amber waiting badge on Approvals in the sidebar (v7.5 had its own "Waiting on you" item; since v7.6 the count rides the Approvals nav item). The Approvals page lists it under "Waiting for your answer". |
| Either park | The existing phone push, sent by the backend for every parked run. |
| A notification is clicked | The window comes to the front and the run opens: live while it runs, or at its question once parked. |
| A parked run cannot continue (v7.6) | A Windows notification "Ridian couldn't continue: <command>" and a phone push, once. The badges clear. A thread showing the run stops waiting for an answer and shows why, with "Send again". |

**How it works:**

- The backend keeps a numbered notice feed per process: `claimed` when a
  job run starts, `parked` once per park, with `park: "approval"` or
  `"question"`, and `expired` (v7.6) when a parked run could not continue.
  The `expired` notice is sent for any parked run, including one typed on
  this PC.
- The window polls the feed every 3 seconds
  (`GET /owner-workspace/jobs/notices?after=&epoch=`).
- `renderer/job_notices.js` decides what is new, so each notification fires
  once however often the window polls or the backend re-answers. A
  restarted backend starts a new numbered stream.
- The live view replays the run's events from
  `GET /owner-workspace/jobs/events` through the same event handler a typed
  run uses. So a question arms the composer's answer mode, exactly as in a
  typed run.
- `GET /approvals/questions` lists the parked questions.
- All three routes are loopback-only and off the companion allowlist.
- Notices hold the command's first line in memory, for the local
  notification only. They are never written to disk or logged.

### Opening a run that is still going (v7.7)

**Why:** on 2026-09-24 job e52e4f29 (`op_5d6d24ad87a7`, a research
packet) was claimed at 14:25:21.777 and parked on its research-plan
approval at 14:25:31.27. A run writes `operation_log.json` into its folder
only when it first parks or ends. Opened in between, the pane read the
folder, found no log, and painted the live run "Failed — Could not
rehydrate this operator run". It stayed that way after the park.

The auto-open on claim had fired, but its live view stopped at once. The
pane still held the previous job run (`op_5d8e6e5c475d`, auto-opened at
14:23), and the first live tick took the different run id for "the window
moved on". The renderer keeps no log on disk, so which click opened the
run then cannot be recovered. Every path that reads the folder is a click:
the pinned rail row, a notification, the history panel, or "Open the run".

**What happens now:**

- The pane's status comes from the run's live state,
  `GET /operations/live?operation_id=&artifact_folder=` (PC only). It
  answers from the live session first, then the operations store. It never
  depends on whether the folder could be read.
- A run that is alive but has no folder log yet is shown from memory:
  - a job run in flight is followed live, as the auto-open does;
  - a run waiting on the owner shows its question;
  - any other run shows as running, and its folder loads once it parks or
    ends.
- The live state wins over what the folder last recorded. A run resumed
  since its last park shows as running, and a dismissed one as cancelled.
- "Failed" appears only for a run that failed. An unknown run whose folder
  cannot be read shows "Could not load run".
- The auto-open sets the pane to the claimed run before following it.

### Phone notifications are withdrawn (v7.7)

When a run is answered (in the thread, from the Approvals inbox, or from
the phone) or cancelled, the phone notifications it raised are closed: its
parks and its staged approvals. The PC sends a push listing their tags,
with nothing to show, and the companion's service worker closes them. When
a run expires, its "Ridian couldn't continue" push carries the same list.
Only delivered notifications are withdrawn, once each (ledger keys
`wd:<tag>`). Chrome may show its own "updated in the background" notice
for a push that shows nothing, once the site's small allowance for such
pushes is used up.

## Handoff: the site change for `awaiting_input`

Give this to the site session. Until it ships, the site answers
`awaiting_input` with 400 `invalid_status`. The Operator then resends that
report as `awaiting_approval`, and keeps using `awaiting_approval` until
the Operator restarts. **Restart Ridian Operator once after the site
ships.**

The change, against ridian-technologies-site `docs/ridian-jobs-v1.md` (at
47484f6 or later) and its code:

1. **Statuses table:** add the row
   `| awaiting_input | The run is paused on a question only the owner can answer; it is answered on the PC. | device |`.
2. **State machine table:** add these rows:
   - `| claimed | awaiting_input | device | POST /api/jobs/{id}/status |`
   - `| running | awaiting_input | device | POST /api/jobs/{id}/status |`
   - `| awaiting_input | running | device only | POST /api/jobs/{id}/status |`
   - `| awaiting_input | completed, failed, cancelled | device | POST /api/jobs/{id}/result |`

   Also extend the existing `claimed, running, awaiting_approval` result
   row to include `awaiting_input`. There is no direct move between
   `awaiting_input` and `awaiting_approval`: a run resumes (`running`)
   before it can park again.
3. **`POST /api/jobs/{id}/status`:** `status` may be `running`,
   `awaiting_approval` or `awaiting_input`.
   - `running` is allowed from `claimed`, `awaiting_approval` or
     `awaiting_input`.
   - `awaiting_approval` and `awaiting_input` are each allowed from
     `claimed` or `running`.
4. **`POST /api/jobs/{id}/result`:** allowed from `claimed`, `running`,
   `awaiting_approval` or `awaiting_input`.
5. **`$lib/operator-jobs/types.ts`:**
   - add `'awaiting_input'` to `JOB_STATUSES`, `ACTIVE_STATUSES` (so a
     token refresh moves such jobs) and `PROGRESS_STATUSES`;
   - `JOB_STATUS_LABELS.awaiting_input = 'Waiting for your answer on the PC'`.
6. **`$lib/server/operator-jobs.ts` `TRANSITIONS`:**
   - `claimed` gains `'awaiting_input'`;
   - `running` gains `'awaiting_input'`;
   - new entry `awaiting_input: ['running', 'completed', 'failed', 'cancelled']`.
7. **Database:** `ridian_operator_jobs_status_check` must accept
   `'awaiting_input'`. Change the `check()` in `db/schema.ts` and generate
   one migration.
8. **`/owner` thread:** style `awaiting_input` like `awaiting_approval`, the
   amber "waiting" badge.
9. **Tests:**
   - the state-machine table test gains the four new transitions;
   - the status test covers `claimed → awaiting_input → running →
     awaiting_input → completed`;
   - the device-only test covers `awaiting_input → running`;
   - a refresh moves an `awaiting_input` job to the new token.

## The result

The body follows the contract's allowlist:

```json
{"status": "completed", "result": {"status": "completed", "replyText": "…",
 "artifactNames": ["recap.docx"], "toolsUsed": ["draft_document"],
 "spendUsd": 0.42, "openQuestions": 0, "errorCount": 0}}
```

- `status` inside `result` is the operation's own status: `completed`,
  `partial`, `failed` or `cancelled`. It is `expired` for a parked run
  that could not continue (see below); the job itself is `failed`.
- `artifactNames`: deliverable filenames only, computed the way the
  snapshot's recentWork does it (`owner_snapshot_service.deliverable_names`).
  A name the site would refuse is left out, not altered: one containing
  `/`, `\`, `@` or a control character, or longer than 200 characters.
- `toolsUsed`: the tools the run used, each listed once.
- `spendUsd`: the run's spend, capped at 1000.
- `openQuestions` and `errorCount`: counts, as in the snapshot. Answered
  questions stay in `openQuestions`.

`replyText` is the run's receipt (for a failed run with no receipt, its
last error; for an expired run, why it expired). It is prepared in three
steps:

1. Cut at a word boundary with "…" when it is longer than the exporter's
   2000-character read limit. The cut never splits an address.
2. Passed through the snapshot exporter's free-text scrub
   (`owner_snapshot_service.scrub_free_text`), the same one the snapshot
   uses:
   - local paths and the data directory are removed;
   - email addresses become `[email]` and phone numbers become `[phone]`;
   - the site refuses every `@`, so any word still holding one (an address
     the email pattern missed, such as `name@localhost`) becomes `[email]`
     and a lone `@` becomes `(at)`;
   - the result is self-checked.
3. Control characters other than line breaks and tabs are dropped.

The operation record on this PC keeps the original receipt.

**When the site refuses a result** (400 or 422):

- The operation is marked with the reason and path, in
  `owner_workspace_result` and a timeline step.
- The job ends here, and the result is **never resent**, with this content
  or any other.

**Other answers:**

- A transient failure (network, 5xx, 429) resends the byte-identical body
  later.
- 409 means the site already holds a final state; the job ends here.
- 403 `not_your_job` or 404 means the site no longer lists the job for this
  PC; the job ends here as `orphaned`.

## Restarts and disconnects

- **A restart** keeps the current job, and the engine recovers it:
  - an operation that already ended reports its result;
  - a parked one keeps waiting, and the owner's answer continues it (see
    "Parked runs survive a restart" below);
  - a run that died with the process is reported `failed` with
    `interrupted`.

### Parked runs survive a restart (v7.6)

**Why:** `op_3bb0dfb95e13` parked on a question at 10:34:51 on
2026-09-24. The app was quit and relaunched at 11:49:00 (the same 0.9.14
install; Windows logged no crash, sleep or restart in between). The parked
session lived only in memory, so the answer at 11:53 got "That operation
is no longer active". There was no parked-session timeout: the app quit
ended it.

**What happens now:**

- Every park (on a question or a gate approval) also writes
  `state/parked/<operation id>.json`. It holds what the answer needs to
  continue the run: the full operation record (every gate flag), the run
  folder, the planner system prompt, the conversation so far (the model's
  thinking signatures included), and the run's text caches.
- An answer to a run whose session is gone rebuilds it from that file and
  continues. This covers a lost session while the app runs and a restart.
- The file is deleted when the run ends, is dismissed, or is answered from
  the Approvals inbox. It is local only: never synced, exported, logged or
  included in state backups.
- From the moment an answer is accepted until the run parks again or ends,
  the file is marked `resuming`. A restart in that window means the run
  cannot pick up from the middle, and replaying from the question could
  repeat what it already did, so it expires.
- At startup, before the jobs engine starts, every waiting run is checked.
  A run with a usable parked file stays waiting. Any other waiting run
  expires, because nothing can continue it: the file is missing (it parked
  before 0.9.16), unreadable, or marked `resuming`.

**An expired run:**

- The operation is marked `failed`, with the reason in its errors, in an
  `expired` field, and in a timeline step. Its run-folder log is updated
  too, so a reopened thread shows the ending.
- Its staged approvals are voided.
- The site hears `failed` (from `awaiting_input` or `awaiting_approval`),
  with `result.status` `expired` and the reason as `replyText`.
- One notification on this PC and one phone push. The waiting and
  Approvals badges drop it.
- Answering it, or any run that already ended, never shows a bare error.
  The thread says it expired (or already ended) and offers "Send again",
  which sends the original command as a new run.
- **Closing the app (v7.8, 0.9.18).** Closing used to kill the backend at
  once, with runs in flight. Now:
  - Before the backend is killed, it is asked to drain (`POST /app/drain`,
    PC only). Nothing new starts or is claimed.
  - Each run in flight finishes its current step (the model turn and the
    tools it called) and is checkpointed at that boundary
    (`state/parked/<id>.json`, state `checkpoint`).
  - The backend gets 20 seconds; the app waits up to 25.
  - After the restart, the run resumes from exactly that boundary: the same
    conversation, record and context, with no new message. The window gets
    a "Ridian is continuing: <command>" notice, and a job run keeps its job,
    so the site hears the result as usual.
  - Every run is marked `running` on disk when it starts. A run still inside
    a step when the time runs out, or when the app is killed, expires after
    the restart as parked runs that cannot continue do: failed, reason
    `mid_step`, reported to the site, notified once. It never replays a
    step whose outcome is unknown.
- **When the connection ends while a job is in progress** (Disconnect, or a
  401), the job is abandoned here and nothing more is reported.
  - The operation itself keeps running on this PC like any other.
  - On the site, the job stays in its last reported status. A later
    pairing gets a new token, and jobs move between tokens only on a
    refresh.

## Privacy and the phone

- The command text is never logged and never written to
  `owner_jobs.json`. The operation record keeps it, as for any typed
  command.
- Every jobs route is loopback-only and absent from the companion
  allowlist: `/owner-workspace/status`, `/owner-workspace/jobs/notices`,
  `/owner-workspace/jobs/events` and `/approvals/questions`. A paired phone
  gets 403 on each (pinned by `test_owner_jobs.py`). The phone keeps what it
  had: the recent operations list, the approval inbox, and the parked-run
  push.

## Related change: the first push after pairing

The first snapshot push after a pairing always sends, so the new token is
exercised at once:

- `_connect` sets `force_next_push`;
- only a push the site accepts clears it;
- until then, skip-unchanged does not apply.

0.9.12 already reset the stored hash on pairing. The flag makes the
guarantee explicit, independent of what the record carries.

## Notes

- **Clock:** "stale" is judged by this PC's clock against the site's
  `createdAt`. A PC clock more than an hour off would misjudge; Windows
  keeps it in sync by default.
- **A bare `@` (fixed in v7.4):** the site refuses any `@`, not only
  addresses. `@jane` in a command used to sink a whole snapshot push. The
  neutralizing step now lives in the exporter's shared free-text scrub, so
  the snapshot and job results handle it the same way. A deliverable
  filename holding `@` is left out of `artifactNames`, and the exporter's
  self-check refuses any `@` that gets past the coercer.
