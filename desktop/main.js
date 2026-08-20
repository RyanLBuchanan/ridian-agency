// Ridian Agency — Electron main process.
// Creates a single BrowserWindow that loads renderer/index.html locally.
// The renderer talks to the existing FastAPI backend at http://127.0.0.1:8000
// over plain fetch — no IPC needed.

const { app, BrowserWindow, Menu, dialog, shell, session,
        globalShortcut, ipcMain } = require('electron');
const { spawn, execFile } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { registerGlobalHotkey, applyHotkey, DEFAULT_ACCELERATOR } = require('./hotkey');

// v4.4 state-guard: RIDIAN_PORT lets a sandboxed harness run the whole app
// (supervisor, CSP, renderer origin) on a scratch port so its probes can
// never collide with a real instance on 8000. Unset = 8000, unchanged.
const BACKEND_PORT = parseInt(process.env.RIDIAN_PORT || '8000', 10);
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;

// v4.4.1 leaked-sandbox preflight. Harnesses run the app with ALL THREE of
// RIDIAN_SANDBOX + RIDIAN_DATA_DIR + RIDIAN_PORT (per-child env only —
// scripts/Run-Sandboxed-Backend.ps1 is the paved road). If the sandbox flag
// reaches a REAL launch (a terminal or Explorer session still holding
// leaked harness vars), the backend would refuse with a cryptic crash;
// catch it HERE with an actionable message instead. A complete sandbox
// config passes through untouched.
function sandboxEnvPreflight() {
  if (!process.env.RIDIAN_SANDBOX) return true;
  const missing = [];
  if (!process.env.RIDIAN_DATA_DIR) missing.push('RIDIAN_DATA_DIR');
  if (!process.env.RIDIAN_PORT || BACKEND_PORT === 8000) missing.push('RIDIAN_PORT (non-8000)');
  if (missing.length === 0) return true;
  dialog.showErrorBox(
    'Ridian Operator — leaked sandbox environment',
    'RIDIAN_SANDBOX is set in this launch environment, but the sandbox config is '
    + `incomplete (missing ${missing.join(', ')}).\n\n`
    + 'This usually means test-harness variables leaked into the session you are '
    + 'launching from. Clear them there and relaunch:\n\n'
    + 'PowerShell:\n'
    + '  Remove-Item Env:RIDIAN_SANDBOX, Env:RIDIAN_DATA_DIR, Env:RIDIAN_PORT -ErrorAction SilentlyContinue\n\n'
    + 'cmd:\n'
    + '  set RIDIAN_SANDBOX= & set RIDIAN_DATA_DIR= & set RIDIAN_PORT=\n\n'
    + 'Or simply launch Ridian Operator from the Start menu after signing out and '
    + 'back in (nothing is set persistently).');
  return false;
}

/* v4.1 packaged mode: main.js is the backend SUPERVISOR — it spawns uvicorn
   as a hidden background process (windowsHide: no console window ever
   exists) and kills the process tree on quit. Dev mode (npm start / the
   .bat) is untouched: the backend is started externally and this block
   no-ops because the port is already serving. */
let _backendChild = null;

function _portInUse(port) {
  return new Promise((resolve) => {
    const sock = net.connect({ port, host: '127.0.0.1' });
    sock.once('connect', () => { sock.destroy(); resolve(true); });
    sock.once('error', () => resolve(false));
    setTimeout(() => { try { sock.destroy(); } catch (_) {} resolve(false); }, 1500);
  });
}

// v4.7: the state home THIS app instance expects its backend to use.
function _expectedDataDir() {
  if (process.env.RIDIAN_SANDBOX && process.env.RIDIAN_DATA_DIR) {
    return process.env.RIDIAN_DATA_DIR;
  }
  // Dev (.bat flow): the historical writable base is the repo's apps/api.
  if (!app.isPackaged) return path.join(__dirname, '..', 'apps', 'api');
  return path.join(process.env.APPDATA || '', 'Ridian Operator');
}

