/* Ridian Agency — desktop renderer.
 *
 * Talks to the local FastAPI backend at http://127.0.0.1:8000 via fetch().
 * The backend origin is provided by preload.js as window.ridian.backendOrigin.
 */

const BACKEND = (window.ridian && window.ridian.backendOrigin) || 'http://127.0.0.1:8000';

const EXAMPLE_TASK =
  "Research practical AI consulting opportunities for small businesses in Gulf Shores, Orange Beach, Foley, and Fairhope Alabama.";

const RESULT_FIELDS = [
  'artifact_folder',
  'research_summary',
  'business_document',
  'slide_outline',
  'draft_email',
];

const els = {
  taskInput: document.getElementById('task-input'),
  runBtn: document.getElementById('run-btn'),
  clearBtn: document.getElementById('clear-btn'),
  exampleBtn: document.getElementById('example-btn'),
  status: document.getElementById('status-region'),
  elapsed: document.getElementById('elapsed'),
  errorRegion: document.getElementById('error-region'),
  errorMessage: document.getElementById('error-message'),
  resultsRegion: document.getElementById('results-region'),
  backendPill: document.getElementById('backend-pill'),
  backendLabel: document.getElementById('backend-pill-label'),
  backendDownBanner: document.getElementById('backend-down-banner'),
  sendEmailBtn: document.getElementById('send-email-btn'),
  sendEmailStatus: document.getElementById('send-email-status'),
  actionsStatus: document.getElementById('actions-status'),
  actionOpenFolder: document.getElementById('action-open-folder'),
  actionCopyFolder: document.getElementById('action-copy-folder'),
  actionExportZip: document.getElementById('action-export-zip'),
  actionUploadDrive: document.getElementById('action-upload-drive'),
  actionDriveLink: document.getElementById('action-drive-link'),
  googleConnectBtn: document.getElementById('google-connect-btn'),
  googleDisconnectBtn: document.getElementById('google-disconnect-btn'),
  googleStatusLabel: document.getElementById('google-status-label'),
  googleStatusHint: document.getElementById('google-status-hint'),
  settingsOpenBtn: document.getElementById('settings-open-btn'),
  settingsModal: document.getElementById('settings-modal'),
  settingsForm: document.getElementById('settings-form'),
  settingsCloseBtn: document.getElementById('settings-close-btn'),
  settingsCancelBtn: document.getElementById('settings-cancel-btn'),
  settingsSaveBtn: document.getElementById('settings-save-btn'),
  settingsTestEmailBtn: document.getElementById('settings-test-email-btn'),
  settingsStatus: document.getElementById('settings-status'),
  settingsPasswordHint: document.getElementById('settings-password-hint'),
  settingsOpenaiKeyHint: document.getElementById('settings-openai-key-hint'),
  settingsOutputsPath: document.getElementById('settings-outputs-path'),
  openaiMissingBanner: document.getElementById('openai-missing-banner'),
  // Workflow mode + Social Media Production
  statusSub: document.getElementById('status-sub'),
  modeBusinessPanel: document.getElementById('mode-business'),
  modeSocialPanel: document.getElementById('mode-social'),
  resultsBusiness: document.getElementById('results-business'),
  resultsSocial: document.getElementById('results-social'),
  socialPromptsTabs: document.getElementById('social-prompts-tabs'),
  socialPromptsPanels: document.getElementById('social-prompts-panels'),
  socialChannel: document.getElementById('social-channel'),
  socialStartingPoint: document.getElementById('social-starting-point'),
  socialContentFormat: document.getElementById('social-content-format'),
  socialGoal: document.getElementById('social-goal'),
  socialOutputDepth: document.getElementById('social-output-depth'),
  socialMediaNotes: document.getElementById('social-media-notes'),
  socialTopicNotes: document.getElementById('social-topic-notes'),
  socialRunBtn: document.getElementById('social-run-btn'),
  socialClearBtn: document.getElementById('social-clear-btn'),
};

let cachedSettings = null;
let currentMode = 'business'; // 'business' | 'social'

const DEFAULT_EMAIL_SUBJECT = 'Ridian Agency Draft Email Output';

const PROMPT_CATEGORIES = [
  {
    id: 'market-research',
    label: 'Market Research',
    prompts: [
      'Research practical AI consulting opportunities for small businesses in Baldwin County, Alabama. Focus on industries, pain points, competitors, and first outreach opportunities.',
      'Research AI workflow automation opportunities for healthcare clinics, dental offices, and wellness providers in Lower Alabama.',
      'Research how local tourism and hospitality businesses could use AI for guest communication, scheduling, reviews, and operations.',
    ],
  },
  {
    id: 'client-outreach',
    label: 'Client Outreach',
    prompts: [
      'Create a concise outreach package for a local business owner explaining how AI workflow automation could save time, reduce missed follow-ups, and improve customer communication.',
      'Draft a professional introductory email to a chamber of commerce director proposing a short AI productivity presentation for local businesses.',
      'Create a follow-up email after an AI consulting conversation, summarizing the opportunity and suggesting next steps.',
    ],
  },
  {
    id: 'slide-decks',
    label: 'Slide Decks',
    prompts: [
      'Create a 7-slide outline for a presentation titled "Practical AI for Small Business Owners." Include pain points, examples, workflow ideas, and a simple call to action.',
      'Create a chamber of commerce lunch-and-learn slide outline about AI productivity tools for local businesses.',
      'Create a sales presentation outline for Ridian Technologies explaining market research agents, document generation, and email workflow support.',
    ],
  },
  {
    id: 'internal-productivity',
    label: 'Internal Productivity',
    prompts: [
      'Create a weekly business development plan for Ridian Technologies focused on outreach, follow-ups, demos, content creation, and partnership opportunities.',
      'Create a prioritized task plan for turning Ridian Agency into a polished local desktop product.',
      'Create a founder operating brief summarizing the next 5 actions, risks, and opportunities for Ridian Technologies this week.',
    ],
  },
  {
    id: 'industry-specific',
    label: 'Industry-Specific',
    prompts: [
      'Research AI automation opportunities for HVAC, plumbing, and electrical companies in Baldwin County.',
      'Research AI productivity opportunities for real estate agents and property managers in coastal Alabama.',
      'Research AI support workflows for educators, instructional designers, and training departments.',
    ],
  },
];

