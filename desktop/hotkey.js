// Global hotkey registration (v6.0 Phase 7) — pure and dependency-injected.
//
// Electron's globalShortcut.register() returns FALSE when the combination is
// already owned by another process, and it throws on a malformed accelerator.
// Both cases previously would have been silent no-ops: the user presses
// Ctrl+Shift+R forever and nothing happens, with no explanation. This module
// turns every failure into a structured result the caller MUST surface.
//
// No Electron import: `globalShortcut` is injected, so the real logic is
// testable (apps/api/tests/test_global_hotkey.py runs this file under Node).

const DEFAULT_ACCELERATOR = 'CommandOrControl+Shift+R';

// Electron accelerators: one or more modifiers plus exactly one key. We
// validate shape here so a typo fails LOUDLY at startup rather than throwing
// from deep inside Electron.
const MODIFIERS = new Set([
  'command', 'cmd', 'control', 'ctrl', 'commandorcontrol', 'cmdorctrl',
  'alt', 'option', 'altgr', 'shift', 'super', 'meta',
]);

function validateAccelerator(accelerator) {
  if (typeof accelerator !== 'string' || !accelerator.trim()) {
    return 'Hotkey is empty.';
  }
  const parts = accelerator.split('+').map((p) => p.trim()).filter(Boolean);
  if (parts.length < 2) {
    return `"${accelerator}" needs at least one modifier and a key (e.g. Ctrl+Shift+R).`;
  }
  const keys = parts.filter((p) => !MODIFIERS.has(p.toLowerCase()));
  if (keys.length !== 1) {
    return `"${accelerator}" must name exactly one non-modifier key.`;
  }
  return '';
}

/**
 * Register the global hotkey.
 *
 * Returns { ok, accelerator, reason, detail }:
 *   ok:false + reason 'invalid'    — malformed accelerator (never attempted)
 *   ok:false + reason 'taken'      — another app owns the combination
 *   ok:false + reason 'threw'      — Electron threw (detail carries the text)
 *   ok:false + reason 'unverified' — register() claimed success but
 *                                    isRegistered() disagrees
 * The caller is expected to surface every non-ok result to the user; a
 * silent no-op is exactly the bug this module exists to prevent.
 */
function registerGlobalHotkey({
  globalShortcut,
  accelerator = DEFAULT_ACCELERATOR,
  onTrigger,
} = {}) {
  if (!globalShortcut || typeof globalShortcut.register !== 'function') {
    return { ok: false, accelerator, reason: 'invalid',
             detail: 'No globalShortcut implementation was provided.' };
  }
  const badShape = validateAccelerator(accelerator);
  if (badShape) {
    return { ok: false, accelerator, reason: 'invalid', detail: badShape };
  }

  // Someone else already holds it (or we double-registered): report, never
  // silently overwrite.
  try {
    if (typeof globalShortcut.isRegistered === 'function'
        && globalShortcut.isRegistered(accelerator)) {
      return { ok: false, accelerator, reason: 'taken',
               detail: `${accelerator} is already registered by another `
                       + 'application. Close whatever owns it, or change the '
                       + 'shortcut, then restart Ridian.' };
    }
  } catch (err) {
    return { ok: false, accelerator, reason: 'threw',
             detail: `Checking ${accelerator} failed: ${err && err.message ? err.message : err}` };
  }

  let registered = false;
  try {
    registered = globalShortcut.register(accelerator, onTrigger || (() => {}));
  } catch (err) {
    return { ok: false, accelerator, reason: 'threw',
             detail: `Registering ${accelerator} failed: ${err && err.message ? err.message : err}` };
  }
  if (!registered) {
    return { ok: false, accelerator, reason: 'taken',
             detail: `Windows refused ${accelerator} — another application `
                     + 'already owns that combination. Close it, or change the '
                     + 'shortcut, then restart Ridian.' };
  }

  // Trust but verify: a true return with no actual registration would be the
  // silent failure all over again.
  try {
    if (typeof globalShortcut.isRegistered === 'function'
        && !globalShortcut.isRegistered(accelerator)) {
      return { ok: false, accelerator, reason: 'unverified',
               detail: `${accelerator} reported success but is not actually `
                       + 'registered.' };
    }
  } catch (_err) { /* verification is best-effort; registration succeeded */ }

  return { ok: true, accelerator, reason: '', detail: '' };
}

module.exports = { registerGlobalHotkey, validateAccelerator, DEFAULT_ACCELERATOR };
