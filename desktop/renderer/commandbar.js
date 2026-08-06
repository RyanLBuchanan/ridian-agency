/* Compact global command bar (v6.0 Phase 7).
 *
 * Opened by the global hotkey from anywhere in Windows. A command typed OR
 * spoken here starts a run in the main window and brings it up. Dictation
 * uses the SAME Whisper path as the composer mic (POST /operations/transcribe).
 */

const BACKEND = (window.ridianBar && window.ridianBar.backendOrigin) || 'http://127.0.0.1:8000';
const input = document.getElementById('cb-input');
const micBtn = document.getElementById('cb-mic');
const statusEl = document.getElementById('cb-status');

function setStatus(text, kind) {
  statusEl.textContent = text || '';
  statusEl.className = `bar-status${kind === 'err' ? ' is-err' : ''}`;
}

function submit() {
  const text = (input.value || '').trim();
  if (!text) { setStatus('Type or dictate a command first.', 'err'); return; }
  window.ridianBar.submit(text);      // main shows the window and forwards it
  input.value = '';
  setStatus('');
}

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  if (e.key === 'Escape') { e.preventDefault(); window.ridianBar.close(); }
});

// Focus is granted by main on every show; this covers the first paint.
window.addEventListener('DOMContentLoaded', () => input.focus());
window.ridianBar.onShown(() => { input.value = ''; setStatus(''); input.focus(); });

/* ----- Push-to-talk dictation (hold the mic, release to transcribe) ----- */
const mic = { recorder: null, chunks: [], stream: null };

async function startRecording() {
  if (mic.recorder) return;
  try {
    mic.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (_err) {
    setStatus('Microphone unavailable — check Windows mic permissions.', 'err');
    return;
  }
  mic.chunks = [];
  const recorder = new MediaRecorder(mic.stream, { mimeType: 'audio/webm' });
  mic.recorder = recorder;
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) mic.chunks.push(e.data); };
  recorder.onstop = async () => {
    micBtn.classList.remove('is-recording');
    (mic.stream.getTracks() || []).forEach((t) => t.stop());
    const blob = new Blob(mic.chunks, { type: 'audio/webm' });
    mic.recorder = null; mic.chunks = []; mic.stream = null;
    if (blob.size < 200) { setStatus('Too short — hold the mic and speak.', 'err'); return; }
    setStatus('Transcribing…');
    try {
      const buf = await blob.arrayBuffer();
      let binary = '';
      new Uint8Array(buf).forEach((b) => { binary += String.fromCharCode(b); });
      const res = await fetch(`${BACKEND}/operations/transcribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ audio_base64: btoa(binary), mime: 'audio/webm' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
      const text = (data.text || '').trim();
      if (!text) { setStatus("Didn't catch that — try again.", 'err'); return; }
      input.value = input.value ? `${input.value} ${text}` : text;
      setStatus('Transcribed — Enter to run.');
      input.focus();
    } catch (err) {
      setStatus(`Transcription failed: ${err && err.message ? err.message : err}`, 'err');
    }
  };
  recorder.start();
  micBtn.classList.add('is-recording');
  setStatus('Listening… release to transcribe.');
}

function stopRecording() {
  if (mic.recorder && mic.recorder.state === 'recording') mic.recorder.stop();
}

micBtn.addEventListener('pointerdown', (e) => { e.preventDefault(); startRecording(); });
micBtn.addEventListener('pointerup', stopRecording);
micBtn.addEventListener('pointerleave', stopRecording);
micBtn.addEventListener('pointercancel', stopRecording);
