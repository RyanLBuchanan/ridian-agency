// Ridian Agency — preload script.
//
// The renderer runs with contextIsolation:true and nodeIntegration:false.
// We expose only a tiny constant the renderer needs (the backend origin) and
// nothing else. All HTTP calls happen via window.fetch in the renderer.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ridian', {
  // v4.4 state-guard: RIDIAN_PORT moves a sandboxed harness's whole app to
  // a scratch port (matches main.js's supervisor + CSP). Unset = 8000.
  backendOrigin: `http://127.0.0.1:${parseInt(process.env.RIDIAN_PORT || '8000', 10)}`,
  platform: process.platform,
  // v6.0 Phase 7: a command typed or spoken in the global command bar
  // arrives here and starts a run in this window.
  onRunCommand: (cb) => ipcRenderer.on('ridian:run-command',
                                       (_e, text) => cb(String(text || ''))),
  // Whether the global shortcut actually registered — Settings shows the
  // truth rather than implying a shortcut that does nothing.
  hotkeyStatus: () => ipcRenderer.invoke('hotkey:status'),
});
