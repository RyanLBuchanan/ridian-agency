// Ridian Agency — Electron main process.
// Creates a single BrowserWindow that loads renderer/index.html locally.
// The renderer talks to the existing FastAPI backend at http://127.0.0.1:8000
// over plain fetch — no IPC needed.

const { app, BrowserWindow, Menu, shell, session } = require('electron');
const path = require('node:path');

const BACKEND_ORIGIN = 'http://127.0.0.1:8000';

function createWindow() {
  const win = new BrowserWindow({
    title: 'Ridian Agency',
    width: 1100,
    height: 820,
    minWidth: 720,
    minHeight: 560,
    backgroundColor: '#f7f8fa',
    autoHideMenuBar: true,
    show: false,
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
