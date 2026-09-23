# Owner Workspace sync — keep ridiantechnologies.com/owner current

Ridian Operator keeps the Owner Workspace on ridiantechnologies.com current
by sending it the read-only Owner Snapshot v1 document on its own. The
document is exactly what Settings → Advanced → Owner snapshot → Export
writes to a file (see `owner-snapshot-v1.md`); sync only removes the manual
upload step.

Sync is outbound only. This PC calls the site over HTTPS; the site never
calls this PC and cannot act on it. Code: `apps/api/app/services/sync_service.py`.
Tests: `apps/api/tests/test_owner_workspace_sync.py`.

## Connecting (one click, v7.2)

1. In Ridian Operator, open **Settings → Owner Workspace → Connect**.
2. Ridian asks the site for a pairing request and opens its approval page
   in the default browser. A dialog shows a code such as `BCDF-GHJK`.
3. On the approval page (sign in if asked), check that it shows the same
   code and choose **Approve this device**.
4. The dialog closes by itself. The block reads
   `Connected as <label> · up to date · checked just now` once the first
   sync has gone out, and the rail shows `Owner Workspace · up to date`.

The device label is this PC's name. Nothing is typed or pasted.

While the dialog is open the backend polls the site at the interval the
site asks for (5 seconds; clamped to 2–30). It stops when:

- the token arrives. It is stored DPAPI-wrapped, together with its expiry.
- the owner denies the request. Nothing is saved.
- the site answers 410 because the request expired or was already used.
  Nothing is saved.
- 10 minutes pass. This is the site's own request lifetime, and it is
  enforced on this PC too. Nothing is saved.
- the site fails 5 polls in a row. Nothing is saved.
- the owner chooses **Cancel**. Nothing is saved.

In every case the dialog says which of these happened.

An approval that arrives after Cancel, after a newer Connect, or after the
PC was connected another way is revoked on the site at once. It is never
stored.

### Connect with a token (Advanced)

**Settings → Advanced → Owner Workspace token → Connect with a token…**
is the only paste field. Create a token on the Owner Workspace under
**Device tokens → Advanced**, paste it, and choose **Connect**. The site
states no expiry for a pasted token, so it is not refreshed. It lives its
90 days and then answers 401, and the Operator disconnects.

Nothing is saved until the site has accepted the token. The Operator first
offers it to the pairing exchange (`POST /api/devices/pair`, not on the
site; the site answers 403). It then treats the pasted value as the device
token itself and verifies it with one real push.

## The site contract

The live routes are in the ridian-technologies-site repo at 5558f10:
`src/routes/api/devices/*` and `src/routes/api/operator-snapshot/push`.

| Call | Request | Answers the Operator acts on | Live |
| --- | --- | --- | --- |
| authorize/start | `POST /api/devices/authorize/start`, `Content-Type: application/json`, no Origin, no Authorization, body `{"label": "<device label>"}` | 200 `{"requestId", "userCode", "verifyUrl", "expiresAt", "interval"}`; 429 `{"retryAfterSeconds"}` + `Retry-After`; 400 bad label; 403, 404 or 405 means "no browser approval here" | yes |
| authorize/poll | `GET /api/devices/authorize/poll?request=<requestId>` | 200 `{"status": "pending", "interval"}`; 200 `{"status": "denied"}`; 200 `{"status": "approved", "token", "expiresAt", "label"}` exactly once; 410 `{"status": "expired"}`; anything else is a transient failure | yes |
| push | `POST /api/operator-snapshot/push`, `Authorization: Bearer <device token>`, `Content-Type: application/json`, body = the snapshot (at most 2 MB) | 200 accepted or duplicate; 401 dead token; 429 with `Retry-After`; 413, 415, 422 refused document; 5xx | yes |
| refresh | `POST /api/devices/refresh`, `Authorization: Bearer <device token>` | 200 `{"token", "expiresAt"}` and the old token is dead; 401 dead token; 403, 404 or 405 unsupported | yes |
| revoke | `POST /api/devices/revoke`, `Authorization: Bearer <device token>` | 200 or 204 revoked; anything else is ignored | yes |
| pair | `POST /api/devices/pair`, `Authorization: Bearer <pairing code>`, body `{"label": "<device label>"}` | 200 `{"token", "expiresAt"}`; 400 or 401 refused; 403, 404 or 405 means "no exchange here" | no |

`verifyUrl` must be the approval page (`/owner/devices/approve?…`) on the
same scheme, host and port as the site. It must carry no user info and no
fragment. Anything else is refused before the browser opens.

The site is `https://ridiantechnologies.com` unless **Settings → Advanced →
Owner Workspace site** names another https origin. That field affects the
next pairing only: a connection always talks to the site it was paired
with. `https://www.ridiantechnologies.com` answers 308, and the Operator
never follows a redirect with the token, so use the apex address.

## When it syncs

A sync attempt is triggered by:

- backend startup, 60 seconds after the backend starts;
- an operation reaching a terminal state (completed, failed, cancelled, or
  partial, which is a finished run with errors);
- any approval staged, answered, or voided;
- any obligation or deal write;
- a 30-minute timer, counted from the last attempt.