let currentResult = null;
let elapsedTimer = null;

/* ---------- DOM helpers ---------- */

const show = (el) => el.classList.remove('hidden');
const hide = (el) => el.classList.add('hidden');

function setRunning(isRunning) {
  els.runBtn.disabled = isRunning;
  els.clearBtn.disabled = isRunning;
  els.exampleBtn.disabled = isRunning;
  els.runBtn.textContent = isRunning ? 'Running…' : 'Run workflow';
}

function startElapsed() {
  const t0 = Date.now();
  els.elapsed.textContent = '0s';
  elapsedTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    els.elapsed.textContent = `${s}s`;
  }, 1000);
}

function stopElapsed() {
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
}

function showError(message) {
  els.errorMessage.textContent = message;
  show(els.errorRegion);
}

/* ---------- Minimal markdown -> HTML (headings, paragraphs, bold,
 *           italic, inline code, lists, horizontal rules).
 *           Source comes from our own agents — not arbitrary user input.
 */

function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inlineMd(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
  return s;
}

function renderMarkdown(md) {
  const lines = (md || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;

  const flushParagraph = (buf) => {
    const text = buf.join(' ').trim();
    if (text) out.push(`<p>${inlineMd(text)}</p>`);
  };

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*$/.test(line)) {
      i++;
      continue;
    }
    if (/^\s*---+\s*$/.test(line)) {
      out.push('<hr />');
      i++;
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      out.push(`<h${level}>${inlineMd(h[2].trim())}</h${level}>`);
      i++;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i++;
      }
      out.push('<ul>' + items.map((it) => `<li>${inlineMd(it)}</li>`).join('') + '</ul>');
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
        i++;
      }
      out.push('<ol>' + items.map((it) => `<li>${inlineMd(it)}</li>`).join('') + '</ol>');
      continue;
    }

    const buf = [];
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^(#{1,4})\s+/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^\s*---+\s*$/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i++;
    }
    flushParagraph(buf);
  }

  return out.join('\n');
}

/* ---------- Render results ---------- */

