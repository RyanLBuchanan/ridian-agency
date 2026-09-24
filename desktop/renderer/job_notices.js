/* Ridian Operator — which Owner Workspace job notices are new (v7.5).
 *
 * The backend keeps a numbered notice feed per process (jobs_service:
 * "claimed" when a job run starts, "parked" when it waits on a gate approval
 * or a question, "expired" when a parked run could not continue) and
 * serves it at GET /owner-workspace/jobs/notices?after=&
 * epoch=. The window polls it. This module is the only place that decides
 * what to show, so a notification fires ONCE per notice however often the
 * window polls, reloads, or the backend re-answers:
 *   - a notice is new only if its seq is above the last seq this window saw
 *     from the SAME backend process (epoch);
 *   - a new epoch (the backend restarted) starts from zero, because the
 *     numbering restarted with it.
 * Pure functions; no DOM, no network. Pinned by desktop/scripts/
 * check_job_notices.js and the "Job visibility (real DOM)" harness section.
 */
(function (root) {
  'use strict';

  const COMMAND_CHARS = 90;

  function initialState() {
    return { epoch: '', lastSeq: 0 };
  }

  /** { notices: the new ones in order, state: what to remember next }. */
  function fresh(state, response) {
    const prev = state && typeof state === 'object' ? state : initialState();
    if (!response || typeof response !== 'object' || !Array.isArray(response.notices)) {
      return { notices: [], state: prev };
    }
    const epoch = String(response.epoch || '');
    const floor = epoch && epoch === prev.epoch ? Number(prev.lastSeq) || 0 : 0;
    const notices = response.notices
      .filter((n) => n && Number.isInteger(n.seq) && n.seq > floor)
      .sort((a, b) => a.seq - b.seq);
    const lastSeq = notices.reduce((max, n) => Math.max(max, n.seq), floor);
    return { notices, state: { epoch, lastSeq } };
  }

  function shorten(text) {
    const line = String(text || '').split(/\r?\n/)[0].trim() || 'a command';
    return line.length > COMMAND_CHARS ? line.slice(0, COMMAND_CHARS - 1).trimEnd() + '…' : line;
  }

  /** The notification text for a notice ("" for an unknown kind). */
  function text(notice) {
    if (!notice) return '';
    if (notice.kind === 'claimed') return `Ridian is working on: ${shorten(notice.command)}`;
    if (notice.kind === 'parked') return `Ridian needs you: ${shorten(notice.command)}`;
    if (notice.kind === 'expired') return `Ridian couldn't continue: ${shorten(notice.command)}`;
    return '';
  }

  /** Which sidebar badge a notice raises: 'approvals', 'waiting', or ''. */
  function badge(notice) {
    if (!notice || notice.kind !== 'parked') return '';
    return notice.park === 'approval' ? 'approvals' : 'waiting';
  }

  const api = { initialState, fresh, text, badge, shorten };
  if (typeof module === 'object' && module && module.exports) module.exports = api;
  root.RidianJobNotices = api;
})(typeof window !== 'undefined' ? window : globalThis);
