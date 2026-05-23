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
};

let cachedSettings = null;

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
  resetEmailStatus();
  show(els.resultsRegion);
  els.resultsRegion.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

  setRunning(true);
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
  currentResult = null;
  els.taskInput.focus();
}

function fillExample() {
  els.taskInput.value = EXAMPLE_TASK;
  els.taskInput.focus();
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
wireCopyButtons();
startHealthPolling();