function renderResults(result) {
  currentResult = result;
  document.querySelector('[data-field="artifact_folder"]').textContent = result.artifact_folder;
  document.querySelector('[data-field="research_summary"]').innerHTML = renderMarkdown(result.research_summary);
  document.querySelector('[data-field="business_document"]').innerHTML = renderMarkdown(result.business_document);
  document.querySelector('[data-field="slide_outline"]').innerHTML = renderMarkdown(result.slide_outline);
  document.querySelector('[data-field="draft_email"]').innerHTML = renderMarkdown(result.draft_email);
  // Toggle which mode's result cards are visible
  if (els.resultsBusiness) els.resultsBusiness.classList.remove('hidden');
  if (els.resultsSocial) els.resultsSocial.classList.add('hidden');
  resetEmailStatus();
  resetActionsStatus();
  show(els.resultsRegion);
  els.resultsRegion.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- Artifact actions (open / export) ----------
 *
 * Each per-card action button writes its feedback to a status span sitting
 * directly inside the same .card-actions-row. The global Actions card has
 * its own #actions-status that handles its three global buttons.
 *
 * Earlier the per-card buttons wrote to #actions-status too, which is at
 * the top of the results region — clicking Export DOCX on the Business
 * Document card produced a success message off-screen, looking like a
 * silent failure. The new per-card status fixes that.
 */

function writeStatus(el, text, kind) {
  if (!el) return;
  el.textContent = text || '';
  // Preserve the base class (.actions-status or .card-action-status) but
  // strip our two state modifiers before reapplying.
  el.classList.remove('is-ok', 'is-err');
  if (kind === 'ok') el.classList.add('is-ok');
  if (kind === 'err') el.classList.add('is-err');
}

function setActionsStatus(text, kind) {
  writeStatus(els.actionsStatus, text, kind);
}

function resetActionsStatus() {
  setActionsStatus('');
  document.querySelectorAll('.card-action-status').forEach((el) => writeStatus(el, ''));
  if (els.actionDriveLink) {
    els.actionDriveLink.classList.add('hidden');
    els.actionDriveLink.removeAttribute('href');
  }
}

function statusForButton(btn) {
  // The per-card action row contains the button and the status span as
  // siblings; the global Actions card's buttons are siblings of #actions-status
  // inside .actions-row -> .actions-card. Look upward for either container.
  const row = btn.closest('.card-actions-row');
  if (row) {
    const span = row.querySelector('.card-action-status');
    if (span) return span;
  }
  // Fall back to the global status (used for the Actions card's own buttons).
  return els.actionsStatus;
}

async function postJson(endpoint, body) {
  const res = await fetch(`${BACKEND}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function debugLog(...args) {
  // Console-only, never user-facing. No secrets ever passed.
  // Visible in DevTools (View > Toggle Developer Tools in Electron).
  // eslint-disable-next-line no-console
  console.log('[ridian]', ...args);
}

async function runAction({ statusEl, label, endpoint, body, success }) {
  if (!currentResult || !currentResult.artifact_folder) {
    writeStatus(statusEl, 'Run a workflow before exporting.', 'err');
    debugLog('action.skip', { endpoint, reason: 'no-currentResult' });
    return;
  }
  writeStatus(statusEl, `${label}…`);
  debugLog('action.start', { endpoint, body });
  try {
    const data = await postJson(endpoint, body);
    debugLog('action.ok', { endpoint, data });
    writeStatus(statusEl, success(data), 'ok');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    debugLog('action.fail', { endpoint, error: msg });
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      setBackendStatus(false);
      writeStatus(statusEl, 'Backend is not reachable. Start the FastAPI server first.', 'err');
    } else {
      writeStatus(statusEl, msg, 'err');
    }
  }
}

// --- global Actions card buttons ---

function openArtifactFolderAction() {
  return runAction({
    statusEl: els.actionsStatus,
    label: 'Opening folder',
    endpoint: '/artifacts/open-folder',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `Opened ${data.path}.`,
  });
}

async function copyArtifactFolderAction() {
  if (!currentResult || !currentResult.artifact_folder) {
    setActionsStatus('Run a workflow before copying.', 'err');
    return;
  }
  const ok = await copyToClipboard(currentResult.artifact_folder);
  setActionsStatus(
    ok ? 'Folder path copied to clipboard.' : 'Could not copy folder path.',
    ok ? 'ok' : 'err'
  );
}

function exportZipAction() {
  return runAction({
    statusEl: els.actionsStatus,
    label: 'Building ZIP',
    endpoint: '/artifacts/export-zip',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `ZIP saved: ${data.zip_path}`,
  });
}

// --- per-card buttons (dispatched from handleCardAction) ---

function openFolderFromCard(statusEl) {
  return runAction({
    statusEl,
    label: 'Opening folder',
    endpoint: '/artifacts/open-folder',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `Opened ${data.path}.`,
  });
}

function openFileFromCard(statusEl, filename) {
  return runAction({
    statusEl,
    label: `Opening ${filename}`,
    endpoint: '/artifacts/open-file',
    body: {
      artifact_folder: currentResult && currentResult.artifact_folder,
      filename,
    },
    success: (data) => `Opened ${data.path}.`,
  });
}

function exportDocxFromCard(statusEl) {
  return runAction({
    statusEl,
    label: 'Exporting DOCX',
    endpoint: '/artifacts/export-docx',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `DOCX exported: ${data.docx_path}`,
  });
}

function exportPptxFromCard(statusEl) {
  return runAction({
    statusEl,
    label: 'Exporting PPTX',
    endpoint: '/artifacts/export-pptx',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `PPTX exported: ${data.pptx_path}`,
  });
}

function handleCardAction(e) {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const action = btn.getAttribute('data-action');
  const statusEl = statusForButton(btn);
  debugLog('card.click', { action, hasResult: !!(currentResult && currentResult.artifact_folder) });

  if (action === 'open-folder') {
    openFolderFromCard(statusEl);
  } else if (action === 'open-file') {
    const filename = btn.getAttribute('data-filename');
    if (filename) openFileFromCard(statusEl, filename);
  } else if (action === 'export-docx') {
    exportDocxFromCard(statusEl);
  } else if (action === 'export-pptx') {
    exportPptxFromCard(statusEl);
  } else {
    debugLog('card.click.unknown', { action });
  }
}

/* ---------- Approve & send email ---------- */

function resetEmailStatus() {
  if (!els.sendEmailStatus) return;
  els.sendEmailStatus.textContent = '';
  els.sendEmailStatus.className = 'email-status';
}

function parseDraftEmail(raw) {
  // The email agent emits:
  //   Subject: ...
  //   <blank>
  //   <body lines>
  // If the first line isn't "Subject: ...", treat the whole thing as the body.
  const lines = (raw || '').split(/\r?\n/);
  if (lines.length && /^subject:\s*/i.test(lines[0])) {
    const subject = lines[0].replace(/^subject:\s*/i, '').trim();
    let i = 1;
    while (i < lines.length && lines[i].trim() === '') i++;
    const body = lines.slice(i).join('\n').trim();
    return { subject, body };
  }
  return { subject: '', body: (raw || '').trim() };
}

async function sendApprovedEmail() {
  if (!currentResult || !currentResult.draft_email) return;

  const ok = window.confirm('Send this generated email to your configured email address?');
  if (!ok) return;

  const { subject, body } = parseDraftEmail(currentResult.draft_email);
  if (!body) {
    els.sendEmailStatus.className = 'email-status is-err';
    els.sendEmailStatus.textContent = 'Email body is empty — nothing to send.';
    return;
  }
  const finalSubject = subject || DEFAULT_EMAIL_SUBJECT;

  els.sendEmailBtn.disabled = true;
  els.sendEmailStatus.className = 'email-status';
  els.sendEmailStatus.textContent = 'Sending…';

  try {
    const res = await fetch(`${BACKEND}/email/send-approved`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subject: finalSubject, body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(msg);
    }
    els.sendEmailStatus.className = 'email-status is-ok';
    const to = data && data.to_email ? ` to ${data.to_email}` : '';
    els.sendEmailStatus.textContent = `Email sent successfully${to}.`;
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    els.sendEmailStatus.className = 'email-status is-err';
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      els.sendEmailStatus.textContent = 'Backend is not reachable. Start the FastAPI server first.';
      setBackendStatus(false);
    } else {
      els.sendEmailStatus.textContent = msg;
    }
  } finally {
    els.sendEmailBtn.disabled = false;
  }
}

/* ---------- Copy ---------- */

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
    return ok;
  }
}

function wireCopyButtons() {
  document.querySelectorAll('.copy-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!currentResult) return;
      const key = btn.getAttribute('data-target');
      const value = currentResult[key];
      if (typeof value !== 'string') return;
      const ok = await copyToClipboard(value);
      const original = btn.textContent;
      btn.textContent = ok ? 'Copied' : 'Copy failed';
      btn.classList.toggle('is-copied', ok);
      setTimeout(() => {
        btn.textContent = original;
        btn.classList.remove('is-copied');
      }, 1400);
    });
  });
}

/* ---------- Backend health polling ---------- */

let backendUp = null; // null = unknown, true/false once known
let openaiKeyConfigured = null;

function setBackendStatus(up) {
  if (backendUp === up) return;
  backendUp = up;
  els.backendPill.classList.remove('is-up', 'is-down', 'is-unknown');
  if (up) {
    els.backendPill.classList.add('is-up');
    els.backendLabel.textContent = 'Backend online';
    hide(els.backendDownBanner);
  } else {
    els.backendPill.classList.add('is-down');
    els.backendLabel.textContent = 'Backend offline';
    show(els.backendDownBanner);
  }
}

function setOpenAIKeyState(configured) {
  if (openaiKeyConfigured === configured) return;
  openaiKeyConfigured = configured;
  if (configured) {
    hide(els.openaiMissingBanner);
    els.runBtn.disabled = false;
    els.runBtn.title = '';
  } else {
    show(els.openaiMissingBanner);
    els.runBtn.disabled = true;
    els.runBtn.title = 'Configure your OpenAI API key in Settings first.';
  }
}

async function pollHealth() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 2500);
    const res = await fetch(`${BACKEND}/health`, { signal: ctrl.signal });
    clearTimeout(t);
    setBackendStatus(res.ok);
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      setOpenAIKeyState(!!data.openai_key_loaded);
    }
  } catch (_) {
    setBackendStatus(false);
    // If we can't reach the backend we don't know the key state — leave the
    // run button as-is, the backend-down banner already explains the issue.
  }
}

function startHealthPolling() {
  pollHealth();
  setInterval(pollHealth, 5000);
}

/* ---------- Run workflow ---------- */

async function runWorkflow() {
  const task = els.taskInput.value.trim();
  if (task.length < 10) {
    showError('Please describe the task in at least 10 characters before running the workflow.');
    return;
  }

  if (backendUp === false) {
    showError('Backend is not running. Start the FastAPI server first.');
    return;
  }

  if (openaiKeyConfigured === false) {
    showError('OpenAI API key is not configured. Open Settings to add your key before running workflows.');
    return;
  }

  hide(els.errorRegion);
  hide(els.resultsRegion);
  RESULT_FIELDS.forEach((f) => {
    const el = document.querySelector(`[data-field="${f}"]`);
    if (el) el.textContent = '';
  });
  resetEmailStatus();
  resetActionsStatus();

  setRunning(true);
  setRunningStatusForMode('business');
  show(els.status);
  startElapsed();

  try {
    const res = await fetch(`${BACKEND}/workflows/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task }),
    });

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        if (j && j.detail) detail = j.detail;
      } catch (_) {}
      throw new Error(detail);
    }

    const data = await res.json();
    renderResults(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      showError('Backend is not running. Start the FastAPI server first.');
      setBackendStatus(false);
    } else {
      showError(msg);
    }
  } finally {
    setRunning(false);
    hide(els.status);
    stopElapsed();
  }
}

