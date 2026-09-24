// Settings layout regression harness (v6.1).
//
// Renders the REAL renderer/index.html in Chromium at several window widths,
// fills every status line with realistic long text (the QuickBooks
// "Connected to sandbox (company …)" string that triggered the collision),
// and measures actual bounding rectangles. Fails if any settings block
// overlaps another vertically, or if a status line escapes its own block.
//
// Run:  npx electron scripts/check_settings_layout.js
// Exits 0 with "LAYOUT OK", or 1 and prints every collision found.
// Pinned by apps/api/tests/test_settings_layout.py.

const { app, BrowserWindow } = require('electron');
const path = require('node:path');

// Default restored size first, then progressively narrower, down to the
// window's configured minWidth (880).
const WIDTHS = [1280, 1100, 1000, 940, 880];
const HEIGHT = 860;

// Long, realistic status text for every note — the reported bug needed the
// QuickBooks line to wrap, so make each line wrap-capable.
const STATUS_TEXT = {
  'settings-test-anthropic-status': '✓ Anthropic works — verified live with claude-opus-4-8.',
  'settings-anthropic-key-hint': 'Saved key ending ••••4f2a is in use for every planner turn.',
  'settings-test-openai-status': '✓ OpenAI works — verified live (Whisper + read-aloud).',
  'settings-openai-key-hint': 'Optional — only needed for microphone voice input (Whisper).',
  'settings-qbo-secret-hint': 'Client secret saved; leave blank to keep the stored value.',
  'settings-qb-status': 'Connected to sandbox (company 9341457602986271).',
  'settings-drive-status': 'Not connected.',
  'settings-gmail-status': 'Gmail draft access is not granted — Connect (or Reconnect) Google to grant it.',
  'settings-calendar-status': 'Calendar read access is not granted — Connect (or Reconnect) Google to grant it.',
  'settings-drive-note': 'Connected as ryan@ridiantechnologies.com — Test to verify.',
  'settings-gmail-note': 'Connected as ryan@ridiantechnologies.com — Test to verify.',
  'settings-calendar-note': 'Connected as ryan@ridiantechnologies.com — Test to verify.',
  // v6.9 Phone companion — the longest realistic line: URL + a device row.
  'settings-companion-status': 'Listening on your Wi-Fi — on the phone visit http://192.168.96.201:8000/companion · Pixel 7 (seen 2026-08-23 09:14) Revoke',
  // v7.1 Owner Workspace sync — connected, plus the longest error line.
  'settings-ows-note': 'Keeps ridiantechnologies.com/owner current with a read-only summary.',
  'settings-ows-status': 'Connected as RYAN-DESKTOP · last sync 12 min ago · The Owner Workspace is rate limiting this device. Ridian will try again after the limit resets.',
  // v7.3 Ridian Jobs — the longest line: the not-allowed text plus an error.
  'settings-ows-jobs': 'Owner Workspace has not allowed this PC to run commands · Could not reach ridiantechnologies.com (ConnectTimeout).',
  // Owner snapshot export (under Advanced since v7.1).
  'settings-export-snapshot-status': 'Saved owner-snapshot-20260922-101500.json in the Ridian Operator exports folder — 25 operations, 3 pending approvals, 2 obligations due. Nothing was uploaded.',
};

const MEASURE = `(() => {
  const view = document.getElementById('settings-view');
  const main = document.querySelector('.operator-main');
  if (main) main.classList.add('hidden');
  view.classList.remove('hidden');
  // v7.1: the Owner snapshot export block lives under Advanced — measure
  // with Advanced open so that block (and the grid) are checked too.
  const adv = document.getElementById('settings-advanced');
  if (adv) adv.open = true;
  const TEXT = ${JSON.stringify(STATUS_TEXT)};
  for (const [id, text] of Object.entries(TEXT)) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  // Force layout, then measure.
  void view.offsetHeight;
  const form = document.getElementById('settings-form');
  const blocks = [...form.querySelectorAll('.settings-block')];
  const read = (el) => {
    const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right,
             height: r.height, width: r.width };
  };

  // STRUCTURE-AGNOSTIC: every element that actually paints text. The
  // reported bug was "two lines of different rows rendered on top of each
  // other", so this is the direct expression of it and it works on ANY
  // markup — old sibling rows or new self-contained blocks.
  const painted = [...form.querySelectorAll('span, label, input, select, button')]
    .filter((el) => {
      const r = el.getBoundingClientRect();
      if (r.height < 1 || r.width < 1) return false;
      const hasText = (el.textContent || '').trim().length > 0;
      return hasText || el.tagName === 'INPUT' || el.tagName === 'SELECT';
    })
    .map((el) => ({
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      text: (el.textContent || el.placeholder || '').trim().slice(0, 48),
      rect: read(el),
      path: (() => { const p = []; let n = el;
        while (n && n !== form) { p.push(n.className || n.tagName); n = n.parentElement; }
        return p.join(' < '); })(),
    }));

  return {
    scrollWidth: form.scrollWidth,
    clientWidth: form.clientWidth,
    painted,
    blocks: blocks.map((b, i) => ({
      index: i,
      label: (b.querySelector('.settings-row-label') || {}).textContent || '(none)',
      rect: read(b),
      notes: [...b.querySelectorAll('.settings-row-note')].map((n) => ({
        text: n.textContent.trim().slice(0, 60), rect: read(n),
      })),
      rows: [...b.querySelectorAll('.settings-row')].map((r) => read(r)),
    })),
  };
})()`;

// One window, RESIZED between measurements — the same thing a user does,
// and it exercises the media query on the way down.
let win = null;