Every trigger is debounced 20 seconds, so a burst of writes becomes one
attempt. A steady trickle of triggers can hold an attempt back at most 120
seconds. Triggers come from the state store's save listener, so every write
path to those stores counts, including ones added later. A run that ended
before the backend started never counts as a new finish.

**Only changes are sent (v7.2).** Each attempt builds the document and
takes its content hash:

- the hash is the sha256 of the canonical JSON (sorted keys, compact
  separators, UTF-8);
- `generatedAt` and `source.localUtcOffset` are left out, the same rule the
  site uses for its duplicates.

If the hash equals that of the last push the site accepted, whether as
accepted or duplicate, nothing is sent. The attempt is recorded as
`unchanged`, and Settings keeps saying `up to date · checked <time>`.

The timer therefore still runs every 30 minutes but sends only when
something changed. A change can happen without any write, for example a
due date arriving or a new calendar event in the brief; the timer is what
sends those. A push the site did not accept (429, 5xx, a refusal, a network
failure) does not count, so the same content goes out on the next attempt.
A new connection always sends its first snapshot.

## Failures

| Situation | What happens |
| --- | --- |
| Site answers 401 to a push or a refresh (token revoked or expired) | The token is discarded, the connection is marked disconnected, and nothing is sent again until the owner connects anew. Settings says: "Disconnected — the Owner Workspace refused this device's token (revoked or expired), so Ridian stopped sending. Choose Connect to approve this PC again." |
| Token past its known expiry | Disconnected without sending it. |
| Token within 7 days of its known expiry | Refreshed at the start of the next attempt, whether or not anything is then sent, so an idle PC still renews: the timer runs every 30 minutes. The new token replaces the old one. A site without refresh is asked again after a day, not on every attempt. |
| Saved token unreadable (another Windows account, corrupt file) | Disconnected; connect again. |
| 429 | Backs off for at least the site's `Retry-After`, then retries only on a later trigger. |
| Network failure or 5xx | Backs off exponentially (1, 2, 4, 8 minutes up to 30), then retries only on a later trigger. |
| 413, 415 or 422 | Recorded with the site's reason and JSON path, never the content, then backs off like a failure. |
| 3xx | Never followed with the token. Recorded with the target host so the site address can be fixed under Advanced. |
| The exporter's own policy check refuses the document | Nothing is sent; recorded and backed off. |

A failure never schedules a retry by itself. The next trigger, at the
latest the 30-minute timer, is the retry, so a failing site is never hit in
a loop.

## Disconnecting

**Disconnect** discards the token on this PC first; that part always
succeeds. It then asks the site to revoke the token (`POST
/api/devices/revoke`), best effort. If the site does not confirm the
revoke, Settings says to revoke the device's label under Device tokens on
the Owner Workspace.

## Security

- The device token is DPAPI-wrapped at rest in
  `<data_dir>/owner_workspace.json`. That file sits beside
  `local_settings.json`, not under `state/`, so frequent status writes never
  rotate the state backups. The token is decrypted only for the request
  that uses it.
- The token and a pasted code are never logged, never returned by an
  endpoint, never written to the state store, and never part of the
  snapshot. `device_token` and `pairing_code` are on the exporter's deny
  list. The tests check logs, the status and settings routes, the settings
  file, the state store, every pushed document, and the export.
- The pairing request's `requestId` is the polling secret: whoever holds it
  can collect the token after approval.
  - It stays in the backend's memory, is never written to disk, and is
    never returned to the renderer.
  - httpx logs request URLs at INFO, so a filter on the `httpx` logger
    redacts `request=` in poll URLs. The redaction does not depend on
    another module quieting httpx.
  - The renderer gets only the approval page and the code to match.
- HTTPS only. Plain HTTP is accepted solely for a loopback development
  site. The Advanced field refuses anything but a bare origin.
- These routes answer loopback callers only and are not on the companion
  allowlist:
  - `/owner-workspace/status`
  - `/connect/start`
  - `/connect/cancel`
  - `/connect`
  - `/disconnect`

  A paired phone gets 403 from the gate, and each handler checks loopback
  again.
- A push or refresh that finishes after a disconnect never rewrites the
  disconnected state.

## Notes for the site

- **Duplicates.** The site dedupes by the same content hash and stores
  every accepted push. Its retention keeps everything for 7 days, the
  newest per UTC day to 30 days, then only the latest. With skip-unchanged,
  a PC sends one push per change rather than one every 30 minutes.
- **Comparison baseline.** The Operator compares against its own last
  accepted push, not against the site's latest row. Suppose the site's
  latest changes by another route, such as a browser upload of an older
  export. The site keeps that document until the content on this PC next
  changes.
- **Discovering a revoked token.** Without changes, a token revoked on the
  site is found only at the next change or the next refresh. Until then
  Settings keeps saying up to date.
- **Rate limit.** The site allows 60 pushes per hour per token, and
  5 authorize/start calls per hour per IP. Connect explains a 429 with the
  wait.

## Cost on this PC

Each attempt builds the snapshot the same way Export does, including the
morning brief's read-only lookups: today's calendar, the inbox, and unpaid
QuickBooks invoices when those are connected. No model is called, so an
attempt costs no Anthropic or OpenAI spend. Attempts run on a background
thread and never delay the app. An unchanged attempt costs the build and no
network call, apart from a refresh when one is due.
