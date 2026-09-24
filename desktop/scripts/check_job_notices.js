// Notify-once check for Owner Workspace job notices (v7.5, renderer/job_notices.js).
//
// Run:  node scripts/check_job_notices.js
// Exits 0 with "JOB NOTICES OK", or 1 with the failure. Pinned by
// apps/api/tests/test_job_visibility_renderer.py; the real-DOM half (the
// window's poll, notifications, badges, live view, Approvals page) is the
// "Job visibility (real DOM)" section of check_settings_layout.js.

const assert = require('node:assert/strict');
const path = require('node:path');

const jn = require(path.join(__dirname, '..', 'renderer', 'job_notices.js'));

const OP = 'op_3bb0dfb95e13';
const claimed = { seq: 1, kind: 'claimed', operation_id: OP, command: 'Draft a follow-up to Greg about the Navigator pilot' };
const parked = { seq: 2, kind: 'parked', park: 'question', operation_id: OP, command: claimed.command, question: 'What is the Navigator pilot?' };
const gate = { seq: 3, kind: 'parked', park: 'approval', operation_id: 'op_other', command: 'Invoice Sandy Alvarez $250' };

// 1. A fresh window sees what this backend process has, in order.
let step = jn.fresh(jn.initialState(), { epoch: 'e1', latest: 2, notices: [parked, claimed] });
assert.deepEqual(step.notices.map((n) => n.seq), [1, 2]);
assert.deepEqual(step.state, { epoch: 'e1', lastSeq: 2 });

// 2. Polling again — even if the backend re-sends everything — shows nothing new.
for (let i = 0; i < 5; i += 1) {
  const again = jn.fresh(step.state, { epoch: 'e1', latest: 2, notices: [claimed, parked] });
  assert.deepEqual(again.notices, [], 'a notice notifies once, not on every poll');
  assert.deepEqual(again.state, step.state);
}

// 3. Only the newer one when a third arrives.
step = jn.fresh(step.state, { epoch: 'e1', latest: 3, notices: [claimed, parked, gate] });
assert.deepEqual(step.notices.map((n) => n.seq), [3]);

// 4. A restarted backend (new epoch) numbers from 1 again: its notices are new.
const restarted = jn.fresh(step.state, { epoch: 'e2', latest: 1, notices: [claimed] });
assert.deepEqual(restarted.notices.map((n) => n.seq), [1]);
assert.deepEqual(restarted.state, { epoch: 'e2', lastSeq: 1 });

// 5. Malformed answers change nothing and show nothing.
for (const bad of [null, undefined, {}, { notices: 'x' }, { epoch: 'e1', notices: [{ seq: 'nope' }, null] }]) {
  const out = jn.fresh(step.state, bad);
  assert.deepEqual(out.notices, [], JSON.stringify(bad));
}

// 6. The words and the badges.
assert.equal(jn.text(claimed), 'Ridian is working on: Draft a follow-up to Greg about the Navigator pilot');
assert.equal(jn.text(parked), 'Ridian needs you: Draft a follow-up to Greg about the Navigator pilot');
assert.equal(jn.text({ kind: 'mystery' }), '');
assert.equal(jn.badge(parked), 'waiting', 'a question raises "Waiting on you"');
assert.equal(jn.badge(gate), 'approvals', 'a gate approval raises the Approvals badge');
assert.equal(jn.badge(claimed), '');
// v7.6: a parked run that could not continue — its own words, no new badge
// (the window refreshes the badges, which drops it from them).
const expired = { seq: 4, kind: 'expired', operation_id: OP, command: claimed.command };
assert.equal(jn.text(expired), "Ridian couldn't continue: Draft a follow-up to Greg about the Navigator pilot");
assert.equal(jn.badge(expired), '');
const long = jn.text({ kind: 'claimed', command: 'x'.repeat(300) + '\nsecond line' });
assert.ok(long.length <= 'Ridian is working on: '.length + 90 && long.endsWith('…'), long);

console.log('JOB NOTICES OK — each notice notifies once; a restarted backend starts a new stream.');