async function openOnce() {
  win = new BrowserWindow({
    width: WIDTHS[0], height: HEIGHT, show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  await win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
  // Let the renderer's own scripts settle (they fail without a backend;
  // the layout under test does not depend on them).
  await new Promise((r) => setTimeout(r, 700));
}

async function measureAt(width) {
  win.setContentSize(width, HEIGHT);
  await new Promise((r) => setTimeout(r, 250));   // layout + media queries
  return win.webContents.executeJavaScript(MEASURE, true);
}

function check(width, data) {
  const problems = [];
  const EPS = 0.5;   // sub-pixel rounding is not a collision

  // THE primary, structure-agnostic check: no two painted elements from
  // different rows may occupy the same pixels. Catches the reported
  // collision regardless of how the markup is organised.
  const p = data.painted;
  for (let i = 0; i < p.length; i++) {
    for (let j = i + 1; j < p.length; j++) {
      const a = p[i].rect, b = p[j].rect;
      const vOverlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      const hOverlap = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      if (vOverlap <= EPS || hOverlap <= EPS) continue;
      // Nested elements legitimately share space (a span inside a label).
      const nested = (p[i].path.includes(p[j].path) || p[j].path.includes(p[i].path));
      if (nested) continue;
      problems.push(`[${width}px] TEXT COLLISION: `
        + `"${p[i].text || p[i].id || p[i].tag}" overlaps `
        + `"${p[j].text || p[j].id || p[j].tag}" `
        + `(${vOverlap.toFixed(1)}px vertical, ${hOverlap.toFixed(1)}px horizontal)`);
    }
  }

  if (!data.blocks.length) problems.push('no .settings-block elements found');

  for (let i = 0; i < data.blocks.length; i++) {
    const b = data.blocks[i];
    // 1. Every note must sit INSIDE its own block's box.
    for (const n of b.notes) {
      if (n.rect.bottom > b.rect.bottom + EPS) {
        problems.push(`[${width}px] "${b.label}" status text escapes its block `
          + `(note bottom ${n.rect.bottom.toFixed(1)} > block bottom ${b.rect.bottom.toFixed(1)}): "${n.text}"`);
      }
    }
    // 2. Every note must be BELOW its own block's controls.
    for (const n of b.notes) {
      for (const r of b.rows) {
        if (n.rect.top + EPS < r.bottom && n.rect.bottom > r.top + EPS) {
          problems.push(`[${width}px] "${b.label}" status text overlaps its own controls`);
        }
      }
    }
    // 3. No block may overlap the next block. THE reported bug.
    const next = data.blocks[i + 1];
    if (next && b.rect.bottom > next.rect.top + EPS) {
      problems.push(`[${width}px] "${b.label}" (bottom ${b.rect.bottom.toFixed(1)}) `
        + `collides with "${next.label}" (top ${next.rect.top.toFixed(1)})`);
    }
    // 4. A block with zero height means it got compressed away.
    if (b.rect.height < 1) {
      problems.push(`[${width}px] "${b.label}" has no height — it was compressed`);
    }
  }
  // 5. The pane must not scroll horizontally.
  if (data.scrollWidth > data.clientWidth + 1) {
    problems.push(`[${width}px] settings pane overflows horizontally `
      + `(${data.scrollWidth} > ${data.clientWidth})`);
  }
  return problems;
}

// ---------------------------------------------------------------------------
// Morning brief view (v6.1) — same grid-cell contract as Settings.
// ---------------------------------------------------------------------------
// A full brief payload is stubbed into fetch so the view renders every
// section without a backend, then the REAL openMorningBrief() runs.

const BRIEF_PAYLOAD = {
  generated_for: '2026-08-07',
  sections: {
    obligations_due: { items: [{ id: 'obl1', name: 'WRN Monthly Support Retainer — Greg Alexander', status: 'overdue', days_overdue: 7, due_date: '2026-08-03', missed_periods: 1 }], empty: false, unavailable: false, note: '' },
    today_events: { items: [{ summary: 'Sandy discovery call', start: '2026-08-07T10:00:00', all_day: false, location: 'Zoom' }], empty: false, unavailable: false, note: '' },
    needs_reply: { items: [{ subject: 'Re: Discovery scope', last_from: 'sandy@gulf.test', days_quiet: 1, contact: { name: 'Sandy Alvarez', in_pipeline: true } }], empty: false, unavailable: false, note: '' },
    due_today: { items: [], empty: true, unavailable: false, note: 'Nothing due today — no next actions dated today or overdue.' },
    due_this_week: { items: [{ id: 'd1', contact: 'Cam Fox', title: 'Week deal', stage: 'lead', next_action: 'Prep demo', next_action_date: '2026-08-10', last_touch: '2026-08-05T09:00:00', overdue: false }], empty: false, unavailable: false, note: '' },
    stale_deals: { items: [], empty: true, unavailable: false, note: 'No active deals have gone quiet — every deal has a touch in the last 7 days.' },
    unpaid_invoices: { items: [], empty: true, unavailable: true, note: 'QuickBooks unreachable (not connected) — unpaid invoices unknown, NOT zero.' },
    awaiting_approval: { items: [{ operation_id: 'op_1', command: 'Invoice Sandy for the discovery engagement', question: 'Invoice preview — approve before anything is created?', started_at: '2026-08-07T08:00:00' }], empty: false, unavailable: false, note: '' },
  },
};

const REQUIRED_SECTIONS = ['Obligations due', 'Today’s calendar', 'Needs your reply', 'Due today',
                           'Due this week', 'Gone quiet', 'Unpaid invoices',
                           'Awaiting your approval'];

const OPEN_BRIEF = `(async () => {
  window.fetch = async (url) => {
    if (String(url).includes('/morning-brief')) {
      return { ok: true, json: async () => (${JSON.stringify(BRIEF_PAYLOAD)}) };
    }
    return { ok: false, status: 503, json: async () => ({}) };
  };
  // Drive it exactly as the operator does: click the rail button.
  const btn = document.getElementById('rail-brief-btn');
  if (!btn) return { error: 'rail-brief-btn not found' };
  btn.click();
  await new Promise((r) => setTimeout(r, 300));
  return { ok: true };
})()`;

const MEASURE_BRIEF = `(() => {
  const read = (el) => { const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right,
             height: r.height, width: r.width }; };
  const view = document.getElementById('brief-view');
  const main = document.querySelector('.operator-main');
  const rail = document.querySelector('.operator-rail');
  const composer = document.getElementById('operator-command');
  const sections = [...document.querySelectorAll('#brief-body .brief-section')];
  return {
    viewHidden: view.classList.contains('hidden'),
    viewDisplay: getComputedStyle(view).display,
    viewRect: read(view),
    mainDisplay: getComputedStyle(main).display,
    mainHidden: main.classList.contains('hidden'),
    mainRect: read(main),
    railRect: read(rail),
    composerRect: composer ? read(composer) : null,
    sectionTitles: sections.map((s) => (s.querySelector('h3') || {}).textContent || ''),
    sectionRects: sections.map((s) => read(s)),
    bodyText: (document.getElementById('brief-body').textContent || '').slice(0, 200),
  };
})()`;

function checkBrief(width, b) {
  const problems = [];
  const EPS = 0.5;
  if (b.error) { problems.push(`[${width}px] ${b.error}`); return problems; }

  // 1. The view must claim the chat pane cell, and the chat pane must yield.
  if (b.viewDisplay === 'none' || b.viewRect.height < 50) {
    problems.push(`[${width}px] brief view is not displayed (display=${b.viewDisplay}, h=${b.viewRect.height.toFixed(0)})`);
  }
  if (b.mainDisplay !== 'none') {
    problems.push(`[${width}px] .operator-main is STILL VISIBLE while the brief is open `
      + `(display=${b.mainDisplay}) — the composer will fight the view for the grid cell`);
  }
  // 2. The view must sit right of the rail, never over it.
  if (b.viewRect.left + EPS < b.railRect.right) {
    problems.push(`[${width}px] brief view (left ${b.viewRect.left.toFixed(0)}) overlaps `
      + `the rail (right ${b.railRect.right.toFixed(0)})`);
  }
  // 3. The composer must not be painted anywhere while the brief is open.
  if (b.composerRect && b.composerRect.height > 1 && b.mainDisplay !== 'none') {
    problems.push(`[${width}px] composer is still painted `
      + `(${b.composerRect.width.toFixed(0)}x${b.composerRect.height.toFixed(0)} at `
      + `left ${b.composerRect.left.toFixed(0)})`);
  }
  // 4. EVERY section must render — empty ones included, honestly noted.
  for (const want of REQUIRED_SECTIONS) {
    if (!b.sectionTitles.some((t) => t.includes(want))) {
      problems.push(`[${width}px] section missing from the view: "${want}" `
        + `(rendered: ${b.sectionTitles.join(' | ') || 'none'})`);
    }
  }
  // 5. Sections must not overlap each other.
  for (let i = 0; i + 1 < b.sectionRects.length; i++) {
    if (b.sectionRects[i].bottom > b.sectionRects[i + 1].top + EPS) {
      problems.push(`[${width}px] brief section "${b.sectionTitles[i]}" collides with `
        + `"${b.sectionTitles[i + 1]}"`);
    }
  }
  return problems;
}

// v7.5: runs INSIDE the renderer page (passed via toString). The backend is
// stubbed at window.fetch and the OS notification at window.Notification; the
// window's own job-notice poll, live view, badges and Approvals page run.
async function jobVisibilityProbe() {
  const OP = 'op_harness0001';
  const COMMAND = 'Draft a follow-up to Greg about the Navigator pilot';
  const FOLDER = 'C:/harness/run';
  const QUESTION = 'What is the Navigator pilot? I have nothing on record about it.';
  const claimed = { seq: 1, kind: 'claimed', job_id: 'j1', operation_id: OP, command: COMMAND, artifact_folder: FOLDER };
  const parked = { seq: 2, kind: 'parked', park: 'question', job_id: 'j1', operation_id: OP, command: COMMAND, question: QUESTION, artifact_folder: FOLDER };
  const events = [
    { event: 'start', data: { id: OP, command: COMMAND, artifact_folder: FOLDER, started_at: '2026-09-24T10:34:28' } },
    { event: 'needs_input', data: { id: 'need_1', question: QUESTION, options: [], context_hint: 'Follow-up to Greg' } },
    { event: 'complete', data: { id: OP, command: COMMAND, status: 'awaiting_input', awaiting_input: true, needs_input: [{ id: 'need_1', question: QUESTION }] } },
  ];
  let feed = [claimed];
  const shown = [];
  const realFetch = window.fetch;
  const realNotification = window.Notification;
  function FakeNotification(title, opts) { shown.push((opts && opts.body) || title); this.onclick = null; }
  FakeNotification.permission = 'granted';
  FakeNotification.requestPermission = () => Promise.resolve('granted');
  window.Notification = FakeNotification;
  const json = (obj) => new Response(JSON.stringify(obj), { status: 200, headers: { 'content-type': 'application/json' } });
  window.fetch = async (url) => {
    const u = String(url);
    // The stub re-sends EVERY notice on every poll: "once" must hold anyway.
    if (u.includes('/owner-workspace/jobs/notices')) return json({ epoch: 'harness', latest: feed.length, notices: feed });
    if (u.includes('/owner-workspace/jobs/events')) return json({ operation_id: OP, known: true, events, next: events.length, live: false });
    if (u.includes('/approvals/questions')) {
      return json({ count: 1, questions: [{ operation_id: OP, job_id: 'j1', command: COMMAND, question: QUESTION, artifact_folder: FOLDER, parked_at: '2026-09-24T10:34:51' }] });
    }
    if (u.endsWith('/approvals')) return json({ approvals: [], count: 0 });
    if (u.includes('/operations/recent')) return json({ operations: [] });
    return new Response('{}', { status: 404 });
  };
  const settle = (ms) => new Promise((r) => setTimeout(r, ms));
  try {
    try { localStorage.removeItem('ridian.jobNotices'); } catch (_) {}
    _jobNoticeState = RidianJobNotices.initialState();
    if (typeof closeSettings === 'function') closeSettings();
    _showWorkspaceView(null);
    await _jobsNoticesTick();
    await settle(400);
    const pinnedRow = document.querySelector('#rail-threads .rail-thread[data-op-id="' + OP + '"]');
    const railPinned = !!pinnedRow && pinnedRow.textContent.includes('From Owner Workspace');
    const echoLabel = (document.querySelector('#operator-active .operator-command-echo-label') || {}).textContent || '';
    feed = [claimed, parked];
    await _jobsNoticesTick();
    await _jobsNoticesTick();
    await _jobsNoticesTick();
    await settle(400);
    const question = document.querySelector('.operator-question');
    const waitingBadge = document.getElementById('rail-waiting-count');
    const waitingCount = (document.getElementById('rail-waiting-count') || {}).textContent || '';
    openApprovals();
    await settle(400);
    const section = document.getElementById('approvals-waiting-title');
    const item = document.querySelector('.approval-question');
    const openBtn = document.querySelector('.approval-open-run-btn');
    const result = {
      shown,
      railPinned,
      echoLabel,
      questionShown: !!question && question.textContent.includes('Navigator pilot'),
      answerArmed: !!operatorState.answerMode,
      waitingVisible: !!waitingBadge && !waitingBadge.classList.contains('hidden'),
      waitingCount,
      section: section ? section.textContent : '',
      itemText: item ? item.textContent : '',
      openFolder: openBtn ? openBtn.getAttribute('data-folder') : '',
    };
    closeApprovals();
    return result;
  } finally {
    window.fetch = realFetch;
    window.Notification = realNotification;
  }
}

// v7.6: runs INSIDE the renderer page. A run parked on a question that can
// no longer continue: answering it comes back 'expired' (never a bare error),
// the card offers "Send again", which sends the ORIGINAL command as a new
// run; the startup sweep's 'expired' notice notifies once and clears the
// waiting badge.
async function expiredRunProbe() {
  const OP = 'op_harness0002';
  const COMMAND = 'Draft a follow-up to Greg about the Navigator pilot';
  const QUESTION = 'Which Greg: Greg Ortiz or Greg Lane?';
  const FOLDER = 'C:/harness/run2';
  const NL = String.fromCharCode(10);
  const MESSAGE = "This run expired and cannot continue. Ridian Operator restarted and this run's saved state was not found. Send the command again if it is still needed.";
  const expiredNotice = { seq: 1, kind: 'expired', job_id: 'j2', operation_id: OP, command: COMMAND, message: MESSAGE, artifact_folder: FOLDER };
  let questions = [{ operation_id: OP, job_id: 'j2', command: COMMAND, question: QUESTION, artifact_folder: FOLDER, parked_at: '2026-09-24T10:34:51' }];
  let feed = [];
  const shown = [];
  const posted = [];
  const realFetch = window.fetch;
  const realNotification = window.Notification;
  function FakeNotification(title, opts) { shown.push((opts && opts.body) || title); this.onclick = null; }
  FakeNotification.permission = 'granted';
  FakeNotification.requestPermission = () => Promise.resolve('granted');
  window.Notification = FakeNotification;
  const json = (obj) => new Response(JSON.stringify(obj), { status: 200, headers: { 'content-type': 'application/json' } });
  const sse = (events) => new Response(events.map((e) => 'event: ' + e.event + NL + 'data: ' + JSON.stringify(e.data) + NL + NL).join(''),
    { status: 200, headers: { 'content-type': 'text/event-stream' } });
  window.fetch = async (url, opts) => {
    const u = String(url);
    if (u.includes('/owner-workspace/jobs/notices')) return json({ epoch: 'harness-expired', latest: feed.length, notices: feed });
    if (u.includes('/continue')) {
      questions = [];
      return sse([{ event: 'expired', data: { id: OP, command: COMMAND, status: 'failed', reason: 'state_missing', expired: true, message: MESSAGE } },
                  { event: 'end', data: {} }]);
    }
    if (u.includes('/operations/run')) {
      posted.push(JSON.parse((opts && opts.body) || '{}').command || '');
      return sse([{ event: 'end', data: {} }]);
    }
    if (u.includes('/approvals/questions')) return json({ count: questions.length, questions });
    if (u.endsWith('/approvals')) return json({ approvals: [], count: 0 });
    if (u.includes('/operations/recent')) return json({ operations: [] });
    return new Response('{}', { status: 404 });
  };
  const settle = (ms) => new Promise((r) => setTimeout(r, ms));
  try {
    // A thread parked on a question with answer mode armed — as it is after
    // a restart, when the owner reopens the run and answers.
    _showWorkspaceView(null);
    setWorkspaceView('welcome');
    _opResetUI();
    _opResetComposer();
    if (OPERATOR.active) OPERATOR.active.classList.remove('hidden');
    operatorState.active = { id: OP, command: COMMAND, artifact_folder: FOLDER };
    _opHandleEvent({ event: 'needs_input', data: { id: 'need_expired', question: QUESTION, options: [] } });
    await refreshApprovalsBadge();
    const badge = document.getElementById('rail-waiting-count');
    const out = { waitingBefore: !badge.classList.contains('hidden'), armedBefore: !!operatorState.answerMode };
    OPERATOR.command.value = 'Greg Ortiz';
    await _opSubmit();
    await settle(300);
    const card = document.querySelector('.operator-expired');
    const again = card ? card.querySelector('.operator-send-again-btn') : null;
    const errors = OPERATOR.errors && !OPERATOR.errors.classList.contains('hidden') ? OPERATOR.errors.textContent.trim() : '';
    out.card = card ? card.textContent.replace(/ +/g, ' ').trim() : '';
    out.sendAgain = again ? again.textContent : '';
    out.armedAfter = !!operatorState.answerMode;
    out.bareError = errors;
    out.waitingAfter = !badge.classList.contains('hidden');
    if (again) again.click();
    await settle(300);
    out.posted = posted;
    // The startup sweep's notice: one notification however often it is polled.
    try { localStorage.removeItem('ridian.jobNotices'); } catch (_) {}
    _jobNoticeState = RidianJobNotices.initialState();
    feed = [expiredNotice];
    await _jobsNoticesTick();
    await _jobsNoticesTick();
    await _jobsNoticesTick();
    await settle(300);
    out.shown = shown;
    return out;
  } finally {
    window.fetch = realFetch;
    window.Notification = realNotification;
  }
}

// v7.6: fills the rail like a busy day — 40 operations (two needing the
// owner, deep in the list), six projects, every badge, the longest Owner
// Workspace line — through the app's own fill functions.
async function sidebarFill() {
  const ops = [];
  for (let i = 0; i < 40; i += 1) {
    ops.push({ id: 'op_rail' + String(i).padStart(4, '0'), command: 'Recap the Tuesday call with client number ' + i + ' and draft the notes',
               status: i % 9 === 4 ? 'failed' : 'completed', artifact_folder: 'C:/harness/rail/' + i,
               completed_at: new Date(Date.now() - (i + 1) * 3600e3).toISOString(), project_id: i % 3 ? '' : 'proj_a' });
  }
  ops[17] = { ...ops[17], id: 'op_railattn17', status: 'awaiting_input', command: 'Invoice Sandy Alvarez $250 for the workshop' };
  ops[31] = { ...ops[31], id: 'op_railattn31', status: 'awaiting_input', source: 'owner-workspace', job_id: 'j31',
              command: 'Draft a follow-up to Greg about the Navigator pilot' };
  const projects = ['Gulf Coast Dental', 'Navigator pilot', 'WRN retainer', 'Internal ops', 'Marketing site', 'Bookkeeping'].map((name, i) => ({
    id: i === 0 ? 'proj_a' : 'proj_' + i, name, parent_id: '' }));
  const realFetch = window.fetch;
  const json = (obj) => new Response(JSON.stringify(obj), { status: 200, headers: { 'content-type': 'application/json' } });
  window.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/operations/recent')) return json({ operations: ops });
    if (u.includes('/operator/projects')) return json({ projects });
    return new Response('{}', { status: 404 });
  };
  try {
    try { localStorage.removeItem('ridian.activeProject'); } catch (_) {}
    _activeProjectId = '';
    _showWorkspaceView(null);
    await _railProjectsFill();
    await _railThreadsFill();
  } finally {
    window.fetch = realFetch;
  }
  _updateApprovalsBadge(3);
  _updateWaitingBadge(1);
  const obl = document.getElementById('rail-obligations-count');
  if (obl) { obl.textContent = '2'; obl.classList.remove('hidden'); }
  const ows = document.getElementById('rail-ows-status');
  if (ows) ows.textContent = 'Owner Workspace · Connected as RYAN-DESKTOP · last sync 12 min ago · up to date';
  const list = document.getElementById('rail-threads');
  if (list) list.scrollTop = 0;
  return { rows: document.querySelectorAll('#rail-threads .rail-thread').length };
}

// v7.6: what the sidebar looks like at the current window size.
function sidebarMeasure() {
  const read = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height, width: r.width }; };
  const rail = document.querySelector('.operator-rail');
  const list = document.getElementById('rail-threads');
  const ids = ['rail-new-chat', 'rail-search', 'rail-operations-btn', 'rail-approvals-btn', 'rail-obligations-btn',
               'rail-brief-btn', 'rail-projects-panel', 'rail-threads', 'operator-context-memory',
               'rail-audit-btn', 'rail-settings-btn', 'rail-ows-status'];
  const rects = {};
  ids.forEach((id) => { rects[id] = read(document.getElementById(id)); });
  const rows = [...list.querySelectorAll('.rail-thread')];
  const listBox = read(list);
  const visibleRows = rows.filter((li) => { const r = li.getBoundingClientRect();
    return r.top >= listBox.top - 0.5 && r.bottom <= listBox.bottom + 0.5; }).length;
  return {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    rail: read(rail), railScroll: rail.scrollHeight, railClient: rail.clientHeight,
    railScrollW: rail.scrollWidth, railClientW: rail.clientWidth,
    listScroll: list.scrollHeight, listClient: list.clientHeight, visibleRows,
    rects,
    firstRows: rows.slice(0, 2).map((li) => li.getAttribute('data-op-id')),
    current: [...document.querySelectorAll('.rail-nav-btn.is-current, .rail-utility-link.is-current')].map((b) => b.id),
    approvalsBadges: ['rail-approvals-count', 'rail-waiting-count'].map((id) => {
      const el = document.getElementById(id); return el && !el.classList.contains('hidden') ? el.textContent : ''; }),
  };
}