// v4.7 identity handshake: ask the process on our port WHO it is. Returns
// true only for a Ridian backend using OUR expected data dir. Anything
// else — a sandbox orphan, another profile, a non-Ridian service, or a
// backend too old to identify itself — is a stranger.
async function _backendIdentityMatches() {
  // The abort deadline covers headers AND body: a stranger that sends 200
  // then stalls the body must not hang startup past the 3s budget (found
  // by adversarial review — clearing the timeout before res.json() left a
  // silent up-to-5-minute hang exactly for the not-Ridian-at-all case).
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 3000);
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/health`, { signal: ctl.signal });
    if (!res.ok) return false;
    const j = await res.json();
    if (j.service !== 'ridian-agency' || typeof j.data_dir !== 'string') return false;
    const norm = (p) => path.resolve(p).toLowerCase();
    return norm(j.data_dir) === norm(_expectedDataDir());
  } catch (_) {
    return false;
  } finally {
    clearTimeout(t);
  }
}

async function startBackendIfNeeded() {
  // Three-way spawn (v4.2):
  //   dev            -> no-op here; uvicorn runs from the venv exactly as
  //                     always (npm start / the .bat).
  //   packaged       -> spawn the BUNDLED self-contained backend
  //                     (resources/backend/ridian-backend.exe, PyInstaller
  //                     onedir) — no Python/venv/repo on the target machine.
  //   port already serving -> adopt ONLY after the identity handshake
  //                     (v4.7). "Reuse whatever answers the port" let the
  //                     real app adopt orphaned sandbox backends and render
  //                     their empty state as if the operator's settings,
  //                     chats, and memory had been wiped (4x, 2026-07-27).
  // Returns false when startup must abort (stranger on the port).
  // The handshake runs in DEV too (expected home: the repo's apps/api) —
  // the dev .bat flow was just as exposed to orphan adoption.
  if (await _portInUse(BACKEND_PORT)) {
    if (await _backendIdentityMatches()) return true;
    dialog.showErrorBox(
      'Ridian Operator — another backend owns port ' + BACKEND_PORT,
      'Something is already serving on port ' + BACKEND_PORT + ', but it is not '
      + 'THIS app\'s backend (wrong state directory, or not Ridian at all — '
      + 'often a leftover test/sandbox backend).\n\n'
      + 'Ridian Operator will not adopt it: doing so would show you the wrong '
      + '(usually empty) settings, chats, and memory.\n\n'
      + 'Fix: close the other process — look for ridian-backend.exe or '
      + 'python/uvicorn in Task Manager, or run '
      + '"netstat -ano | findstr :' + BACKEND_PORT + '" to find its PID — '
      + 'then launch Ridian Operator again.');
    return false;
  }
  if (!app.isPackaged) return true;   // dev: backend is external (.bat); nothing to spawn
  const exe = path.join(process.resourcesPath, 'backend', 'ridian-backend.exe');
  if (!fs.existsSync(exe)) return true;
  _backendChild = spawn(exe, [], {
    cwd: path.dirname(exe),
    windowsHide: true,
    stdio: 'ignore',
  });
  _backendChild.on('exit', () => { _backendChild = null; });
  return true;
}

function stopBackend() {
  if (!_backendChild) return;
  const pid = _backendChild.pid;
  _backendChild = null;
  if (process.platform === 'win32') {
    // Kill the whole tree — uvicorn may hold child workers.
    try { execFile('taskkill', ['/PID', String(pid), '/T', '/F']); } catch (_) {}
  } else {
    try { process.kill(pid); } catch (_) {}
  }
}

// Window/taskbar icon: the SUNRISE-WAVES emblem — the Ridian identity shared
// with the website + Open Gulf. Deliberately NOT a file named icon.ico/
// icon.png: those names carried the retired blue "RA" badge and are the
// Electron-tooling default, so they were deleted to keep any future packaging
// config from silently shipping the wrong identity.
//
// PACKAGED: set NO window icon. Windows derives the window/taskbar icon
// from the exe's embedded resource (sunrise-waves.ico via win.icon — proper
// multi-res: 256/48/32/16, classic BMP frames). Passing an .ico path to
// BrowserWindow loads as an EMPTY image (Electron's image loader does not
// decode ICO), and an explicitly-set empty icon OVERRIDES the exe's good
// one — that was the blank-taskbar bug, regardless of where the .ico lived.
// DEV: the exe is electron.exe (Electron logo), so give the window the
// emblem from a PNG, which the image loader handles reliably.
const ICON_PATH = path.join(__dirname, 'assets', 'android-chrome-512x512.png');
const ICON_OPTION = app.isPackaged
  ? {}
  : (fs.existsSync(ICON_PATH) ? { icon: ICON_PATH } : {});
// Windows AppUserModel surfaces (taskbar relaunch/pin) want a real .ico
// FILE path — ships via extraResources in packaged builds.
const APPDETAILS_ICON = app.isPackaged
  ? path.join(process.resourcesPath, 'sunrise-waves.ico')
  : path.join(__dirname, 'assets', 'sunrise-waves.ico');

// Stable AppUserModelID so Windows groups our taskbar entries and applies
// the icon correctly when the shortcut is launched. MUST match the AUMID
// the desktop shortcut writes via scripts/Create-Ridian-Agency-Shortcut.ps1
// — otherwise pinning the running app produces a separate "generic Electron"
// taskbar entry instead of relaunching through Start-Ridian-Agency.bat.
if (process.platform === 'win32') {
  app.setAppUserModelId('com.ridiantechnologies.ridianagency');
}

// The double-click launcher at the repo root — starts the backend AND the
// window. This is what a taskbar pin must relaunch (never bare electron.exe).
const LAUNCHER_PATH = path.join(__dirname, '..', 'Start-Ridian-Agency.bat');

// ---------------------------------------------------------------------------
// v6.7 single-instance lock. Clicking the pinned shortcut while Ridian is
// already running used to launch a SECOND instance, which then lost the
// global-hotkey registration to the first and blamed "another application"
// — the culprit was Ridian itself. Now: the second launch quits before ANY
// work (no backend spawn, no hotkey attempt, no window), and the first
// instance receives 'second-instance' and raises its window.
// The lock is scoped to the userData path. CORRECTION (v6.8): on Windows,
// Electron resolves appData via the OS (SHGetKnownFolderPath), which
// IGNORES the APPDATA env var — so redirecting APPDATA does NOT move the
// lock scope, and a harness instance would contend with the real running
// app (observed live: the real 0.8.8 held the lock and a test instance
// correctly quit as "second"). RIDIAN_USERDATA is the real isolation
// channel: harnesses set it (per-child env only) and userData — and with
// it the lock scope — genuinely moves to the scratch profile.
// ---------------------------------------------------------------------------
if (process.env.RIDIAN_USERDATA) {
  app.setPath('userData', process.env.RIDIAN_USERDATA);
}
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  console.log('[ridian] second launch detected — the running instance takes '
              + 'focus; this one quits without starting anything');
  app.quit();
}

app.on('second-instance', () => {
  console.log('[ridian] second-instance signal — raising the existing window');
  if (!app.isReady()) return;   // mid-boot: the window will show on its own
  raiseMainWindow();
});

function createWindow() {
  const win = new BrowserWindow({
    title: 'Ridian Agency',
    width: 1280,
    height: 860,
    minWidth: 880,
    minHeight: 600,
    backgroundColor: '#f7f8fa',
    autoHideMenuBar: true,
    show: false,
    ...ICON_OPTION,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Windows relaunch identity for THIS window. In dev mode the process is
  // node_modules' electron.exe, so "pin the running app" would otherwise pin
  // generic Electron (wrong icon, and clicking it opens Electron's default
  // app, not Ridian). These AppUserModel window properties tell the taskbar:
  // group under our AUMID, show the sunrise-waves emblem, and relaunch via
  // the .bat that starts backend + app. Set before show.
  if (process.platform === 'win32' && fs.existsSync(LAUNCHER_PATH)) {
    win.setAppDetails({
      appId: 'com.ridiantechnologies.ridianagency',   // must match the .lnk + setAppUserModelId
      appIconPath: APPDETAILS_ICON,                    // sunrise-waves.ico (real file, never asar)
      appIconIndex: 0,
      relaunchCommand: `"${LAUNCHER_PATH}"`,
      relaunchDisplayName: 'Ridian Agency',
    });
  }

  win.once('ready-to-show', () => win.show());

  // Native edit context menu. Electron shows no right-click menu by default,
  // so paste-by-mouse silently didn't exist. One handler at the main level
  // covers every editable field (textarea, input, contenteditable — Chromium
  // sets params.isEditable for all of them); non-editable UI gets Copy only
  // when text is selected, and no menu otherwise. Roles reuse Chromium's
  // built-in clipboard actions, so keyboard shortcuts are untouched.
  win.webContents.on('context-menu', (_event, params) => {
    const template = [];
    if (params.isEditable) {
      template.push(
        { role: 'cut', enabled: params.editFlags.canCut },
        { role: 'copy', enabled: params.editFlags.canCopy },
        { role: 'paste', enabled: params.editFlags.canPaste },
        { type: 'separator' },
        { role: 'selectAll', enabled: params.editFlags.canSelectAll },
      );
    } else if (params.selectionText && params.selectionText.trim()) {
      template.push({ role: 'copy' });
    }
    if (template.length) Menu.buildFromTemplate(template).popup({ window: win });
  });

  // Open any external links in the user's default browser rather than a new
  // Electron window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow = win;
  win.on('closed', () => { if (mainWindow === win) mainWindow = null; });
  return win;
}

// ---------------------------------------------------------------------------
// v6.0 Phase 7 — global hotkey + compact command bar
// ---------------------------------------------------------------------------
// Ctrl+Shift+R from anywhere in Windows opens a small always-on-top bar. A
// command typed or spoken there starts a run and raises the main window.
// Registration failure is LOUD (a dialog + a status the renderer can read),
// never a silent no-op.

let mainWindow = null;
let commandBar = null;
let hotkeyStatus = { ok: false, accelerator: DEFAULT_ACCELERATOR,
                     reason: 'pending', detail: '', active: '' };

function createCommandBar() {
  if (commandBar && !commandBar.isDestroyed()) return commandBar;
  commandBar = new BrowserWindow({
    width: 720, height: 92, frame: false, show: false, resizable: false,
    alwaysOnTop: true, skipTaskbar: true, transparent: true,
    fullscreenable: false, minimizable: false, maximizable: false,
    ...ICON_OPTION,
    webPreferences: {
      preload: path.join(__dirname, 'preload-commandbar.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  commandBar.loadFile(path.join(__dirname, 'renderer', 'commandbar.html'));
  commandBar.on('blur', () => { if (commandBar && commandBar.isVisible()) commandBar.hide(); });
  commandBar.on('closed', () => { commandBar = null; });
  return commandBar;
}

function toggleCommandBar() {
  const bar = createCommandBar();
  if (bar.isVisible()) { bar.hide(); return; }
  bar.center();
  bar.show();
  bar.focus();
  bar.webContents.send('commandbar:shown');
}

function raiseMainWindow() {
  const win = mainWindow && !mainWindow.isDestroyed() ? mainWindow : createWindow();
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
  return win;
}

function wireCommandBarIpc() {
  ipcMain.on('commandbar:close', () => { if (commandBar) commandBar.hide(); });
  ipcMain.on('commandbar:submit', (_event, text) => {
    const command = String(text || '').trim();
    if (!command) return;
    if (commandBar) commandBar.hide();
    const win = raiseMainWindow();
    const send = () => win.webContents.send('ridian:run-command', command);
    if (win.webContents.isLoading()) win.webContents.once('did-finish-load', send);
    else send();
  });
  // The renderer asks whether the hotkey actually registered, so Settings can
  // tell the truth instead of implying a shortcut that does nothing.
  ipcMain.handle('hotkey:status', () => hotkeyStatus);
  // v6.5: Settings applies a new hotkey WITHOUT a restart. The new binding
  // is attempted; on failure the previous one is restored and the returned
  // status says so — the renderer shows it verbatim.
  ipcMain.handle('hotkey:apply', (_event, accelerator) => {
    const result = applyHotkey({
      globalShortcut,
      currentAccelerator: hotkeyStatus.active || '',
      accelerator: String(accelerator || ''),
      onTrigger: toggleCommandBar,
    });
    hotkeyStatus = result;
    return hotkeyStatus;
  });
}

// v6.5: the hotkey is a SETTING (operator_global_hotkey in the same
// settings file as everything else). Priority: settings value, then the
// RIDIAN_HOTKEY env escape hatch, then the default. Read over HTTP — the
// backend is already up by the time this runs (whenReady awaits it).
async function resolveConfiguredHotkey() {
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/settings`);
    if (res.ok) {
      const s = await res.json();
      const configured = (s.operator_global_hotkey || '').trim();
      if (configured) return configured;
    }
  } catch (_err) { /* backend hiccup — fall through to env/default */ }
  return process.env.RIDIAN_HOTKEY || DEFAULT_ACCELERATOR;
}