/* ---------- Clear / example ---------- */

function clearAll() {
  els.taskInput.value = '';
  hide(els.errorRegion);
  hide(els.resultsRegion);
  hide(els.status);
  resetEmailStatus();
  resetActionsStatus();
  currentResult = null;
  els.taskInput.focus();
}

function fillExample() {
  els.taskInput.value = EXAMPLE_TASK;
  els.taskInput.focus();
}

/* ---------- Google Drive (status + connect + disconnect + upload) ---------- */

let googleConnected = null;        // null = unknown, true/false once known
let googleConnectedEmail = null;

function setGoogleStatusUI(state) {
  // state: { connected: bool, email: string|null, error?: string }
  if (!els.googleStatusLabel) return;
  googleConnected = !!state.connected;
  googleConnectedEmail = state.email || null;

  els.googleStatusLabel.classList.remove('is-connected', 'is-disconnected', 'is-err');

  if (state.error) {
    els.googleStatusLabel.classList.add('is-err');
    els.googleStatusLabel.textContent = state.error;
  } else if (state.connected) {
    els.googleStatusLabel.classList.add('is-connected');
    els.googleStatusLabel.textContent = state.email
      ? `Connected as ${state.email}`
      : 'Connected';
  } else {
    els.googleStatusLabel.classList.add('is-disconnected');
    els.googleStatusLabel.textContent = 'Not connected';
  }

  if (els.googleConnectBtn) {
    els.googleConnectBtn.disabled = !!state.busy;
    els.googleConnectBtn.textContent = state.busy
      ? 'Waiting for sign-in…'
      : (state.connected ? 'Reconnect Google Drive' : 'Connect Google Drive');
  }
  if (els.googleDisconnectBtn) {
    els.googleDisconnectBtn.disabled = !state.connected || !!state.busy;
  }
}

async function loadGoogleStatus() {
  setGoogleStatusUI({ connected: false, email: null, error: 'Checking…' });
  try {
    const res = await fetch(`${BACKEND}/google/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    setGoogleStatusUI(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setGoogleStatusUI({
      connected: false,
      email: null,
      error: /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
        ? 'Backend not reachable.'
        : `Status error: ${msg}`,
    });
  }
}

async function connectGoogleDrive() {
  // Sets a busy state — the call may block until the user finishes consent
  // in their browser. The backend runs the flow via asyncio.to_thread so
  // other endpoints stay responsive while we wait.
  setGoogleStatusUI({ connected: googleConnected, email: googleConnectedEmail, busy: true });
  setSettingsStatus('A browser tab opened for Google sign-in. Complete it to continue…');
  debugLog('google.connect.start');
  try {
    const res = await fetch(`${BACKEND}/google/connect`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    setGoogleStatusUI(data);
    setSettingsStatus(
      data.connected ? `Connected as ${data.email || 'your Google account'}.` : 'Connect did not complete.',
      data.connected ? 'ok' : 'err'
    );
    debugLog('google.connect.ok', { connected: data.connected });
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    debugLog('google.connect.fail', { error: msg });
    setGoogleStatusUI({ connected: false, email: null });
    setSettingsStatus(
      /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
        ? 'Backend is not reachable. Start the FastAPI server first.'
        : msg,
      'err'
    );
  }
}

async function disconnectGoogleDrive() {
  const ok = window.confirm('Disconnect Google Drive? The saved token will be deleted from this machine.');
  if (!ok) return;
  setGoogleStatusUI({ connected: googleConnected, email: googleConnectedEmail, busy: true });
  try {
    const res = await fetch(`${BACKEND}/google/disconnect`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setGoogleStatusUI(data);
    setSettingsStatus('Disconnected from Google Drive.', 'ok');
    debugLog('google.disconnect.ok');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    debugLog('google.disconnect.fail', { error: msg });
    setSettingsStatus(`Disconnect failed: ${msg}`, 'err');
    // Re-poll real status so the UI reflects reality.
    loadGoogleStatus();
  }
}

async function uploadArtifactsToDrive() {
  if (!currentResult || !currentResult.artifact_folder) {
    setActionsStatus('Run a workflow before uploading to Drive.', 'err');
    return;
  }
  if (googleConnected === false) {
    setActionsStatus('Google Drive is not connected. Open Settings to connect.', 'err');
    return;
  }
  // If we genuinely don't know yet, do a quick fresh status check.
  if (googleConnected === null) {
    await loadGoogleStatus();
    if (googleConnected === false) {
      setActionsStatus('Google Drive is not connected. Open Settings to connect.', 'err');
      return;
    }
  }

  const ok = window.confirm("Upload this workflow's artifact folder to your connected Google Drive?");
  if (!ok) return;

  if (els.actionDriveLink) {
    els.actionDriveLink.classList.add('hidden');
    els.actionDriveLink.removeAttribute('href');
  }
  setActionsStatus('Uploading to Google Drive…');
  if (els.actionUploadDrive) els.actionUploadDrive.disabled = true;
  debugLog('google.upload.start', { folder: currentResult.artifact_folder });

  try {
    const res = await fetch(`${BACKEND}/google/upload-artifacts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: currentResult.artifact_folder }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    const count = (data.uploaded_files || []).length;
    setActionsStatus(
      `Uploaded to Google Drive: ${count} file${count === 1 ? '' : 's'} in folder "${data.drive_folder_name}".`,
      'ok'
    );
    if (els.actionDriveLink && data.drive_folder_url) {
      els.actionDriveLink.href = data.drive_folder_url;
      els.actionDriveLink.textContent = 'Open Drive folder';
      els.actionDriveLink.classList.remove('hidden');
    }
    debugLog('google.upload.ok', { count, folder_name: data.drive_folder_name });
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    debugLog('google.upload.fail', { error: msg });
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      setBackendStatus(false);
      setActionsStatus('Backend is not reachable. Start the FastAPI server first.', 'err');
    } else {
      setActionsStatus(msg, 'err');
    }
  } finally {
    if (els.actionUploadDrive) els.actionUploadDrive.disabled = false;
  }
}

