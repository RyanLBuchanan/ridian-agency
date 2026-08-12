// Rail cold-start harness (v6.1) — loading is not empty, unknown is not zero.
//
// Boots the REAL renderer against a real, controllable HTTP backend on a
// scratch port and asserts the three states the rail must distinguish:
//
//   1. PENDING   — the server accepts connections but never answers (a
//                  backend still booting). The rail must show its loading
//                  state and NEVER "No chats yet." / "empty — click to add".
//   2. ARRIVAL   — the held responses are then released with real data; the
//                  rail must refresh to the rows WITHOUT any command running
//                  (the reported bug: it never refreshed on arrival).
//   3. EMPTY     — a fresh window against a backend that CONFIRMS zero chats
//                  / zero contacts must show the honest empty states.
//   4. DATA      — a fresh window against a backend with data must render
//                  the rows on cold start, no command needed.
//
// Run:  npx electron scripts/check_rail_coldstart.js   (exit 0 = OK)
// Pinned by apps/api/tests/test_rail_coldstart.py.

const { app, BrowserWindow } = require('electron');
const http = require('node:http');
const path = require('node:path');

const PORT = 8767;                         // scratch — never the real 8000
process.env.RIDIAN_PORT = String(PORT);    // preload derives backendOrigin

const OPS = { operations: [
  { id: 'op_1', command: 'Invoice Sandy for the discovery engagement',
    status: 'completed', completed_at: '2026-08-09T10:00:00', artifact_folder: 'x',
    project_id: '' },
  { id: 'op_2', command: 'Morning brief', status: 'completed',
    completed_at: '2026-08-10T08:00:00', artifact_folder: 'y', project_id: '' },
] };
const EMPTY_OPS = { operations: [] };
const PROJECTS = { projects: [{ id: 'p1', name: 'Gulf Realty', parent_id: '' }] };
const EMPTY_PROJECTS = { projects: [] };
const MEMORY = { contacts: 3, facts: 2, open_follow_ups: 1 };
const EMPTY_MEMORY = { contacts: 0, facts: 0, open_follow_ups: 0 };

let mode = 'hold';                 // 'hold' | 'empty' | 'data'
const held = [];                   // responses parked while mode === 'hold'

function payloadFor(url, dataMode) {
  if (url.startsWith('/operations/recent')) return dataMode ? OPS : EMPTY_OPS;
  if (url.startsWith('/operator/projects')) return dataMode ? PROJECTS : EMPTY_PROJECTS;
  if (url.startsWith('/memory/summary')) return dataMode ? MEMORY : EMPTY_MEMORY;
  if (url.startsWith('/approvals')) return { approvals: [], count: 0 };
  return {};
}

const server = http.createServer((req, res) => {
  if (mode === 'hold') { held.push({ url: req.url, res }); return; }
  res.setHeader('content-type', 'application/json');
  res.setHeader('access-control-allow-origin', '*');
  res.end(JSON.stringify(payloadFor(req.url, mode === 'data')));
});

function releaseHeld() {
  for (const { url, res } of held.splice(0)) {
    res.setHeader('content-type', 'application/json');
    res.setHeader('access-control-allow-origin', '*');
    res.end(JSON.stringify(payloadFor(url, true)));
  }
}

const SNAP = `(() => ({
  threads: document.getElementById('rail-threads').textContent.trim(),
  projects: document.getElementById('rail-projects').textContent.trim(),
  threadRows: document.querySelectorAll('#rail-threads .rail-thread').length,
  memory: (document.getElementById('operator-context-memory-value') || {}).textContent || '',
}))()`;

// One window, reloaded per scenario — recreating BrowserWindows back to
// back flakes with ERR_FAILED in this environment, and a reload is the
// same cold boot for the renderer script.
let win = null;