async function installGlobalHotkey() {
  const accelerator = await resolveConfiguredHotkey();
  const result = registerGlobalHotkey({
    globalShortcut, accelerator, onTrigger: toggleCommandBar,
  });
  hotkeyStatus = { ...result, active: result.ok ? accelerator : '' };
  // Observable in stdout: the single-instance test asserts a second launch
  // NEVER prints this line (it must quit before any registration attempt).
  console.log(`[ridian] hotkey ${result.ok ? 'registered' : 'FAILED'}: ${accelerator}`
              + (result.ok ? '' : ` (${result.reason})`));
  if (!hotkeyStatus.ok) {
    // LOUD by contract: the user must never think a dead shortcut is live.
    // Settings ALSO shows this state persistently (hotkey:status).
    dialog.showErrorBox(
      'Ridian Operator — global shortcut unavailable',
      `${hotkeyStatus.detail}\n\n`
      + 'Everything else works normally; only the global command bar shortcut '
      + 'is affected. Change it in Settings → Advanced → Global hotkey '
      + '(applies immediately, no restart).',
    );
  }
  return hotkeyStatus;
}

// Tight CSP — local renderer assets only, with one allowed network origin.
function applyContentSecurityPolicy() {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            // blob: = the OpenAI TTS read-aloud clip (fetched from the
            // backend, played via object URL); BACKEND_ORIGIN = the
            // audiobook player streaming /operations/audio. Without an
            // explicit media-src both fall back to default-src 'self'
            // and audio playback is refused.
            `media-src 'self' blob: ${BACKEND_ORIGIN}`,
            `connect-src ${BACKEND_ORIGIN}`,
            "base-uri 'self'",
            "form-action 'none'",
            "frame-ancestors 'none'",
          ].join('; '),
        ],
      },
    });
  });
}

app.whenReady().then(async () => {
  // v6.7: a lockless (second) instance is already quitting — ready can still
  // fire on some platforms before the quit lands. Do NOTHING here.
  if (!gotTheLock) return;
  if (!sandboxEnvPreflight()) { app.quit(); return; }
  applyContentSecurityPolicy();
  Menu.setApplicationMenu(null); // hide default File/Edit/View menu chrome
  const backendOk = await startBackendIfNeeded();  // packaged: hidden uvicorn; dev: no-op
  if (!backendOk) { app.quit(); return; }          // stranger on the port — refused
  createWindow();
  wireCommandBarIpc();
  await installGlobalHotkey();   // dialog + persistent status if unavailable

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();   // never leave the combination held
  stopBackend();                    // the hidden backend dies with the app
});