/* ---------- Settings modal ---------- */

const SETTINGS_FIELDS = [
  'operator_name',
  'operator_email',
  'default_to_email',
  'company_name',
  'openai_model',
  'smtp_host',
  'smtp_port',
  'smtp_username',
  'smtp_from_email',
];

// Secret fields use the preserve-on-blank convention: blank submission
// keeps the saved server-side value. Names here must match the form
// input `name` attributes AND the backend's `*_configured` flags
// (without the `_configured` suffix).
const SETTINGS_SECRET_FIELDS = ['openai_api_key', 'smtp_password'];

function setSettingsStatus(text, kind) {
  if (!els.settingsStatus) return;
  els.settingsStatus.textContent = text || '';
  els.settingsStatus.className = 'modal-status';
  if (kind === 'ok') els.settingsStatus.classList.add('is-ok');
  if (kind === 'err') els.settingsStatus.classList.add('is-err');
}

function applySettingsToForm(settings) {
  if (!els.settingsForm) return;
  SETTINGS_FIELDS.forEach((name) => {
    const input = els.settingsForm.elements.namedItem(name);
    if (input) input.value = settings[name] || '';
  });
  // Secrets are never returned from the server. Always leave the inputs blank.
  SETTINGS_SECRET_FIELDS.forEach((name) => {
    const input = els.settingsForm.elements.namedItem(name);
    if (input) input.value = '';
  });

  if (els.settingsOpenaiKeyHint) {
    if (settings.openai_api_key_configured) {
      els.settingsOpenaiKeyHint.className = 'field-hint is-ok';
      els.settingsOpenaiKeyHint.textContent =
        'An OpenAI API key is currently saved. Leave blank to keep it; type a new one to replace it.';
    } else {
      els.settingsOpenaiKeyHint.className = 'field-hint';
      els.settingsOpenaiKeyHint.textContent =
        'Paste your OpenAI API key here. Get one at platform.openai.com/api-keys.';
    }
  }

  if (els.settingsPasswordHint) {
    if (settings.smtp_password_configured) {
      els.settingsPasswordHint.className = 'field-hint is-ok';
      els.settingsPasswordHint.textContent =
        'A password is currently saved. Leave blank to keep it; type a new one to replace it.';
    } else {
      els.settingsPasswordHint.className = 'field-hint';
      els.settingsPasswordHint.textContent =
        'No password saved yet. For Gmail use an App Password.';
    }
  }

  if (els.settingsOutputsPath) {
    els.settingsOutputsPath.textContent = settings.outputs_path || '—';
  }
}

async function loadSettingsIntoForm() {
  setSettingsStatus('Loading…');
  try {
    const res = await fetch(`${BACKEND}/settings`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cachedSettings = data;
    applySettingsToForm(data);
    setSettingsStatus('');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setSettingsStatus(
      /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
        ? 'Backend is not reachable. Start the FastAPI server first.'
        : `Could not load settings: ${msg}`,
      'err'
    );
  }
}

function openSettings() {
  if (!els.settingsModal) return;
  els.settingsModal.classList.remove('hidden');
  els.settingsModal.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', handleSettingsKeydown);
  loadSettingsIntoForm().then(() => {
    const first = els.settingsForm && els.settingsForm.elements.namedItem('operator_name');
    if (first && typeof first.focus === 'function') first.focus();
  });
  // Refresh Google connection state every time Settings opens so the user
  // always sees the current truth (e.g. after disconnecting elsewhere).
  loadGoogleStatus();
}

function closeSettings() {
  if (!els.settingsModal) return;
  els.settingsModal.classList.add('hidden');
  els.settingsModal.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', handleSettingsKeydown);
  setSettingsStatus('');
  if (els.settingsOpenBtn) els.settingsOpenBtn.focus();
}

function handleSettingsKeydown(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    closeSettings();
  }
}

