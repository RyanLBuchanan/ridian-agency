# send_sms — outbound text messages through Twilio

Ridian Operator can send a short text message, and only in one way: the
planner calls `send_sms(recipient_label, body)`, the recipient is looked up
**by label** on an allowlist the operator maintains in Settings, the exact
message is staged as an approval, and nothing leaves the machine until the
operator approves that preview. This is the Operator's only outbound
messaging channel; email remains draft-only.

## How it works

1. **Settings → Text (SMS).** The operator enters the Twilio Account SID, Auth
   Token and the From number (E.164, for example `+15550100123`), and the
   recipient allowlist, one per line:

   ```
   Sarah at the Chamber = +15550100124
   Me = +15550100125
   ```

   The three credentials are DPAPI-wrapped at rest in `local_settings.json`
   (same mechanism as the QuickBooks client secret and token). The Auth Token
   is write-only: the UI shows a "saved" hint and never echoes it. **Test**
   calls Twilio's Account resource (a read) and sends nothing.

2. **The tool.** `send_sms(recipient_label, body)`:
   - `recipient_label` must match an allowlist label. The number is resolved
     here and only here; command text, contact records and memory are never
     a source of a phone number. An unknown label is refused with a message
     telling the planner not to retry with a number or another label.
   - `body` is capped at 320 characters. A link in the body is allowed only
     if the operator typed it verbatim in the command (the same rule the
     email discipline states; for SMS it is enforced in code).
   - Twilio must be configured; otherwise the tool refuses without staging.
   - The first call **stages** `sms_send_pending`: the preview shows the
     label, the E.164 number and the full body. The planner is told to wait.

3. **Approval.** The operator answers in the thread, from the Approvals
   inbox, or from a paired phone (`sms_send_pending` is on the companion's
   approval-kind allowlist, alongside invoice, proposal and research plan).
   Approval re-runs the staged call through the same gate, which re-verifies
   the label→number→body signature; a changed allowlist or body re-asks.
   Cancelling the run voids the staged approval.

4. **Delivery.** One `POST /Accounts/{SID}/Messages.json`. The message SID,
   Twilio status, and price are recorded on the operation (`sms_messages`),
   and the price is added to the run's `spend_usd` so the monthly ledger sees
   it. Twilio often reports `price: null` at send time; the record then shows
   "price pending" and no spend is added.

## The allowlist rule

The allowlist is the whole recipient model. A label is the only thing the
planner can name, so the model cannot text a number it found in a document,
an email, a web page, or its own memory. Two independent checks enforce it:
the resolver looks the label up, then the tool re-checks that the label and
the resolved number belong to the same allowlist entry. A disabled or
mutated resolver is caught by the second check and the send is refused.

Labels must be names, not numbers. Numbers must be E.164. Duplicate labels
are rejected at save time. An unparseable saved allowlist reads as empty, so
a broken setting means nobody is a recipient, never everybody.

## Failure modes

| Situation | Result |
|---|---|
| Label not on the allowlist | Refused, `sms_recipient_unknown`, no approval staged |
| Body over 320 characters | Refused, `sms_body_too_long` |
| Link not typed verbatim in the command | Refused, `sms_url_not_typed` |
| Twilio not configured | Refused, `sms_not_configured`, no approval staged |
| Operator declines the preview | `sms_declined`; the planner is told not to retry |
| Run cancelled while pending | Approval voided; answering later is refused |
| Twilio rejects or is unreachable | `sms_send_failed` with Twilio's own error text; a red error on the timeline; nothing recorded as sent |
| Twilio returns no message SID | Treated as a failure, never a silent success |

Credentials never appear in logs, operation records, the approval inbox, or
the Owner Snapshot export. Logs carry only message SIDs and statuses. The
snapshot's free-text scrub replaces any phone number that appears in a
command with `[phone]`, and `sms_messages` is on the exporter's deny list.

## 10DLC note for future client use

Today the From number is the operator's own Twilio number and the
recipients are the operator's own allowlist, which is the low-volume,
person-to-person case. Before any client-facing or higher-volume use in the
United States, the sending number must be registered under a 10DLC brand and
campaign with the carriers (or use a toll-free number verified for
messaging). Unregistered application-to-person traffic is filtered or
blocked, and the cost per message changes with the campaign type. Treat that
registration, plus a recipient opt-in and opt-out record, as prerequisites
for extending the allowlist beyond people who asked to be on it.
