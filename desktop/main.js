// Ridian Agency — Electron main process.
// Creates a single BrowserWindow that loads renderer/index.html locally.
// The renderer talks to the existing FastAPI backend at http://127.0.0.1:8000
// over plain fetch — no IPC needed.

const { app, BrowserWindow, Menu, shell, session } = require('electron');
const { spawn, execFile } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

const BACKEND_ORIGIN = 'http://127.0.0.1:8000';
const BACKEND_PORT = 8000;

/* v4.1 packaged mode: main.js is the backend SUPERVISOR — it spawns uvicorn
   as a hidden background process (windowsHide: no console window ever
   exists) and kills the process tree on quit. Dev mode (npm start / the
   .bat) is untouched: the backend is started externally and this block
   no-ops because the port is already serving. */
let _backendChild = null;

function _backendLocation() {
  const env = {
    apiDir: process.env.RIDIAN_BACKEND_DIR || '',
    python: process.env.RIDIAN_PYTHON || '',
  };
  try {
    const cfg = JSON.parse(fs.readFileSync(
      path.join(process.resourcesPath, 'backend-location.json'), 'utf-8'));
    return { apiDir: env.apiDir || cfg.apiDir, python: env.python || cfg.python };
  } catch (_) {
    return env.apiDir && env.python ? env : null;
  }
}

function _portInUse(port) {
  return new Promise((resolve) => {
    const sock = net.connect({ port, host: '127.0.0.1' });
    sock.once('connect', () => { sock.destroy(); resolve(true); });
    sock.once('error', () => resolve(false));
    setTimeout(() => { try { sock.destroy(); } catch (_) {} resolve(false); }, 1500);
  });
}

async function startBackendIfNeeded() {
  if (!app.isPackaged) return;                   // dev: external backend
  if (await _portInUse(BACKEND_PORT)) return;    // already running — reuse
  const loc = _backendLocation();
  if (!loc || !fs.existsSync(loc.python) || !fs.existsSync(loc.apiDir)) return;
  _backendChild = spawn(
    loc.python,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
    { cwd: loc.apiDir, windowsHide: true, stdio: 'ignore' },
  );
  _backendChild.on('exit', () => { _backendChild = null; });
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

// Window/taskbar icon: the SUNRISE-WAVES emblem (favicon.ico, multi-size) —
// the Ridian identity shared with the website + Open Gulf. Deliberately NOT
// a file named icon.ico/icon.png: those names carried the retired blue "RA"
// badge and are the Electron-tooling default, so they were deleted to keep
// any future packaging config from silently shipping the wrong identity.
const ICON_PATH = path.join(__dirname, 'assets', 'favicon.ico');
const ICON_OPTION = fs.existsSync(ICON_PATH) ? { icon: ICON_PATH } : {};

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
      appIconPath: ICON_PATH,                          // assets/favicon.ico — sunrise-waves
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
  applyContentSecurityPolicy();
  Menu.setApplicationMenu(null); // hide default File/Edit/View menu chrome
  await startBackendIfNeeded();  // packaged: hidden uvicorn; dev: no-op
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', stopBackend);   // the hidden backend dies with the app