async function saveSettings(e) {
  if (e) e.preventDefault();
  if (!els.settingsForm) return;

  const fd = new FormData(els.settingsForm);
  const payload = {};
  SETTINGS_FIELDS.forEach((name) => {
    payload[name] = (fd.get(name) || '').toString().trim();
  });
  // Secrets are sent only if the user typed something — otherwise the
  // backend preserves the previously-saved value.
  SETTINGS_SECRET_FIELDS.forEach((name) => {
    const v = (fd.get(name) || '').toString();
    if (v !== '') payload[name] = v;
  });

  els.settingsSaveBtn.disabled = true;
  setSettingsStatus('Saving…');

  try {
    const res = await fetch(`${BACKEND}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    cachedSettings = data;
    applySettingsToForm(data);
    // Refresh /health immediately so the openai-missing banner and the
    // Run Workflow button reflect the new state without waiting for the
    // 5-second poll.
    pollHealth();
    setSettingsStatus('Settings saved.', 'ok');
    setTimeout(() => {
      // Clear the green confirmation after a beat, but only if no newer status replaced it.
      if (els.settingsStatus && els.settingsStatus.textContent === 'Settings saved.') {
        setSettingsStatus('');
      }
    }, 2500);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setSettingsStatus(
      /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
        ? 'Backend is not reachable. Start the FastAPI server first.'
        : `Could not save: ${msg}`,
      'err'
    );
  } finally {
    els.settingsSaveBtn.disabled = false;
  }
}

async function testEmailSettings() {
  const ok = window.confirm(
    'Send a test email to your saved default recipient using the current SMTP settings?'
  );
  if (!ok) return;

  els.settingsTestEmailBtn.disabled = true;
  setSettingsStatus('Sending test email…');

  try {
    const res = await fetch(`${BACKEND}/email/send-approved`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject: 'Ridian Agency SMTP test',
        body:
          'This is a test email sent from the Ridian Agency Settings panel. ' +
          'If you received this, your SMTP credentials are working.\n\n' +
          '— Ridian Agency',
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    const to = data && data.to_email ? ` to ${data.to_email}` : '';
    setSettingsStatus(`Test email sent${to}.`, 'ok');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setSettingsStatus(
      /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
        ? 'Backend is not reachable. Start the FastAPI server first.'
        : msg,
      'err'
    );
  } finally {
    els.settingsTestEmailBtn.disabled = false;
  }
}

/* ---------- Prompt library ---------- */

function buildPromptLibrary() {
  const tabsEl = document.querySelector('.prompts-tabs');
  const panelsEl = document.querySelector('.prompts-panels');
  if (!tabsEl || !panelsEl) return;

  PROMPT_CATEGORIES.forEach((cat, i) => {
    const isFirst = i === 0;

    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'prompt-tab' + (isFirst ? ' is-active' : '');
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', isFirst ? 'true' : 'false');
    tab.setAttribute('data-cat', cat.id);
    tab.textContent = cat.label;
    tab.addEventListener('click', () => activatePromptCategory(cat.id));
    tabsEl.appendChild(tab);

    const panel = document.createElement('div');
    panel.className = 'prompts-grid' + (isFirst ? '' : ' hidden');
    panel.setAttribute('data-cat-panel', cat.id);
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-label', cat.label);

    cat.prompts.forEach((promptText) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'prompt-pill';
      pill.textContent = promptText;
      pill.addEventListener('click', () => fillTaskFromPrompt(promptText));
      panel.appendChild(pill);
    });

    panelsEl.appendChild(panel);
  });
}

function activatePromptCategory(id) {
  document.querySelectorAll('.prompt-tab').forEach((t) => {
    const active = t.getAttribute('data-cat') === id;
    t.classList.toggle('is-active', active);
    t.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-cat-panel]').forEach((p) => {
    p.classList.toggle('hidden', p.getAttribute('data-cat-panel') !== id);
  });
}

function fillTaskFromPrompt(text) {
  els.taskInput.value = text;
  els.taskInput.focus();
  // Place caret at the end so the user can immediately keep typing.
  els.taskInput.setSelectionRange(text.length, text.length);
  // Bring the task card into view; the prompt library sits above it.
  const card = els.taskInput.closest('.card') || els.taskInput;
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* ---------- Social Media Production ---------- */

// Each prompt carries a partial field-fill set so clicking it pre-configures
// the form for that channel/scenario. Fields not listed are left alone so
// users can keep an in-progress value.
const SOCIAL_PROMPT_CATEGORIES = [
  {
    id: 'open-gulf-tiktok',
    label: 'Open Gulf TikTok',
    prompts: [
      {
        text: 'Create a warm, cinematic TikTok explaining one practical AI productivity idea for everyday professionals.',
        fields: { channel: 'Open Gulf TikTok', starting_point: 'I have a topic', content_format: 'Short-form video', topic_notes: 'Practical AI productivity idea for everyday professionals. Cinematic, warm tone.' },
      },
      {
        text: 'Create a short Open Gulf TikTok about how AI is changing education without sounding alarmist or hype-driven.',
        fields: { channel: 'Open Gulf TikTok', starting_point: 'I have a topic', content_format: 'Short-form video', topic_notes: 'How AI is changing education. Grounded, calm, non-alarmist. Educator-friendly.' },
      },
      {
        text: 'Create a reflective TikTok script about why people feel overwhelmed by AI and how to start calmly.',
        fields: { channel: 'Open Gulf TikTok', starting_point: 'I have a topic', content_format: 'Short-form video', topic_notes: 'Why people feel overwhelmed by AI, and a calm starting point. Reflective.' },
      },
    ],
  },
  {
    id: 'open-gulf-youtube',
    label: 'Open Gulf YouTube',
    prompts: [
      {
        text: 'Create a long-form YouTube outline for Open Gulf explaining practical AI productivity for beginners.',
        fields: { channel: 'Open Gulf YouTube', starting_point: 'I have a topic', content_format: 'Long-form YouTube video', topic_notes: 'Practical AI productivity for beginners. Long-form outline.' },
      },
      {
        text: 'Create a YouTube tutorial structure showing how a small business owner can use AI to save time.',
        fields: { channel: 'Open Gulf YouTube', starting_point: 'I have a topic', content_format: 'Long-form YouTube video', topic_notes: 'Tutorial: how a small business owner can use AI to save time. Step-by-step.' },
      },
      {
        text: 'Create a long-form video plan about the future of learning, AI tutors, and human-centered education.',
        fields: { channel: 'Open Gulf YouTube', starting_point: 'I have a topic', content_format: 'Long-form YouTube video', topic_notes: 'The future of learning, AI tutors, and human-centered education. Long-form plan.' },
      },
    ],
  },
  {
    id: 'buns-tiktok',
    label: 'Buns TikTok',
    prompts: [
      {
        text: 'Create a funny TikTok concept for Buns, a black tuxedo cat with dramatic, charming, mischievous energy.',
        fields: { channel: 'Buns TikTok', starting_point: 'I have a topic', content_format: 'Short-form video', topic_notes: 'Funny TikTok concept for Buns, a black tuxedo cat. Dramatic, charming, mischievous energy.' },
      },
      {
        text: 'Create a cozy, wholesome Buns TikTok script based on a cat quietly watching the world from a window.',
        fields: { channel: 'Buns TikTok', starting_point: 'I have a topic', content_format: 'Short-form video', topic_notes: 'Cozy, wholesome Buns TikTok. Quietly watching the world from a window.' },
      },
      {
        text: 'Create a playful "Buns as tiny CEO" TikTok with voiceover, captions, and edit notes.',
        fields: { channel: 'Buns TikTok', starting_point: 'I have a topic', content_format: 'Short-form video', topic_notes: 'Buns as tiny CEO. Playful voiceover, captions, edit notes.' },
      },
    ],
  },
  {
    id: 'existing-footage',
    label: 'Existing Footage',
    prompts: [
      {
        text: 'Turn this existing clip description into a TikTok post package with hook, text overlays, voiceover, caption, and hashtags.',
        fields: { starting_point: 'I have existing footage or a thumbnail', content_format: 'Short-form video', media_notes: '(Describe your existing clip here — visuals, what is on screen, mood, any audio.)' },
      },
      {
        text: 'Analyze this thumbnail or video description and propose the best short-form angle.',
        fields: { starting_point: 'I have existing footage or a thumbnail', content_format: 'Repurposed clip', media_notes: '(Describe the thumbnail or video — composition, subject, mood.)' },
      },
      {
        text: 'Create a social post package from existing footage without generating new topic ideas.',
        fields: { starting_point: 'I have existing footage or a thumbnail', content_format: 'Short-form video', media_notes: '(Describe the footage in detail. The agent will not invent new topics — it works with what you have.)' },
      },
    ],
  },
  {
    id: 'weekly-planning',
    label: 'Weekly Planning',
    prompts: [
      {
        text: 'Create a 7-day content plan for Open Gulf TikTok focused on AI productivity, education, and human-centered technology.',
        fields: { channel: 'Open Gulf TikTok', starting_point: 'Generate ideas from scratch', content_format: 'Content calendar', output_depth: 'Weekly content plan', topic_notes: '7-day Open Gulf TikTok plan: AI productivity, education, human-centered technology.' },
      },
      {
        text: 'Create a 7-day content plan for Buns TikTok balancing funny, cozy, and personality-driven posts.',
        fields: { channel: 'Buns TikTok', starting_point: 'Generate ideas from scratch', content_format: 'Content calendar', output_depth: 'Weekly content plan', topic_notes: '7-day Buns TikTok plan: balance funny, cozy, personality-driven.' },
      },
      {
        text: 'Create a combined weekly posting plan for Open Gulf TikTok, Open Gulf YouTube, and Buns TikTok.',
        fields: { channel: 'Custom', starting_point: 'Generate ideas from scratch', content_format: 'Content calendar', output_depth: 'Weekly content plan', topic_notes: 'Combined weekly plan across Open Gulf TikTok, Open Gulf YouTube, and Buns TikTok.' },
      },
    ],
  },
];

const SOCIAL_FIELD_MAP = {
  channel: 'socialChannel',
  starting_point: 'socialStartingPoint',
  content_format: 'socialContentFormat',
  goal: 'socialGoal',
  output_depth: 'socialOutputDepth',
  media_notes: 'socialMediaNotes',
  topic_notes: 'socialTopicNotes',
};

function applySocialPromptFields(fields) {
  for (const [key, value] of Object.entries(fields)) {
    const elKey = SOCIAL_FIELD_MAP[key];
    const el = elKey && els[elKey];
    if (el) el.value = value;
  }
}

function buildSocialPromptLibrary() {
  const tabsEl = els.socialPromptsTabs;
  const panelsEl = els.socialPromptsPanels;
  if (!tabsEl || !panelsEl) return;

  SOCIAL_PROMPT_CATEGORIES.forEach((cat, i) => {
    const isFirst = i === 0;

    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'prompt-tab' + (isFirst ? ' is-active' : '');
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', isFirst ? 'true' : 'false');
    tab.setAttribute('data-social-cat', cat.id);
    tab.textContent = cat.label;
    tab.addEventListener('click', () => activateSocialCategory(cat.id));
    tabsEl.appendChild(tab);

    const panel = document.createElement('div');
    panel.className = 'prompts-grid' + (isFirst ? '' : ' hidden');
    panel.setAttribute('data-social-cat-panel', cat.id);
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-label', cat.label);

    cat.prompts.forEach((p) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'prompt-pill';
      pill.textContent = p.text;
      pill.addEventListener('click', () => {
        applySocialPromptFields(p.fields);
        if (els.socialTopicNotes) {
          els.socialTopicNotes.focus({ preventScroll: true });
        }
        const card = (els.socialTopicNotes || els.socialChannel).closest('.card');
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      panel.appendChild(pill);
    });

    panelsEl.appendChild(panel);
  });
}

function activateSocialCategory(id) {
  document.querySelectorAll('[data-social-cat]').forEach((t) => {
    const active = t.getAttribute('data-social-cat') === id;
    t.classList.toggle('is-active', active);
    t.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-social-cat-panel]').forEach((p) => {
    p.classList.toggle('hidden', p.getAttribute('data-social-cat-panel') !== id);
  });
}

const SOCIAL_RESULT_FIELDS = ['content_package', 'script', 'caption_package', 'posting_checklist'];

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-tab').forEach((t) => {
    const active = t.getAttribute('data-mode') === mode;
    t.classList.toggle('is-active', active);
    t.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  if (els.modeBusinessPanel) els.modeBusinessPanel.classList.toggle('hidden', mode !== 'business');
  if (els.modeSocialPanel) els.modeSocialPanel.classList.toggle('hidden', mode !== 'social');
  // Reset any in-flight running banner / errors when switching modes.
  hide(els.errorRegion);
}

function setRunningStatusForMode(mode) {
  if (!els.statusSub) return;
  if (mode === 'social') {
    els.statusSub.innerHTML =
      'This typically takes <strong>30&ndash;90 seconds</strong>. The social media agent builds a four-section package: content, script, caption, posting checklist.';
  } else {
    els.statusSub.innerHTML =
      'This typically takes <strong>60&ndash;90 seconds</strong>. The five agents run in sequence: research &rarr; writer &rarr; reviewer &rarr; presentation &rarr; email.';
  }
}

function renderSocialResults(result) {
  currentResult = result;
  document.querySelector('[data-field="artifact_folder"]').textContent = result.artifact_folder;
  document.querySelector('[data-field="content_package"]').innerHTML = renderMarkdown(result.content_package);
  document.querySelector('[data-field="script"]').innerHTML = renderMarkdown(result.script);
  document.querySelector('[data-field="caption_package"]').innerHTML = renderMarkdown(result.caption_package);
  document.querySelector('[data-field="posting_checklist"]').innerHTML = renderMarkdown(result.posting_checklist);
  // Toggle which mode's result cards are visible
  if (els.resultsBusiness) els.resultsBusiness.classList.add('hidden');
  if (els.resultsSocial) els.resultsSocial.classList.remove('hidden');
  resetEmailStatus();
  resetActionsStatus();
  show(els.resultsRegion);
  els.resultsRegion.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function runSocialWorkflow() {
  const payload = {
    channel: (els.socialChannel && els.socialChannel.value) || '',
    starting_point: (els.socialStartingPoint && els.socialStartingPoint.value) || '',
    content_format: (els.socialContentFormat && els.socialContentFormat.value) || '',
    media_notes: (els.socialMediaNotes && els.socialMediaNotes.value) || '',
    topic_notes: (els.socialTopicNotes && els.socialTopicNotes.value) || '',
    goal: (els.socialGoal && els.socialGoal.value) || '',
    output_depth: (els.socialOutputDepth && els.socialOutputDepth.value) || '',
  };

  if (!payload.channel) {
    showError('Choose a Channel / Brand before running the social workflow.');
    return;
  }
  if (backendUp === false) {
    showError('Backend is not running. Start the FastAPI server first.');
    return;
  }
  if (openaiKeyConfigured === false) {
    showError('OpenAI API key is not configured. Open Settings to add your key before running workflows.');
    return;
  }

  hide(els.errorRegion);
  hide(els.resultsRegion);
  SOCIAL_RESULT_FIELDS.forEach((f) => {
    const el = document.querySelector(`[data-field="${f}"]`);
    if (el) el.textContent = '';
  });
  resetEmailStatus();
  resetActionsStatus();

  els.socialRunBtn.disabled = true;
  els.socialRunBtn.textContent = 'Running…';
  els.socialClearBtn.disabled = true;
  setRunningStatusForMode('social');
  show(els.status);
  startElapsed();

  try {
    const res = await fetch(`${BACKEND}/workflows/social-media/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    renderSocialResults(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      showError('Backend is not reachable. Start the FastAPI server first.');
      setBackendStatus(false);
    } else {
      showError(msg);
    }
  } finally {
    els.socialRunBtn.disabled = false;
    els.socialRunBtn.textContent = 'Run social workflow';
    els.socialClearBtn.disabled = false;
    hide(els.status);
    stopElapsed();
  }
}

function clearSocialForm() {
  if (els.socialMediaNotes) els.socialMediaNotes.value = '';
  if (els.socialTopicNotes) els.socialTopicNotes.value = '';
  if (els.socialChannel) els.socialChannel.value = 'Open Gulf TikTok';
  if (els.socialStartingPoint) els.socialStartingPoint.value = 'I have a topic';
  if (els.socialContentFormat) els.socialContentFormat.value = 'Short-form video';
  if (els.socialGoal) els.socialGoal.value = 'Educate';
  if (els.socialOutputDepth) els.socialOutputDepth.value = 'Quick post package';
  hide(els.errorRegion);
  hide(els.resultsRegion);
  hide(els.status);
  resetEmailStatus();
  resetActionsStatus();
  currentResult = null;
}

/* ---------- Wire up ---------- */

els.runBtn.addEventListener('click', runWorkflow);
els.clearBtn.addEventListener('click', clearAll);
els.exampleBtn.addEventListener('click', fillExample);

els.taskInput.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    runWorkflow();
  }
});

