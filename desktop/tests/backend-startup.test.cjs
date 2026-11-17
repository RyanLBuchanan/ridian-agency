// Exercise the actual supervisor with process/network boundaries replaced.
// These tests do not run Electron, bind ports, read owner state, or claim
// native Windows installer coverage. Run: node --test desktop/tests/*.test.cjs
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const fullSource = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
const source = fullSource.split('// Window/taskbar icon:')[0];
// Include the real lifecycle callbacks as well as the supervisor. Window and
// hotkey boundaries below are fakes; the readiness/second-launch gates are not.
const lifecycleSource = fullSource.slice(
  fullSource.indexOf("app.on('second-instance'"), fullSource.indexOf('\nfunction createWindow()'),
) + fullSource.slice(
  fullSource.indexOf('app.whenReady().then'), fullSource.indexOf("\napp.on('window-all-closed'"),
);
const PRIVATE = 'PRIVATE_VALUE_MUST_NEVER_APPEAR';

function harness(options = {}) {
  let now = 0;
  let nextTimer = 0;
  const timers = new Map();
  const dialogs = [], logs = [], spawns = [], kills = [], requests = [];
  const appEvents = new EventEmitter();
  const lifecycle = { windows: 0, raises: 0, quits: 0, hotkeys: 0 };
  let readyCallback;
  const env = {
    APPDATA: `/profile/${PRIVATE}`,
    ANTHROPIC_API_KEY: PRIVATE,
    RIDIAN_APP_VERSION: 'stale-parent-version',
    ...options.env,
  };
  const packaged = options.packaged ?? true;
  const desktopDir = `/repository/${PRIVATE}/desktop`;
  const expectedDataDir = env.RIDIAN_SANDBOX && env.RIDIAN_DATA_DIR
    ? env.RIDIAN_DATA_DIR
    : packaged ? path.join(env.APPDATA, 'Ridian Operator')
      : path.join(desktopDir, '..', 'apps', 'api');
  const matchingIdentity = { service: 'ridian-agency', data_dir: expectedDataDir };
  const child = new EventEmitter();
  Object.assign(child, { pid: 4242, exitCode: null, signalCode: null });
  const originalEmit = child.emit.bind(child);
  child.emit = (event, ...args) => {
    if (event === 'exit') {
      child.exitCode = args[0];
      child.signalCode = args[1];
    }
    return originalEmit(event, ...args);
  };
  const setTimer = (fn, ms = 0) => {
    const id = ++nextTimer;
    timers.set(id, { at: now + ms, fn });
    return id;
  };
  const flush = async () => {
    // Drain promise continuations without allowing real wall-clock timers.
    for (let i = 0; i < 24; i++) await Promise.resolve();
  };
  const response = (identity = matchingIdentity) => ({
    ok: true, json: async () => identity,
  });
  const context = {
    require(name) {
      if (name === 'electron') return {
        app: {
          isPackaged: packaged,
          getVersion: () => '0.9.18',
          isReady: () => true,
          on: (...args) => appEvents.on(...args),
          whenReady: () => ({ then: callback => { readyCallback = callback; } }),
          quit: () => { lifecycle.quits++; },
        },
        Menu: { setApplicationMenu() {} },
        BrowserWindow: { getAllWindows: () => Array(lifecycle.windows).fill({}) },
        dialog: { showErrorBox: (...args) => dialogs.push(args) },
      };
      if (name === 'node:child_process') return {
        spawn: (...args) => {
          spawns.push(args);
          if (options.spawnThrow) throw options.spawnThrow;
          options.onSpawn?.(child, setTimer);
          return child;
        },
        execFile: (exe, args, ...rest) => {
          kills.push({ exe, args: [...args] });
          const callback = rest.at(-1);
          if (typeof callback === 'function') queueMicrotask(() => callback(null));
          return new EventEmitter();
        },
      };
      if (name === 'node:fs') return { existsSync: () => options.exists ?? true };
      if (name === 'node:net') return { connect: () => {
        const socket = new EventEmitter();
        socket.destroy = () => {};
        queueMicrotask(() => socket.emit(options.occupied ? 'connect' : 'error'));
        return socket;
      } };
      if (name === './hotkey') return {};
      return require(name);
    },
    __dirname: desktopDir,
    process: {
      env,
      resourcesPath: `/installation/${PRIVATE}/resources`,
      platform: 'win32',
      kill: (pid) => kills.push({ pid }),
    },
    console: Object.fromEntries(['log', 'warn', 'error'].map(method =>
      [method, (...args) => logs.push(args.join(' '))])),
    Date: class extends Date { static now() { return now; } },
    performance: { now: () => now },
    AbortController,
    gotTheLock: true,
    applyContentSecurityPolicy() {},
    createWindow: () => { lifecycle.windows++; },
    raiseMainWindow: () => {
      lifecycle.raises++;
      if (lifecycle.windows === 0) lifecycle.windows++;
    },
    wireCommandBarIpc() {},
    installGlobalHotkey: async () => { lifecycle.hotkeys++; },
    setTimeout: setTimer,
    clearTimeout: id => timers.delete(id),
    fetch: async (url, init) => {
      requests.push({ url, init });
      return options.fetch
        ? options.fetch({ url, init, response, matchingIdentity, requests, child })
        : response(options.identity ?? matchingIdentity);
    },
  };
  vm.createContext(context);
  vm.runInContext(source + lifecycleSource
    + '\nglobalThis.api = { startBackendIfNeeded, stopBackend };', context);

  async function settle(promise, maxTime = 65000) {
    let settled = false, result, failure;
    promise.then(value => { result = value; settled = true; }, error => {
      failure = error; settled = true;
    });
    for (let steps = 0; steps < 1000; steps++) {
      await flush();
      if (settled) {
        if (failure) throw failure;
        return result;
      }
      const next = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      assert.ok(next, 'startup stalled without a deadline');
      assert.ok(next[1].at <= maxTime, 'startup exceeded its bounded deadline');
      now = next[1].at;
      timers.delete(next[0]);
      next[1].fn();
    }
    assert.fail('startup failed to settle');
  }
  return {
    ...context.api, child, dialogs, logs, spawns, kills, requests, env,
    matchingIdentity, flush, settle, now: () => now,
    lifecycle,
    beginReady: () => readyCallback(),
    secondInstance: () => appEvents.emit('second-instance'),
  };
}

