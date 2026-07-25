// Ridian Agency — preload script.
//
// The renderer runs with contextIsolation:true and nodeIntegration:false.
// We expose only a tiny constant the renderer needs (the backend origin) and
// nothing else. All HTTP calls happen via window.fetch in the renderer.

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('ridian', {
  // v4.4 state-guard: RIDIAN_PORT moves a sandboxed harness's whole app to
  // a scratch port (matches main.js's supervisor + CSP). Unset = 8000.
  backendOrigin: `http://127.0.0.1:${parseInt(process.env.RIDIAN_PORT || '8000', 10)}`,
  platform: process.platform,
});
