// Launcher for `npm run start`.
//
// Some environments (including the Claude Code sandbox) set
// ELECTRON_RUN_AS_NODE=1, which makes the electron binary behave like plain
// Node — no GUI, and `require('electron')` returns the binary path instead
// of the API. We clear it here before spawning, then forward exit code.

const { spawn } = require('node:child_process');
const electronPath = require('electron');

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(electronPath, ['.'], { stdio: 'inherit', env });
child.on('close', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  console.error('Failed to launch Electron:', err);
  process.exit(1);
});
