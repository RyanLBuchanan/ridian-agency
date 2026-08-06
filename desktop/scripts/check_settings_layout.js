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

app.whenReady().then(async () => {
  const allProblems = [];
  await openOnce();
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
    console.log('\nLAYOUT OK — no block collides, every status line is inside its own block.');
    app.exit(0);
  }
}).catch((err) => { console.error(err); app.exit(1); });