function assertSafeDiagnostic(h) {
  assert.equal(h.dialogs.length, 1, 'report one actionable startup failure');
  const output = JSON.stringify([h.dialogs, h.logs]);
  assert.ok(!output.includes(PRIVATE), 'do not expose paths, credentials or raw errors');
  assert.ok(output.length < 3000, 'diagnostics must be bounded');
  assert.match(h.dialogs[0][1], /reinstall|install|try again|restart|relaunch|launch.*again/i);
}

test('missing packaged executable fails with safe reinstall guidance before spawning', async () => {
  const h = harness({ exists: false });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  assert.match(h.dialogs[0][1], /reinstall|install/i);
  assert.equal(h.spawns.length, 0);
  assert.equal(h.kills.length, 0);
});

test('synchronous launch exception is caught without exposing raw exception fields', async () => {
  const error = Object.assign(new Error(PRIVATE.repeat(1000)), { code: PRIVATE, path: PRIVATE });
  const h = harness({ spawnThrow: error });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  assert.equal(h.kills.length, 0);
});

test('asynchronous launch error is handled without uncaught error or secret disclosure', async () => {
  const h = harness({
    onSpawn: (child, later) => {
      child.pid = undefined;
      later(() => child.emit('error', Object.assign(new Error(PRIVATE), { code: PRIVATE })), 1);
    },
    fetch: async () => { throw new Error('connection refused'); },
  });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  assert.equal(h.kills.length, 0, 'failed spawn must not taskkill an undefined PID');
});

test('early exit is reported once and no dead process is killed', async () => {
  const h = harness({
    onSpawn: (child, later) => later(() => child.emit('exit', 7, null), 1),
    fetch: async () => { throw new Error('connection refused'); },
  });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  h.stopBackend();
  assert.equal(h.kills.length, 0);
});

test('present executable keeps launch arguments, working directory and environment', async () => {
  const h = harness();
  assert.equal(await h.settle(h.startBackendIfNeeded()), true);
  assert.equal(h.spawns.length, 1);
  const [exe, args, options] = h.spawns[0];
  assert.equal(exe, `/installation/${PRIVATE}/resources/backend/ridian-backend.exe`);
  assert.deepEqual([...args], []);
  assert.equal(options.cwd, path.dirname(exe));
  assert.equal(options.windowsHide, true);
  assert.equal(options.stdio, 'ignore');
  assert.deepEqual({ ...options.env }, { ...h.env, RIDIAN_APP_VERSION: '0.9.18' });
  assert.equal(h.env.RIDIAN_APP_VERSION, 'stale-parent-version', 'parent env stays unchanged');
  assert.ok(h.requests.length > 0, 'newly spawned backend must pass identity readiness');
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.kills.length, 0);
  h.stopBackend();
  assert.deepEqual(h.kills, [{ exe: 'taskkill', args: ['/PID', '4242', '/T', '/F'] }]);
});

test('delayed backend becomes ready without a false failure or restart', async () => {
  const h = harness({ fetch: async ({ requests, response }) => {
    if (requests.length < 3) throw new Error('connection refused');
    return response();
  } });
  assert.equal(await h.settle(h.startBackendIfNeeded()), true);
  assert.equal(h.requests.length, 3);
  assert.equal(h.spawns.length, 1);
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.kills.length, 0);
});

test('unresponsive backend has a bounded deadline and only its owned child is stopped', async () => {
  const h = harness({ fetch: async ({ init }) => new Promise((_, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
  }) });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  assert.ok(h.now() > 0 && h.now() <= 65000);
  assert.deepEqual(h.kills, [{ exe: 'taskkill', args: ['/PID', '4242', '/T', '/F'] }]);
  h.stopBackend();
  assert.equal(h.kills.length, 1, 'do not kill again during quit');
});

