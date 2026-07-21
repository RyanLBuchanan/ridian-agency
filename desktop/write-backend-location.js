// Build-time step: record where the Python backend lives so the PACKAGED
// app can spawn it (hidden) from outside the asar. v1 packaging ships the
// app shell; the backend runs from this repo checkout + venv (overridable
// at runtime via RIDIAN_BACKEND_DIR / RIDIAN_PYTHON env vars). A fully
// self-contained backend (PyInstaller) is a future step.
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..');
const out = {
  apiDir: path.join(repoRoot, 'apps', 'api'),
  python: path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
};
fs.writeFileSync(path.join(__dirname, 'backend-location.json'),
  JSON.stringify(out, null, 2) + '\n');
console.log('backend-location.json:', out);