const SIDEBAR_ORDER = ['rail-new-chat', 'rail-search', 'rail-operations-btn', 'rail-approvals-btn',
  'rail-obligations-btn', 'rail-brief-btn', 'rail-projects-panel', 'rail-threads', 'rail-settings-btn', 'rail-ows-status'];

function checkSidebar(label, m) {
  const problems = [];
  const EPS = 0.5;
  if (m.railScroll > m.railClient + 1) problems.push(`[${label}] the sidebar scrolls (${m.railScroll} > ${m.railClient})`);
  if (m.railScrollW > m.railClientW + 1) problems.push(`[${label}] the sidebar overflows sideways (${m.railScrollW} > ${m.railClientW})`);
  for (const [id, r] of Object.entries(m.rects)) {
    if (!r || r.width < 1 || r.height < 1) { problems.push(`[${label}] ${id} is not on screen`); continue; }
    if (r.top < m.rail.top - EPS || r.bottom > m.rail.bottom + EPS || r.bottom > m.viewport.h + EPS) {
      problems.push(`[${label}] ${id} is cut off (top ${r.top.toFixed(0)}, bottom ${r.bottom.toFixed(0)}, rail ${m.rail.top.toFixed(0)}-${m.rail.bottom.toFixed(0)})`);
    }
    if (r.right > m.rail.right + EPS) problems.push(`[${label}] ${id} runs past the sidebar's right edge`);
  }
  for (let i = 0; i + 1 < SIDEBAR_ORDER.length; i += 1) {
    const a = m.rects[SIDEBAR_ORDER[i]], b = m.rects[SIDEBAR_ORDER[i + 1]];
    if (a && b && a.bottom > b.top + EPS) problems.push(`[${label}] ${SIDEBAR_ORDER[i]} overlaps or follows ${SIDEBAR_ORDER[i + 1]}`);
  }
  const util = ['operator-context-memory', 'rail-audit-btn', 'rail-settings-btn'].map((id) => m.rects[id]);
  if (util.every(Boolean) && Math.max(...util.map((r) => r.top)) - Math.min(...util.map((r) => r.top)) > 1) {
    problems.push(`[${label}] the utility links are not one compact row`);
  }
  const list = m.rects['rail-threads'];
  const foot = m.rects['operator-context-memory'];
  if (list && foot && foot.top - list.bottom > 24) problems.push(`[${label}] the Operations list does not take the remaining height (gap ${(foot.top - list.bottom).toFixed(0)}px)`);
  if (m.visibleRows < 3) problems.push(`[${label}] only ${m.visibleRows} operations are visible`);
  if (m.listScroll <= m.listClient) problems.push(`[${label}] the Operations list does not scroll on its own`);
  if (m.firstRows.join() !== 'op_railattn17,op_railattn31') problems.push(`[${label}] needs-attention rows are not first: ${m.firstRows}`);
  if (m.current.join() !== 'rail-operations-btn') problems.push(`[${label}] current nav item is ${m.current}`);
  if (m.approvalsBadges.join() !== '3,1') problems.push(`[${label}] Approvals badges are ${m.approvalsBadges}`);
  return problems;
}

