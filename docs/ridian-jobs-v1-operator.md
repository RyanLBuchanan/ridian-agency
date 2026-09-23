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
| Owner Workspace has not allowed this PC to run commands | The site answered 403 `jobs_not_allowed`; checking every 5 minutes. |
| Checking the Owner Workspace for commands | Connected, not asked yet. |

Nothing shows while the PC is disconnected.

## Polling

While connected, the engine sends `POST /api/jobs/claim` with the device
token. The first claim goes out 15 seconds after startup.

| Site answer | Next claim |
| --- | --- |
| 204 (nothing queued) | in 15 s |
| 200 (a job) | 15 s after that job is finished here |
| 403 `jobs_not_allowed` | in 5 minutes, and Settings says so |
| 429 | after `Retry-After` |
| 5xx, a redirect, or a network failure | backs off from 15 s, doubling to a 5-minute cap |
| 401 | never: the token is dead, so the Owner Workspace connection is dropped exactly as a push 401 drops it |

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
| The run parks on an approval or a question | `awaiting_approval` |
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

## The result

The body follows the contract's allowlist:

```json
{"status": "completed", "result": {"status": "completed", "replyText": "…",
 "artifactNames": ["recap.docx"], "toolsUsed": ["draft_document"],
 "spendUsd": 0.42, "openQuestions": 0, "errorCount": 0}}
```

- `status` inside `result` is the operation's own status: `completed`,
  `partial`, `failed` or `cancelled`.
- `artifactNames`: deliverable filenames only, computed the way the
  snapshot's recentWork does it (`owner_snapshot_service.deliverable_names`).
  A name the site would refuse is left out, not altered: one containing
  `/`, `\`, `@` or a control character, or longer than 200 characters.
- `toolsUsed`: the tools the run used, each listed once.
- `spendUsd`: the run's spend, capped at 1000.
- `openQuestions` and `errorCount`: counts, as in the snapshot. Answered
  questions stay in `openQuestions`.

`replyText` is the run's receipt (for a failed run with no receipt, its
last error). It is prepared in four steps:

1. Cut at a word boundary with "…" when it is longer than the exporter's
   2000-character read limit. The cut never splits an address.
2. Passed through the snapshot exporter's free-text scrub
   (`owner_snapshot_service.scrub_free_text`): local paths and the data
   directory are removed, email addresses become `[email]`, phone numbers
   become `[phone]`, and the result is self-checked.
3. The site refuses every `@`. Any word still holding one (an address the
   email pattern missed, such as `name@localhost`) becomes `[email]`. A
   lone `@` becomes `(at)`.
4. Control characters other than line breaks and tabs are dropped.

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
  - a parked one keeps waiting, since the inbox or Dismiss can still end
    it;
  - a run that died with the process is reported `failed` with
    `interrupted`.
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
- There is no jobs route. The jobs state appears only inside the
  loopback-only `/owner-workspace/status`, which the companion allowlist
  does not include; `test_owner_jobs.py` pins this.

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
- **Snapshot and a bare `@`:** the exporter's scrub replaces email-shaped
  text but not a bare `@` (for example `@jane` in a command). The site's
  importer refuses any `@`, so a snapshot carrying one would be rejected.
  The job result handles this itself (step 3 above). The snapshot does not
  yet.