test('a stalled health response body cannot bypass the startup deadline', async () => {
  const h = harness({ fetch: async ({ init }) => ({
    ok: true,
    json: () => new Promise((_, reject) => {
      init.signal.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }),
  }) });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  assert.ok(h.now() <= 65000);
  assert.equal(h.kills.length, 1);
});

test('early exit cancels an in-flight health request promptly', async () => {
  let aborted = false;
  const h = harness({
    onSpawn: (child, later) => later(() => child.emit('exit', 9, PRIVATE), 10),
    fetch: async ({ init }) => new Promise((_, reject) => {
      init.signal.addEventListener('abort', () => {
        aborted = true;
        reject(new Error(PRIVATE));
      }, { once: true });
    }),
  });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  assert.equal(aborted, true);
  assert.ok(h.now() < 3000, 'exit does not wait for the request or startup timeout');
  assert.equal(h.kills.length, 0);
});

test('wrong identity after spawning never reports ready or exposes its response', async () => {
  const h = harness({ identity: { service: 'ridian-agency', data_dir: PRIVATE, token: PRIVATE } });
  assert.equal(await h.settle(h.startBackendIfNeeded()), false);
  assertSafeDiagnostic(h);
  assert.deepEqual(h.kills, [{ exe: 'taskkill', args: ['/PID', '4242', '/T', '/F'] }]);
});

test('post-readiness child errors remain handled without raw diagnostics', async () => {
  const h = harness();
  assert.equal(await h.settle(h.startBackendIfNeeded()), true);
  assert.doesNotThrow(() => h.child.emit('error', new Error(PRIVATE)));
  assert.equal(h.dialogs.length, 0, 'do not produce a late startup-failure dialog');
  assert.ok(!JSON.stringify(h.logs).includes(PRIVATE));
  h.child.emit('exit', 0, null);
  h.stopBackend();
  assert.equal(h.kills.length, 0);
});

test('matching existing backend is reused, including when the bundled exe is absent', async () => {
  const h = harness({ occupied: true, exists: false });
  assert.equal(await h.settle(h.startBackendIfNeeded()), true);
  assert.equal(h.spawns.length, 0);
  assert.equal(h.dialogs.length, 0);
  h.stopBackend();
  assert.equal(h.kills.length, 0);
});

for (const [description, identity] of [
  ['unrelated service', { service: 'other', data_dir: PRIVATE }],
  ['wrong state directory', { service: 'ridian-agency', data_dir: PRIVATE }],
  ['missing state identity', { service: 'ridian-agency' }],
]) {
  test(`occupied port rejects ${description} without spawning or killing it`, async () => {
    const h = harness({ occupied: true, identity });
    assert.equal(await h.settle(h.startBackendIfNeeded()), false);
    assertSafeDiagnostic(h);
    assert.equal(h.spawns.length, 0);
    h.stopBackend();
    assert.equal(h.kills.length, 0);
  });
}

test('development launch continues to leave backend startup external', async () => {
  const h = harness({ packaged: false, exists: false });
  assert.equal(await h.settle(h.startBackendIfNeeded()), true);
  assert.equal(h.spawns.length, 0);
  assert.equal(h.requests.length, 0);
  assert.equal(h.dialogs.length, 0);
  h.stopBackend();
  assert.equal(h.kills.length, 0);
});

test('second launch cannot open a window while startup is pending or after it fails', async () => {
  const h = harness({
    onSpawn: (child, later) => later(() => child.emit('exit', 7, null), 10),
    fetch: async ({ init }) => new Promise((_, reject) => {
      init.signal.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
    }),
  });
  const ready = h.beginReady();
  await h.flush();
  assert.equal(h.requests.length, 1, 'backend readiness is pending');
  h.secondInstance();
  assert.deepEqual(h.lifecycle, { windows: 0, raises: 0, quits: 0, hotkeys: 0 });
  await h.settle(ready);
  assertSafeDiagnostic(h);
  assert.equal(h.lifecycle.quits, 1);
  h.secondInstance();
  assert.deepEqual(h.lifecycle, { windows: 0, raises: 0, quits: 1, hotkeys: 0 });
});

test('successful readiness opens once, then a second launch raises the existing window', async () => {
  let releaseHealth;
  const h = harness({ fetch: ({ response }) => new Promise(resolve => {
    releaseHealth = () => resolve(response());
  }) });
  const ready = h.beginReady();
  await h.flush();
  assert.equal(typeof releaseHealth, 'function', 'backend readiness is pending');
  h.secondInstance();
  assert.deepEqual(h.lifecycle, { windows: 0, raises: 0, quits: 0, hotkeys: 0 });
  releaseHealth();
  await h.settle(ready);
  assert.deepEqual(h.lifecycle, { windows: 1, raises: 0, quits: 0, hotkeys: 1 });
  h.secondInstance();
  assert.deepEqual(h.lifecycle, { windows: 1, raises: 1, quits: 0, hotkeys: 1 });
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.kills.length, 0);
});
