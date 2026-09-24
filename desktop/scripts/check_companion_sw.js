// Companion service worker check (v7.7): a push with {withdraw: [tags]}
// closes those notifications — their run was answered, cancelled or
// expired — and a withdrawal with no title shows nothing.
//
// Run:  node scripts/check_companion_sw.js
// Exits 0 with "COMPANION SW OK", or 1 with the failure. Pinned by
// apps/api/tests/test_live_run_state.py.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SW = path.join(__dirname, '..', '..', 'apps', 'api', 'app', 'static', 'companion-sw.js');

function load() {
  const listeners = {};
  const shown = [];
  const registration = {
    async showNotification(title, opts) {
      shown.push({ title, tag: opts.tag, body: opts.body, closed: false, close() { this.closed = true; } });
    },
    async getNotifications(filter) {
      return shown.filter((n) => !n.closed && (!filter || !filter.tag || n.tag === filter.tag));
    },
  };
  const self = {
    addEventListener: (type, fn) => { listeners[type] = fn; },
    registration,
    skipWaiting() {},
    clients: { claim() {} },
  };
  vm.runInNewContext(fs.readFileSync(SW, 'utf8'), { self, console });
  async function push(data) {
    let waited = null;
    listeners.push({ data: { json: () => data }, waitUntil: (p) => { waited = p; } });
    await waited;
  }
  const open = () => shown.filter((n) => !n.closed).map((n) => n.tag);
  return { push, shown, open };
}

(async () => {
  const sw = load();
  // A park and an approval notify, as before.
  await sw.push({ title: 'Ridian is waiting on you', body: 'Which Greg?', tab: 'task', tag: 'op:op_a:q1' });
  await sw.push({ title: 'Approval waiting', body: 'Research plan', tab: 'approvals', tag: 'appr:appr_a' });
  await sw.push({ title: 'Ridian is waiting on you', body: 'Another run', tab: 'task', tag: 'op:op_b:q1' });
  assert.deepEqual(sw.open(), ['op:op_a:q1', 'appr:appr_a', 'op:op_b:q1']);

  // Answered or cancelled: a withdrawal alone closes exactly its tags and shows nothing.
  await sw.push({ withdraw: ['appr:appr_a', 'op:op_a:q1'], tag: 'wd:op_a' });
  assert.deepEqual(sw.open(), ['op:op_b:q1'], 'the other run is untouched');
  assert.equal(sw.shown.length, 3, 'a withdrawal with no title shows nothing');

  // Expired: the same push closes the stale park and says why.
  await sw.push({ title: "Ridian couldn't continue", body: 'Another run', tab: 'task', tag: 'op:op_b:expired',
                  withdraw: ['op:op_b:q1'] });
  assert.deepEqual(sw.open(), ['op:op_b:expired']);

  // Unknown tags and junk entries are harmless; a non-list is not a withdrawal.
  await sw.push({ withdraw: ['op:gone:q9', 7, null, ''], tag: 'wd:gone' });
  assert.deepEqual(sw.open(), ['op:op_b:expired']);
  await sw.push({ withdraw: 'op:op_b:expired', title: 'Plain', tag: 'x' });
  assert.deepEqual(sw.open(), ['op:op_b:expired', 'x']);

  console.log('COMPANION SW OK — withdrawn notifications close; a bare withdrawal shows nothing.');
})().catch((err) => { console.error(err); process.exit(1); });
