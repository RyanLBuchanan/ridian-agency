// Exercise the actual supervisor with process/network boundaries replaced.
// These tests do not run Electron, bind ports, read owner state, or claim
// native Windows installer coverage. Run: node --test desktop/tests/*.test.cjs
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const windowsPath = path.win32;
const { test } = require('node:test');
const vm = require('node:vm');

const fullSource = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
const source = fullSource.split('// Window/taskbar icon:')[0];
// Include the real lifecycle callbacks as well as the supervisor. Window and
// hotkey boundaries below are fakes; the readiness, second-launch and shutdown
// gates are not. This includes the real drain-before-quit behavior.
const lifecycleSource = fullSource.slice(
  fullSource.indexOf("app.on('second-instance'"), fullSource.indexOf('\nfunction createWindow()'),
) + fullSource.slice(
  fullSource.indexOf('app.whenReady().then'),
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
  let portSocket;
  const quit = () => {
    lifecycle.quits++;
    let prevented = false;
    appEvents.emit('before-quit', { preventDefault: () => { prevented = true; } });
    if (!prevented) appEvents.emit('will-quit');
  };
  const env = {
    APPDATA: windowsPath.join('C:\\profile', PRIVATE),
    ANTHROPIC_API_KEY: PRIVATE,
    RIDIAN_APP_VERSION: 'stale-parent-version',
    ...options.env,
  };
  const packaged = options.packaged ?? true;
  const desktopDir = windowsPath.join('C:\\repository', PRIVATE, 'desktop');
  const expectedDataDir = env.RIDIAN_SANDBOX && env.RIDIAN_DATA_DIR
    ? env.RIDIAN_DATA_DIR
    : packaged ? windowsPath.join(env.APPDATA, 'Ridian Operator')
      : windowsPath.join(desktopDir, '..', 'apps', 'api');
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
      if (name === 'node:path') return windowsPath;
      if (name === 'electron') return {
        app: {
          isPackaged: packaged,
          getVersion: () => '0.9.18',
          isReady: () => true,
          on: (...args) => appEvents.on(...args),
          whenReady: () => ({ then: callback => { readyCallback = callback; } }),
          quit,
        },
        Menu: { setApplicationMenu() {} },
        BrowserWindow: { getAllWindows: () => Array(lifecycle.windows).fill({ hide() {} }) },
        globalShortcut: { unregisterAll() {} },
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
        portSocket = socket;
        socket.destroy = () => {};
        if (!options.holdPortProbe) {
          queueMicrotask(() => socket.emit(options.occupied ? 'connect' : 'error'));
        }
        return socket;
      } };
      if (name === './hotkey') return {};
      return require(name);
    },
    __dirname: desktopDir,
    process: {
      env,
      resourcesPath: windowsPath.join('C:\\installation', PRIVATE, 'resources'),
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
  async function advance(ms) {
    const target = now + ms;
    for (let steps = 0; steps < 1000; steps++) {
      await flush();
      const next = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > target) {
        now = target;
        return;
      }
      now = next[1].at;
      timers.delete(next[0]);
      next[1].fn();
    }
    assert.fail('timers did not quiesce');
  }
  return {
    ...context.api, child, dialogs, logs, spawns, kills, requests, env,
    matchingIdentity, flush, settle, advance, now: () => now,
    lifecycle,
    beginReady: () => readyCallback(),
    secondInstance: () => appEvents.emit('second-instance'),
    requestQuit: quit,
    releasePortProbe: () => portSocket.emit(options.occupied ? 'connect' : 'error'),
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
  assert.equal(exe, windowsPath.join('C:\\installation', PRIVATE, 'resources', 'backend', 'ridian-backend.exe'));
  assert.deepEqual([...args], []);
  assert.equal(options.cwd, windowsPath.dirname(exe));
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

test('quit cancels pending startup while retaining its child for the original drain', async () => {
  let releaseHealth, releaseDrain, healthSignal;
  const h = harness({ fetch: ({ url, init, response }) => new Promise(resolve => {
    if (url.endsWith('/health')) {
      healthSignal = init.signal;
      // Deliberately resolve even after abort: a completed response may already
      // be queued when quit begins, and must not resurrect the ready callback.
      releaseHealth = () => resolve(response());
    } else if (url.endsWith('/app/drain')) {
      assert.equal(init.method, 'POST');
      releaseDrain = () => resolve(response());
    } else {
      assert.fail(`unexpected request: ${url}`);
    }
  }) });
  const ready = h.beginReady();
  await h.flush();
  assert.equal(typeof releaseHealth, 'function');
  h.requestQuit();
  await h.flush();
  assert.equal(healthSignal.aborted, true, 'quit cancels the in-flight health request');
  assert.equal(typeof releaseDrain, 'function', 'retain the existing drain request');
  assert.equal(h.lifecycle.quits, 1, 'cancellation must not request another quit');
  assert.equal(h.kills.length, 0, 'the backend must remain alive while drain is pending');
  releaseHealth();
  await h.settle(ready);
  h.secondInstance();
  assert.deepEqual(h.lifecycle, { windows: 0, raises: 0, quits: 1, hotkeys: 0 });
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.kills.length, 0);
  releaseDrain();
  await h.flush();
  assert.equal(h.lifecycle.quits, 2, 'only drain completion requests the final quit');
  assert.deepEqual(h.kills, [{ exe: 'taskkill', args: ['/PID', '4242', '/T', '/F'] }]);
  await h.advance(65000);
  assert.equal(h.dialogs.length, 0, 'cancelled startup cannot show a stale timeout');
  assert.equal(h.lifecycle.quits, 2);
  assert.equal(h.kills.length, 1);
});