async function freshWindow() {
  if (!win) {
    win = new BrowserWindow({
      width: 1280, height: 860, show: false,
      webPreferences: {
        preload: path.join(__dirname, '..', 'preload.js'),
        contextIsolation: true, nodeIntegration: false, sandbox: true,
      },
    });
    await win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
  } else {
    const done = new Promise((r) => win.webContents.once('did-finish-load', r));
    win.webContents.reload();
    await done;
  }
  return win;
}

const problems = [];
function expect(cond, msg) { if (!cond) problems.push(msg); }

app.whenReady().then(async () => {
  await new Promise((r) => server.listen(PORT, '127.0.0.1', r));

  // --- 1. PENDING: backend accepted the connection but has not answered ---
  mode = 'hold';
  await freshWindow();
  await new Promise((r) => setTimeout(r, 2000));   // well past first paint
  let s = await win.webContents.executeJavaScript(SNAP, true);
  console.log(`pending : threads="${s.threads}" projects="${s.projects}" memory="${s.memory}"`);
  expect(s.threads.includes('Loading'), `pending: threads shows "${s.threads}" not a loading state`);
  expect(!s.threads.includes('No chats yet'), 'pending: threads claims "No chats yet" with NO backend answer');
  expect(!s.projects.includes('No projects yet'), 'pending: projects claims empty with NO backend answer');
  expect(s.memory.includes('Loading'), `pending: memory chip shows "${s.memory}" not a loading state`);
  expect(!s.memory.includes('empty'), 'pending: memory chip claims empty with NO backend answer');

  // --- 2. ARRIVAL: the same window, the held responses now arrive ---------
  mode = 'data';
  releaseHeld();
  await new Promise((r) => setTimeout(r, 1500));
  s = await win.webContents.executeJavaScript(SNAP, true);
  console.log(`arrival : rows=${s.threadRows} memory="${s.memory}"`);
  expect(s.threadRows === 2, `arrival: expected 2 chat rows after the response arrived, got ${s.threadRows}`);
  expect(s.memory.includes('3 contacts'), `arrival: memory chip never refreshed ("${s.memory}")`);
  // window is reused; reload happens in freshWindow()

  // --- 3. CONFIRMED EMPTY: backend answers "zero" — honest empty state ----
  mode = 'empty';
  await freshWindow();
  await new Promise((r) => setTimeout(r, 1500));
  s = await win.webContents.executeJavaScript(SNAP, true);
  console.log(`empty   : threads="${s.threads}" memory="${s.memory}"`);
  expect(s.threads.includes('No chats yet'), `empty: confirmed-zero backend must show the empty state, got "${s.threads}"`);
  expect(s.memory.includes('empty'), `empty: memory chip should say empty, got "${s.memory}"`);
  // window is reused; reload happens in freshWindow()

  // --- 4. DATA on a cold start: rows render with no command run -----------
  mode = 'data';
  await freshWindow();
  await new Promise((r) => setTimeout(r, 1500));
  s = await win.webContents.executeJavaScript(SNAP, true);
  console.log(`data    : rows=${s.threadRows} projects="${s.projects.slice(0, 40)}" memory="${s.memory}"`);
  expect(s.threadRows === 2, `data: expected 2 chat rows on cold start, got ${s.threadRows}`);
  expect(s.threads.includes('Invoice Sandy'), 'data: chat command text missing from the rail');
  expect(s.projects.includes('Gulf Realty'), 'data: project missing from the rail');
  expect(s.memory.includes('3 contacts') && s.memory.includes('2 facts'),
         `data: memory chip should show real counts, got "${s.memory}"`);
  // window is reused; reload happens in freshWindow()

  server.close();
  if (problems.length) {
    console.log('\nRAIL PROBLEMS:');
    problems.forEach((p) => console.log('  - ' + p));
    app.exit(1);
  } else {
    console.log('\nRAIL OK — loading until the backend answers, honest empty only when confirmed, rows on arrival.');
    app.exit(0);
  }
}).catch((err) => { console.error(err); app.exit(1); });
