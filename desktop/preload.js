// Ridian Agency — preload script.
//
// The renderer runs with contextIsolation:true and nodeIntegration:false.
// We expose only a tiny constant the renderer needs (the backend origin) and
// nothing else. All HTTP calls happen via window.fetch in the renderer.

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('ridian', {
  backendOrigin: 'http://127.0.0.1:8000',
  platform: process.platform,
});
