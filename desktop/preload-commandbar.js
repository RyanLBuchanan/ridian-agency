// Preload for the compact command bar (v6.0 Phase 7).
//
// Same isolation contract as the main preload: contextIsolation on,
// nodeIntegration off, and only a named, minimal surface exposed — submit a
// command, close the bar, and learn when the bar is shown.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ridianBar', {
  backendOrigin: `http://127.0.0.1:${parseInt(process.env.RIDIAN_PORT || '8000', 10)}`,
  submit: (text) => ipcRenderer.send('commandbar:submit', String(text || '')),
  close: () => ipcRenderer.send('commandbar:close'),
  onShown: (cb) => ipcRenderer.on('commandbar:shown', () => cb()),
});
