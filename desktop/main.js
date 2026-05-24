// Ridian Agency — Electron main process.
// Creates a single BrowserWindow that loads renderer/index.html locally.
// The renderer talks to the existing FastAPI backend at http://127.0.0.1:8000
// over plain fetch — no IPC needed.

const { app, BrowserWindow, Menu, shell, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

const BACKEND_ORIGIN = 'http://127.0.0.1:8000';

// Use the bundled icon if it exists; otherwise Electron picks a default.
// generate_icon.py produces both icon.png (BrowserWindow) and icon.ico (Windows shortcut).
const ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const ICON_OPTION = fs.existsSync(ICON_PATH) ? { icon: ICON_PATH } : {};

// Stable AppUserModelID so Windows groups our taskbar entries and applies
// the icon correctly when the shortcut is launched.
if (process.platform === 'win32') {
  app.setAppUserModelId('com.ridiantechnologies.ridian-agency');
}

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

  win.once('ready-to-show', () => win.show());

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

app.whenReady().then(() => {
  applyContentSecurityPolicy();
  Menu.setApplicationMenu(null); // hide default File/Edit/View menu chrome
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
