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
};

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
  show(els.resultsRegion);
  els.resultsRegion.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

async function pollHealth() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 2500);
    const res = await fetch(`${BACKEND}/health`, { signal: ctrl.signal });
    clearTimeout(t);
    setBackendStatus(res.ok);
  } catch (_) {
    setBackendStatus(false);
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

  hide(els.errorRegion);
  hide(els.resultsRegion);
  RESULT_FIELDS.forEach((f) => {
    const el = document.querySelector(`[data-field="${f}"]`);
    if (el) el.textContent = '';
  });

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
  currentResult = null;
  els.taskInput.focus();
}

function fillExample() {
  els.taskInput.value = EXAMPLE_TASK;
  els.taskInput.focus();
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

wireCopyButtons();
startHealthPolling();