test('quit during pending startup preserves the drain timeout before stopping its child', async () => {
  const h = harness({ fetch: ({ init }) => new Promise((_, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error(PRIVATE)), { once: true });
  }) });
  const ready = h.beginReady();
  await h.flush();
  h.requestQuit();
  await h.settle(ready);
  await h.advance(24999);
  assert.equal(h.lifecycle.quits, 1);
  assert.equal(h.kills.length, 0, 'startup cancellation must not shorten the drain grace');
  assert.equal(h.dialogs.length, 0);
  await h.advance(1);
  assert.equal(h.lifecycle.quits, 2);
  assert.equal(h.kills.length, 1);
  await h.advance(40000);
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.lifecycle.windows, 0);
  assert.equal(h.lifecycle.hotkeys, 0);
});

test('quit during the port probe prevents a later backend spawn or window', async () => {
  const h = harness({ holdPortProbe: true });
  const ready = h.beginReady();
  await h.flush();
  h.requestQuit();
  h.releasePortProbe();
  await h.settle(ready);
  h.secondInstance();
  assert.deepEqual(h.lifecycle, { windows: 0, raises: 0, quits: 1, hotkeys: 0 });
  assert.equal(h.spawns.length, 0);
  assert.equal(h.requests.length, 0);
  assert.equal(h.kills.length, 0);
  assert.equal(h.dialogs.length, 0);
});

test('quit while checking an existing backend never adopts or drains it', async () => {
  let releaseHealth;
  const h = harness({ occupied: true, fetch: ({ response }) => new Promise(resolve => {
    releaseHealth = () => resolve(response());
  }) });
  const ready = h.beginReady();
  await h.flush();
  h.requestQuit();
  releaseHealth();
  await h.settle(ready);
  assert.deepEqual(h.lifecycle, { windows: 0, raises: 0, quits: 1, hotkeys: 0 });
  assert.equal(h.spawns.length, 0);
  assert.equal(h.requests.length, 1, 'only the original health request was made');
  assert.equal(h.kills.length, 0);
  assert.equal(h.dialogs.length, 0);
});

test('successful packaged startup still drains before stopping the owned backend on quit', async () => {
  let releaseDrain;
  const h = harness({ fetch: async ({ url, response }) => {
    if (url.endsWith('/app/drain')) {
      return new Promise(resolve => { releaseDrain = () => resolve(response()); });
    }
    return response();
  } });
  await h.settle(h.beginReady());
  assert.equal(h.lifecycle.windows, 1);
  assert.equal(h.lifecycle.hotkeys, 1);
  h.requestQuit();
  await h.flush();
  h.secondInstance();
  assert.equal(h.lifecycle.raises, 0, 'a second launch must not raise the draining window');
  assert.equal(h.lifecycle.quits, 1);
  assert.equal(h.kills.length, 0);
  releaseDrain();
  await h.flush();
  assert.equal(h.lifecycle.quits, 2);
  assert.deepEqual(h.kills, [{ exe: 'taskkill', args: ['/PID', '4242', '/T', '/F'] }]);
  assert.equal(h.dialogs.length, 0);
});
