// v4.2: freeze the Python backend with PyInstaller (--onedir) before
// electron-builder ships it as extraResources -> resources/backend/.
// Flags encode the installed-package data audit (2026-07-21): certifi
// (TLS roots — Anthropic/Intuit HTTPS fails without it), googleapiclient
// discovery documents (581 files — all Google APIs), docx/pptx templates,
// and uvicorn's dynamically-imported loops/protocols. The entrypoint is
// backend_main.py, which imports the app OBJECT (never a module string).
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..');
const apiDir = path.join(repoRoot, 'apps', 'api');
const python = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');

const args = [
  '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--noconsole',
  '--name', 'ridian-backend',
  '--distpath', path.join(apiDir, 'dist'),
  '--workpath', path.join(apiDir, 'build'),
  '--specpath', apiDir,
  '--collect-all', 'certifi',
  '--collect-data', 'googleapiclient',
  '--collect-data', 'docx',
  '--collect-data', 'pptx',
  '--hidden-import', 'uvicorn.logging',
  '--hidden-import', 'uvicorn.loops.auto',
  '--hidden-import', 'uvicorn.protocols.http.auto',
  '--hidden-import', 'uvicorn.protocols.websockets.auto',
  '--hidden-import', 'uvicorn.lifespan.on',
  '--add-data', `${path.join(apiDir, 'app', 'prompts')};app/prompts`,
  '--add-data', `${path.join(apiDir, 'app', 'static')};app/static`,
  path.join(apiDir, 'backend_main.py'),
];

console.log('[build-backend] freezing backend…');
const res = spawnSync(python, args, { stdio: 'inherit' });
if (res.status !== 0) {
  console.error('[build-backend] PyInstaller failed');
  process.exit(res.status || 1);
}
const exe = path.join(apiDir, 'dist', 'ridian-backend', 'ridian-backend.exe');
if (!fs.existsSync(exe)) {
  console.error('[build-backend] expected output missing:', exe);
  process.exit(1);
}
console.log('[build-backend] OK:', exe);
