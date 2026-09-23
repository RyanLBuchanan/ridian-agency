# Owner Workspace sync — keep ridiantechnologies.com/owner current

Ridian Operator keeps the Owner Workspace on ridiantechnologies.com current
by sending it the read-only Owner Snapshot v1 document on its own. The
document is exactly what Settings → Advanced → Owner snapshot → Export
writes to a file (see `owner-snapshot-v1.md`); sync only removes the manual
upload step.

Sync is outbound only. This PC calls the site over HTTPS; the site never
calls this PC and cannot act on it. Code: `apps/api/app/services/sync_service.py`.
Tests: `apps/api/tests/test_owner_workspace_sync.py`.

## Connecting

1. On ridiantechnologies.com, open the Owner Workspace, choose **Device
   tokens**, and create a token for this PC. The site shows it once.
2. In Ridian Operator, open **Settings → Owner Workspace → Connect**. Paste
   the token as the pairing code, keep or change the device label (the
   default is this PC's name), and choose **Connect**.
3. Nothing is saved until the site has accepted the code. On success the
   block reads `Connected as <label> · last sync just now` and the rail
   shows `Owner Workspace · synced just now`.

**Pairing code versus device token.** The Operator first offers the code to
the site's pairing exchange (`POST /api/devices/pair`). Today's site has no
exchange: it issues the device token directly on /owner/devices and answers
the pairing path with 403. The Operator then treats the pasted code as the
device token itself and verifies it with one real push. If the site later
adds the exchange (a short one-time code traded for a token with an
expiry), Connect uses it with no Operator change.

## The site contract

| Call | Request | Answers the Operator acts on | Live today |
|---|---|---|---|
| push | `POST /api/operator-snapshot/push`, `Authorization: Bearer <device token>`, `Content-Type: application/json`, body = the snapshot (at most 2 MB) | 200 accepted or duplicate; 401 dead token; 429 with `Retry-After`; 413, 415, 422 refused document; 5xx | yes |
| pair | `POST /api/devices/pair`, `Authorization: Bearer <pairing code>`, body `{"label": "<device label>"}` | 200 `{"token", "expiresAt"}`; 400 or 401 refused; 403, 404 or 405 means "no exchange here" | no |
| refresh | `POST /api/devices/refresh`, `Authorization: Bearer <device token>` | 200 `{"token", "expiresAt"}` and the old token is dead; 401 dead token; 403, 404 or 405 unsupported | no |
| revoke | `POST /api/devices/revoke`, `Authorization: Bearer <device token>` | 200 or 204 revoked; anything else is ignored | no |

The site is `https://ridiantechnologies.com` unless **Settings → Advanced →
Owner Workspace site** names another https origin. That field affects the
next pairing only: a connection always talks to the site it was paired
with. `https://www.ridiantechnologies.com` answers 308, and the Operator
never follows a redirect with the token, so use the apex address.

## When it syncs

A push is triggered by:

- backend startup, 60 seconds after the backend starts;
- an operation reaching a terminal state (completed, failed, cancelled, or
  partial, which is a finished run with errors);
- any approval staged, answered, or voided;
- any obligation or deal write;
- a 30-minute timer, counted from the last attempt.

Every trigger is debounced 20 seconds, so a burst of writes becomes one
push. A steady trickle of triggers can hold a push back at most 120
seconds. Triggers come from the state store's save listener, so every write
path to those stores counts, including ones added later. A run that ended
before the backend started never counts as a new finish.

## Failures

| Situation | What happens |
|---|---|
| Site answers 401 (token revoked or expired) | The token is discarded, the connection is marked disconnected, and nothing is sent again until the owner connects anew. Settings says why. |
| Token past its known expiry | Disconnected without sending it. |
| Token within 7 days of its known expiry | Refreshed before the push; the new token replaces the old one. A site without refresh is asked again after a day, not on every push. |
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
/api/devices/revoke`), best effort. Today's site has no revoke endpoint, so
Settings says to revoke the device's label under Device tokens on the
Owner Workspace, and the owner revokes it there.

## Security

- The device token is DPAPI-wrapped at rest in
  `<data_dir>/owner_workspace.json`. That file sits beside
  `local_settings.json`, not under `state/`, so frequent status writes never
  rotate the state backups. The token is decrypted only for the request
  that uses it.
- The token and the pairing code are never logged, never returned by an
  endpoint, never written to the state store, and never part of the
  snapshot. `device_token` and `pairing_code` are on the exporter's deny
  list, and the tests check logs, the status and settings routes, the
  settings file, the state store, every pushed document, and the export.
- HTTPS only. Plain HTTP is accepted solely for a loopback development
  site. The Advanced field refuses anything but a bare origin.
- `/owner-workspace/status`, `/connect` and `/disconnect` answer loopback
  callers only and are not on the companion allowlist; a paired phone gets
  403 from the gate, and each handler checks loopback again.
- A push or refresh that finishes after a disconnect never rewrites the
  disconnected state.

## Notes for the site

- `generatedAt` changes on every push, so the site's sha256 dedupe only
  catches byte-identical retries. With the 30-minute timer a PC that stays
  on stores about 48 snapshot rows a day in the append-only table.
- The site allows 10 pushes per hour per token. A busy hour can reach it;
  the Operator backs off on 429 and the timer catches up.
- Until the site adds refresh, a token lives its 90 days and then answers
  401. The Operator disconnects and Settings asks for a new token.

## Cost on this PC

Each push builds the snapshot the same way Export does, including the
morning brief's read-only lookups: today's calendar, the inbox, and unpaid
QuickBooks invoices when those are connected. No model is called, so a push
costs no Anthropic or OpenAI spend. Pushes run on a background thread and
never delay the app.