if (els.sendEmailBtn) {
  els.sendEmailBtn.addEventListener('click', sendApprovedEmail);
}

if (els.actionOpenFolder) els.actionOpenFolder.addEventListener('click', openArtifactFolderAction);
if (els.actionCopyFolder) els.actionCopyFolder.addEventListener('click', copyArtifactFolderAction);
if (els.actionExportZip) els.actionExportZip.addEventListener('click', exportZipAction);
if (els.actionUploadDrive) els.actionUploadDrive.addEventListener('click', uploadArtifactsToDrive);

if (els.googleConnectBtn) els.googleConnectBtn.addEventListener('click', connectGoogleDrive);
if (els.googleDisconnectBtn) els.googleDisconnectBtn.addEventListener('click', disconnectGoogleDrive);

// Light initial probe so the Upload button knows whether to allow the click
// before the user opens Settings. Errors are tolerated — the click handler
// re-probes if state is unknown.
loadGoogleStatus();

// Delegate per-card action buttons (Open markdown / Open folder / Export DOCX-PPTX)
if (els.resultsRegion) {
  els.resultsRegion.addEventListener('click', handleCardAction);
}

if (els.settingsOpenBtn) {
  els.settingsOpenBtn.addEventListener('click', openSettings);
}
if (els.settingsCloseBtn) {
  els.settingsCloseBtn.addEventListener('click', closeSettings);
}
if (els.settingsCancelBtn) {
  els.settingsCancelBtn.addEventListener('click', closeSettings);
}
if (els.settingsForm) {
  els.settingsForm.addEventListener('submit', saveSettings);
}
if (els.settingsTestEmailBtn) {
  els.settingsTestEmailBtn.addEventListener('click', testEmailSettings);
}
if (els.settingsModal) {
  // Click on the dim backdrop closes the modal; clicks inside the card don't.
  els.settingsModal.addEventListener('click', (e) => {
    if (e.target === els.settingsModal) closeSettings();
  });
}

buildPromptLibrary();
buildSocialPromptLibrary();

// Mode tabs
document.querySelectorAll('.mode-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    const mode = tab.getAttribute('data-mode');
    if (mode === 'business' || mode === 'social') setMode(mode);
  });
});

if (els.socialRunBtn) els.socialRunBtn.addEventListener('click', runSocialWorkflow);
if (els.socialClearBtn) els.socialClearBtn.addEventListener('click', clearSocialForm);

// Ctrl/Cmd+Enter inside the social topic textarea runs the social workflow.
if (els.socialTopicNotes) {
  els.socialTopicNotes.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      runSocialWorkflow();
    }
  });
}

wireCopyButtons();
startHealthPolling();