app.whenReady().then(async () => {
  const allProblems = [];
  await openOnce();

  // BRIEF FIRST, from the app's pristine state — the operator's real path
  // (click the rail button), before any other view has touched the DOM.
  console.log('--- Morning brief view (opened by clicking the rail button) ---');
  const opened = await win.webContents.executeJavaScript(OPEN_BRIEF, true);
  if (opened && opened.error) allProblems.push(opened.error);
  for (const width of WIDTHS) {
    win.setContentSize(width, HEIGHT);
    await new Promise((r) => setTimeout(r, 250));
    const b = await win.webContents.executeJavaScript(MEASURE_BRIEF, true);
    const problems = checkBrief(width, b);
    console.log(`${width}px: ${b.sectionTitles.length} sections | `
      + `view ${b.viewRect.width.toFixed(0)}x${b.viewRect.height.toFixed(0)} @left ${b.viewRect.left.toFixed(0)} | `
      + `main display=${b.mainDisplay}`);
    allProblems.push(...problems);
  }

  // View SWITCHING — the sequence that produced the reported bug: open the
  // Brief, open Settings over it, then close Settings. Before the view
  // manager this left the Brief open AND the chat pane restored, so the
  // composer auto-placed into an implicit grid row (240px wide, under the
  // rail) and stole height from the Brief.
  console.log('\n--- View switching (brief -> settings -> close) ---');
  win.setContentSize(WIDTHS[0], HEIGHT);
  await new Promise((r) => setTimeout(r, 200));
  const seq = await win.webContents.executeJavaScript(`(async () => {
    const snap = () => {
      const main = document.querySelector('.operator-main');
      const rail = document.querySelector('.operator-rail');
      const m = main.getBoundingClientRect(), r = rail.getBoundingClientRect();
      return {
        visible: ['brief-view','settings-view','approvals-view','audit-view']
          .filter((id) => !document.getElementById(id).classList.contains('hidden')),
        mainDisplay: getComputedStyle(main).display,
        mainWidth: Math.round(m.width), mainLeft: Math.round(m.left),
        railRight: Math.round(r.right), railBottom: Math.round(r.bottom),
      };
    };
    const steps = {};
    document.getElementById('rail-brief-btn').click();
    await new Promise((r) => setTimeout(r, 200)); steps.afterBrief = snap();
    document.getElementById('rail-settings-btn').click();
    await new Promise((r) => setTimeout(r, 200)); steps.afterSettings = snap();
    document.getElementById('settings-close-btn').click();
    await new Promise((r) => setTimeout(r, 200)); steps.afterClose = snap();
    return steps;
  })()`, true);
  for (const [name, s] of Object.entries(seq)) {
    console.log(`  ${name}: views=[${s.visible}] main=${s.mainDisplay}`
      + `${s.mainDisplay !== 'none' ? ` ${s.mainWidth}px @left ${s.mainLeft}` : ''}`);
    if (s.visible.length > 1) {
      allProblems.push(`view switching (${name}): ${s.visible.length} views visible `
        + `at once [${s.visible}] — they stack in the same grid cell`);
    }
    if (name !== 'afterClose' && s.mainDisplay !== 'none') {
      allProblems.push(`view switching (${name}): chat pane visible while a view is open`);
    }
    if (name === 'afterClose') {
      if (s.visible.length) {
        allProblems.push(`view switching (afterClose): "${s.visible}" still open after `
          + 'closing Settings');
      }
      if (s.mainDisplay !== 'none' && s.mainWidth < 400) {
        allProblems.push(`view switching (afterClose): chat pane collapsed to `
          + `${s.mainWidth}px at left ${s.mainLeft} — auto-placed outside its cell`);
      }
    }
  }

  // --- v6.9.5: navigation from INSIDE views ------------------------------
  // The reported bug: "New chat" inside Settings did nothing visible. Any
  // rail navigation must land where the user clicked, from any view, and
  // unsaved settings edits must be guarded by a confirm, not discarded.
  console.log('\n--- Nav from inside views ---');
  const nav = await win.webContents.executeJavaScript(`(async () => {
    const wait = () => new Promise((r) => setTimeout(r, 150));
    const state = () => {
      const views = ['settings-view','brief-view','approvals-view','audit-view','obligations-view']
        .filter((id) => !document.getElementById(id).classList.contains('hidden'));
      const main = getComputedStyle(document.querySelector('.operator-main')).display;
      return { views, main };
    };
    const out = {};
    // 1. New chat from inside Settings goes to the chat pane.
    document.getElementById('rail-settings-btn').click(); await wait();
    document.getElementById('rail-new-chat').click(); await wait();
    out.newChatFromSettings = state();
    // 2. Dirty settings + refuse the confirm -> navigation is refused.
    document.getElementById('rail-settings-btn').click(); await wait();
    const nameField = document.getElementById('settings-form').elements.namedItem('operator_name');
    nameField.value = 'edited-but-not-saved';
    nameField.dispatchEvent(new Event('input', { bubbles: true }));
    const realConfirm = window.confirm;
    let confirmAsked = 0;
    window.confirm = () => { confirmAsked += 1; return false; };
    document.getElementById('rail-new-chat').click(); await wait();
    out.dirtyRefused = { views: state().views, main: state().main, confirmAsked };
    // 3. Same edit, accept the confirm -> navigation proceeds.
    window.confirm = () => { confirmAsked += 1; return true; };
    document.getElementById('rail-new-chat').click(); await wait();
    out.dirtyConfirmed = { views: state().views, main: state().main, confirmAsked };
    window.confirm = realConfirm;
    // 4. Every view reachable from inside every other (5x4 ordered pairs).
    const RAIL = { 'settings-view': 'rail-settings-btn', 'brief-view': 'rail-brief-btn',
                   'approvals-view': 'rail-approvals-btn', 'audit-view': 'rail-audit-btn',
                   'obligations-view': 'rail-obligations-btn' };
    let pairsOk = 0, pairsTried = 0; const pairFails = [];
    for (const from of Object.keys(RAIL)) {
      for (const to of Object.keys(RAIL)) {
        if (from === to) continue;
        pairsTried += 1;
        document.getElementById(RAIL[from]).click(); await wait();
        document.getElementById(RAIL[to]).click(); await wait();
        const s = state();
        if (s.views.length === 1 && s.views[0] === to && s.main === 'none') pairsOk += 1;
        else pairFails.push(from + '->' + to + ':' + JSON.stringify(s));
      }
    }
    out.crossNav = { pairsOk, pairsTried, pairFails };
    if (typeof closeSettings === 'function') closeSettings();
    return out;
  })()`, true);
  console.log(`  newChatFromSettings: views=[${nav.newChatFromSettings.views}] main=${nav.newChatFromSettings.main}`);
  console.log(`  dirtyRefused: views=[${nav.dirtyRefused.views}] confirmAsked=${nav.dirtyRefused.confirmAsked}`);
  console.log(`  dirtyConfirmed: views=[${nav.dirtyConfirmed.views}] main=${nav.dirtyConfirmed.main}`);
  console.log(`  crossNav: ${nav.crossNav.pairsOk}/${nav.crossNav.pairsTried} pairs ok`);
  if (nav.newChatFromSettings.views.length || nav.newChatFromSettings.main === 'none') {
    allProblems.push('nav: New chat from Settings did not reach the chat pane');
  }
  if (nav.dirtyRefused.views.join() !== 'settings-view' || nav.dirtyRefused.confirmAsked !== 1) {
    allProblems.push('nav: dirty-settings refusal did not hold the view');
  }
  if (nav.dirtyConfirmed.views.length || nav.dirtyConfirmed.main === 'none') {
    allProblems.push('nav: dirty-settings confirm did not release the view');
  }
  if (nav.crossNav.pairsOk !== nav.crossNav.pairsTried) {
    allProblems.push('nav: cross-view pairs failed: ' + nav.crossNav.pairFails.join('; '));
  }

  console.log('\n--- Settings view ---');
  await win.webContents.executeJavaScript(
    '(() => { if (typeof closeSettings === "function") closeSettings(); })()', true);
  for (const width of WIDTHS) {
    const data = await measureAt(width);
    const problems = check(width, data);
    const heights = data.blocks
      .map((b) => `${b.label.trim() || '?'}=${b.rect.height.toFixed(0)}px`).join(' ');
    console.log(`${width}px: ${data.blocks.length} blocks | ${heights}`);
    allProblems.push(...problems);
  }

  // --- v7.4: Ridian's reply as sanitized Markdown, in the REAL renderer DOM.
  // The hostile reply goes through the app's own _opRenderReceipt into the
  // real #operator-receipt-text; nothing in it may become an element.
  console.log('');
  console.log('--- Reply markdown (real DOM) ---');
  const replyLines = [
    '**Today:** 3 meetings', '', '- **Invoice** Sandy Alvarez', '- Follow up', '',
    '<script>window.__pwned = 1</script>', '', '<img src=x onerror="window.__pwned = 2">', '',
    '![tracker](https://evil.example/pixel.png)', '', '[click me](javascript:window.__pwned=3)',
  ];
  const reply = await win.webContents.executeJavaScript(`(() => {
    const text = ${JSON.stringify(replyLines)}.join(String.fromCharCode(10));
    _opRenderReceipt(text);
    const el = document.getElementById('operator-receipt-text');
    const all = [...el.querySelectorAll('*')];
    return {
      loaded: typeof window.RidianMarkdown === 'object',
      tags: [...new Set(all.map((e) => e.tagName.toLowerCase()))].sort(),
      attrs: [...new Set(all.flatMap((e) => [...e.attributes].map((a) => a.name)))],
      dangerous: el.querySelectorAll('script, img, a, iframe, b, style, object, embed').length,
      literalStars: el.textContent.includes('**'),
      strong: el.querySelectorAll('strong').length,
      items: el.querySelectorAll('li').length,
      scriptShownAsText: el.textContent.includes('<script>window.__pwned = 1</script>'),
      pwned: window.__pwned || 0,
    };
  })()`, true);
  console.log(`  tags=${reply.tags.join(',')} strong=${reply.strong} items=${reply.items} dangerous=${reply.dangerous} pwned=${reply.pwned}`);
  const allowedTags = new Set(['h4', 'h5', 'h6', 'p', 'br', 'ul', 'ol', 'li', 'strong']);
  if (!reply.loaded) allProblems.push('markdown: window.RidianMarkdown is not loaded');
  if (reply.dangerous || reply.pwned) allProblems.push(`markdown: hostile reply was not inert (dangerous=${reply.dangerous} pwned=${reply.pwned})`);
  if (reply.tags.some((t) => !allowedTags.has(t))) allProblems.push('markdown: unexpected element ' + reply.tags.join(','));
  if (reply.attrs.some((a) => a !== 'start')) allProblems.push('markdown: unexpected attribute ' + reply.attrs.join(','));
  if (reply.literalStars || reply.strong < 2 || reply.items !== 2) allProblems.push('markdown: bold/lists did not render');
  if (!reply.scriptShownAsText) allProblems.push('markdown: the script tag was not shown as text');
  if (reply.loaded && !reply.dangerous && !reply.pwned) console.log('  markdown inert: no script, img or link element; tags shown as text');

  // --- v7.5: an Owner Workspace job run is visible, in the REAL renderer.
  console.log('');
  console.log('--- Job visibility (real DOM) ---');
  const jobs = await win.webContents.executeJavaScript('(' + jobVisibilityProbe.toString() + ')()', true);
  console.log(`  notifications=${jobs.shown.length} pinned=${jobs.railPinned} echo=${jobs.echoLabel} question=${jobs.questionShown} armed=${jobs.answerArmed} waiting=${jobs.waitingVisible}:${jobs.waitingCount} section=${jobs.section}`);
  const expectShown = ['Ridian is working on: Draft a follow-up to Greg about the Navigator pilot',
    'Ridian needs you: Draft a follow-up to Greg about the Navigator pilot'];
  if (JSON.stringify(jobs.shown) !== JSON.stringify(expectShown)) allProblems.push('jobs: notifications were ' + JSON.stringify(jobs.shown));
  if (!jobs.railPinned) allProblems.push('jobs: the claimed run was not pinned in the Operations list');
  if (jobs.echoLabel !== 'From Owner Workspace') allProblems.push('jobs: the live view did not open (echo=' + jobs.echoLabel + ')');
  if (!jobs.questionShown || !jobs.answerArmed) allProblems.push('jobs: the parked question was not shown and answerable');
  if (!jobs.waitingVisible || jobs.waitingCount !== '1') allProblems.push('jobs: no "Waiting on you" badge');
  if (jobs.section !== 'Waiting for your answer' || !jobs.itemText.includes('Navigator pilot') || jobs.openFolder !== 'C:/harness/run') {
    allProblems.push('jobs: the Approvals page did not list the parked question with an Open-the-run button');
  }
  if (!allProblems.some((p) => p.startsWith('jobs:'))) console.log('  job runs visible: notified once each, pinned, opened live, badged, listed under Waiting for your answer');

  // --- v7.6: a parked run that cannot continue, in the REAL renderer.
  console.log('');
  console.log('--- Expired run (real DOM) ---');
  const exp = await win.webContents.executeJavaScript('(' + expiredRunProbe.toString() + ')()', true);
  console.log(`  armed=${exp.armedBefore}->${exp.armedAfter} waiting=${exp.waitingBefore}->${exp.waitingAfter} card=${exp.card.startsWith('This run expired')} sendAgain=${exp.sendAgain} bareError=${exp.bareError ? 'yes' : 'no'} posted=${exp.posted.length} notifications=${exp.shown.length}`);
  const expCommand = 'Draft a follow-up to Greg about the Navigator pilot';
  if (!exp.armedBefore || exp.armedAfter) allProblems.push('expired: answer mode did not disarm');
  if (!exp.card.startsWith('This run expired') || !exp.card.includes('saved state was not found') || exp.sendAgain !== 'Send again') {
    allProblems.push('expired: no expired card with Send again: ' + exp.card);
  }
  if (exp.bareError) allProblems.push('expired: a bare error was shown: ' + exp.bareError);
  if (!exp.waitingBefore || exp.waitingAfter) allProblems.push('expired: the waiting badge did not clear');
  if (JSON.stringify(exp.posted) !== JSON.stringify([expCommand])) allProblems.push('expired: Send again posted ' + JSON.stringify(exp.posted));
  if (JSON.stringify(exp.shown) !== JSON.stringify(["Ridian couldn't continue: " + expCommand])) allProblems.push('expired: notifications were ' + JSON.stringify(exp.shown));
  if (!allProblems.some((p) => p.startsWith('expired:'))) console.log('  expired run: said so, Send again re-sent the command, notified once, badge cleared');

  // --- v7.6: the sidebar at every width, and at 1024x700.
  console.log('');
  console.log('--- Sidebar ---');
  const filled = await win.webContents.executeJavaScript('(' + sidebarFill.toString() + ')()', true);
  const sizes = WIDTHS.map((w) => ({ label: `${w}px`, set: () => win.setContentSize(w, HEIGHT) }));
  sizes.push({ label: '1024x700 content', set: () => win.setContentSize(1024, 700) });
  sizes.push({ label: '1024x700 window', set: () => win.setSize(1024, 700) });
  for (const size of sizes) {
    size.set();
    // A healthy app: no "Backend is not running" banner (there is no backend
    // here), and the app's own 60 s badge refresh must not race the check.
    await win.webContents.executeJavaScript(`(() => { _updateApprovalsBadge(3); _updateWaitingBadge(1);
      const o = document.getElementById('rail-obligations-count'); o.textContent = '2'; o.classList.remove('hidden');
      document.getElementById('backend-down-banner').classList.add('hidden'); })()`, true);
    await new Promise((r) => setTimeout(r, 250));
    const m = await win.webContents.executeJavaScript('(' + sidebarMeasure.toString() + ')()', true);
    const problems = checkSidebar(size.label, m);
    console.log(`${size.label}: viewport ${m.viewport.w}x${m.viewport.h} | rail ${m.rail.width.toFixed(0)}x${m.rail.height.toFixed(0)} `
      + `scrolls=${m.railScroll > m.railClient + 1 ? 'yes' : 'no'} | list ${m.listClient}px, ${m.visibleRows} rows visible of ${filled.rows} `
      + `| attention first=${m.firstRows.join() === 'op_railattn17,op_railattn31' ? 'yes' : 'no'} | ${problems.length ? 'PROBLEMS' : 'ok'}`);
    allProblems.push(...problems);
  }
  // Degraded: the backend-down banner takes ~110px above the rail at the
  // smallest size. The list gives way; the sidebar itself still never scrolls.
  const degraded = await win.webContents.executeJavaScript(`(async () => {
    document.getElementById('backend-down-banner').classList.remove('hidden');
    await new Promise((r) => setTimeout(r, 150));
    const rail = document.querySelector('.operator-rail');
    const box = rail.getBoundingClientRect();
    const visible = ['rail-new-chat', 'rail-brief-btn', 'rail-settings-btn', 'rail-ows-status'].every((id) => {
      const r = document.getElementById(id).getBoundingClientRect();
      return r.height > 0 && r.top >= box.top - 0.5 && r.bottom <= box.bottom + 0.5; });
    const out = { h: Math.round(box.height), scrolls: rail.scrollHeight > rail.clientHeight + 1, visible };
    document.getElementById('backend-down-banner').classList.add('hidden');
    return out;
  })()`, true);
  console.log(`  backend-down banner at 1024x700 window: rail ${degraded.h}px, scrolls=${degraded.scrolls ? 'yes' : 'no'}, nav and footer visible=${degraded.visible}`);
  if (degraded.scrolls || !degraded.visible) allProblems.push('sidebar: with the backend-down banner the sidebar scrolls or loses its nav/footer');
  // Still true with the project filter open, at the smallest size.
  const panelOpen = await win.webContents.executeJavaScript(`(() => {
    document.getElementById('rail-projects-panel').open = true;
    const rail = document.querySelector('.operator-rail');
    const foot = document.getElementById('rail-ows-status').getBoundingClientRect();
    const rows = document.querySelectorAll('#rail-projects .rail-thread').length;
    const toggle = document.getElementById('rail-hide-terminal').getBoundingClientRect();
    const out = { scrolls: rail.scrollHeight > rail.clientHeight + 1, footVisible: foot.bottom <= rail.getBoundingClientRect().bottom + 0.5, rows,
                  toggle: toggle.height > 0 };
    document.getElementById('rail-projects-panel').open = false;
    return out;
  })()`, true);
  console.log(`  project filter open at 1024x700 window: ${panelOpen.rows} project rows, hide-failed toggle=${panelOpen.toggle}, sidebar scrolls=${panelOpen.scrolls ? 'yes' : 'no'}, footer visible=${panelOpen.footVisible}`);
  if (panelOpen.scrolls || !panelOpen.footVisible || panelOpen.rows !== 7 || !panelOpen.toggle) allProblems.push('sidebar: the open project filter pushed the sidebar: ' + JSON.stringify(panelOpen));
  // The Operations nav item brings the chat pane back from any view.
  const back = await win.webContents.executeJavaScript(`(async () => {
    const wait = () => new Promise((r) => setTimeout(r, 150));
    document.getElementById('rail-approvals-btn').click(); await wait();
    const inView = [...document.querySelectorAll('.rail-nav-btn.is-current')].map((b) => b.id).join();
    document.getElementById('rail-operations-btn').click(); await wait();
    return { inView, main: getComputedStyle(document.querySelector('.operator-main')).display,
             current: [...document.querySelectorAll('.rail-nav-btn.is-current')].map((b) => b.id).join() };
  })()`, true);
  console.log(`  nav: approvals current=${back.inView}; Operations -> main=${back.main} current=${back.current}`);
  if (back.inView !== 'rail-approvals-btn' || back.main === 'none' || back.current !== 'rail-operations-btn') {
    allProblems.push('sidebar: the Operations nav item did not return to the chat pane: ' + JSON.stringify(back));
  }
  win.setContentSize(WIDTHS[0], HEIGHT);

  if (allProblems.length) {
    console.log('\nLAYOUT PROBLEMS:');
    allProblems.forEach((p) => console.log('  - ' + p));
    app.exit(1);
  } else {
    console.log('\nLAYOUT OK — settings blocks self-contained; brief claims the '
      + 'chat pane cell with every section present.');
    app.exit(0);
  }
}).catch((err) => { console.error(err); app.exit(1); });
