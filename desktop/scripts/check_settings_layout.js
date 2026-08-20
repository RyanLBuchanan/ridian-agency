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
};

const MEASURE = `(() => {
  const view = document.getElementById('settings-view');
  const main = document.querySelector('.operator-main');
  if (main) main.classList.add('hidden');
  view.classList.remove('hidden');
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
