"""Ridian Jobs v1, Operator side (v7.3) — run what the owner sends from the Owner Workspace.

THE CONTRACT is ridian-technologies-site docs/ridian-jobs-v1.md (the site
side, live). This side is described in docs/ridian-jobs-v1-operator.md.

WHAT IT DOES. While this PC is connected to the Owner Workspace (the same
device token sync_service holds), it asks the site for work: POST
<site>/api/jobs/claim every 15 s. A claimed job's command runs EXACTLY as if
it were typed in the command bar — operator_service.run_operation, the same
planner, tools, approval gates, cost ceilings and recipient allowlists. The
only difference is a stamp on the operation record: source
"owner-workspace" and the job id, which the sidebar shows as "From Owner
Workspace". Progress is reported back (running, awaiting_approval, running
again after the approval is answered on this PC or the phone), then the
allowlisted result.

THE RULES, each pinned by tests/test_owner_jobs.py:

  1. One job at a time: nothing is claimed while a job is non-terminal here.
  2. Claim only while connected. 403 (the owner has not allowed this PC to
     run commands) slows polling to every 5 minutes and says so in
     Settings; 429 honors Retry-After; a network failure or 5xx backs off
     exponentially (capped at 5 minutes); 401 means the token is dead and
     the Owner Workspace connection is dropped, exactly as a push 401 does.
  3. A job created more than an hour ago is refused even if handed one
     ("stale"), as is a command over 2000 characters ("too_long"); both are
     reported failed and nothing runs.
  4. Nothing here answers an approval. Approvals stay where they are today:
     this PC's thread and inbox, or the phone companion's inbox.
  5. The result carries only the contract's allowlisted fields. replyText
     goes through the snapshot exporter's free-text scrub (no local paths,
     no email addresses, no phone numbers); then, because the site refuses
     any "@", a word still holding one becomes "[email]" and a lone "@"
     becomes "(at)". A result the site refuses is
     recorded on the operation with the reason and NEVER resent with
     different content; a transient failure resends the identical body.
  6. The command text is never logged and never written to this module's
     store (the operation record keeps it, as for any typed command).
  7. Nothing about jobs is reachable from the phone companion: there is no
     jobs route at all, and the status lives in the loopback-only
     /owner-workspace/status.

State: <data_dir>/owner_jobs.json (beside owner_workspace.json, never under
state/), holding the current job's ids, phase and an ordered outbox of
reports, plus the last few outcomes.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

import httpx

from . import operator_service, state_store, sync_service
from .runtime_paths import data_dir, guard_real_state_write

log = logging.getLogger("ridian.owner_jobs")

CLAIM_PATH = "/api/jobs/claim"
STATUS_PATH = "/api/jobs/{id}/status"
RESULT_PATH = "/api/jobs/{id}/result"
JOBS_PATH = data_dir() / "owner_jobs.json"

POLL_SECONDS = 15.0
NOT_ALLOWED_POLL_SECONDS = 5 * 60.0
BACKOFF_MAX_SECONDS = 5 * 60.0
STARTUP_DELAY_SECONDS = 15.0
STALE_AFTER = _dt.timedelta(hours=1)
COMMAND_MAX_CHARS = 2000
REPLY_TEXT_MAX_CHARS = 4000
LIST_MAX_ITEMS = 50
ARTIFACT_NAME_MAX_CHARS = 200
TOOL_NAME_MAX_CHARS = 100
SPEND_MAX_USD = 1000.0
COUNT_MAX = 1000
RECENT_KEEP = 20
SOURCE = operator_service.JOB_SOURCE          # "owner-workspace"

NOT_ALLOWED_TEXT = "Owner Workspace has not allowed this PC to run commands"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_RESULT_STATUS_RE = re.compile(r"^[a-z_]{1,32}$")
_BAD_CONTROL = {chr(c) for c in range(32) if c not in (9, 10, 13)} | {chr(127)}
# After the exporter's scrub: a word still holding an '@' between two
# characters is an address the email pattern missed (no TLD, or cut short).
_AT_WORD_RE = re.compile(r"\S+@\S+")

# Operation status -> the job's final status on the site. "partial" is a
# finished run that also hit errors: completed, with errorCount telling why.
TERMINAL_OPERATION = {"completed": "completed", "partial": "completed",
                      "failed": "failed", "cancelled": "cancelled"}

_store_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(value: Optional[_dt.datetime]) -> str:
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""


def _parse_iso(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _clean_text(text: str, limit: int) -> str:
    text = "".join(ch for ch in text if ch not in _BAD_CONTROL)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _names(values: Any, max_chars: int) -> list[str]:
    """A string list the site's allowlist accepts: each 1..max_chars, no
    path separator, no '@', no control character; at most 50. Anything else
    is left out rather than altered."""
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str):
            continue
        if (not value or len(value) > max_chars or "/" in value or "\\" in value
                or "@" in value or any(ch in _BAD_CONTROL for ch in value)):
            continue
        out.append(value)
        if len(out) >= LIST_MAX_ITEMS:
            break
    return out


# ---------------------------------------------------------------------------
# The result (contract: the allowlist; rule 5)
# ---------------------------------------------------------------------------

def reply_text(text: Any) -> str:
    """replyText for the site: the snapshot exporter's free-text scrub (which
    reads at most its TEXT_MAX characters, so longer text is first cut at a
    word boundary — never through an address — and marked "…"). Then any
    word still holding an '@' becomes "[email]", a lone '@' becomes "(at)"
    (the site refuses every '@'), and control characters are dropped. ""
    when there is nothing to say or the scrub refused itself."""
    from . import owner_snapshot_service  # lazy: it imports half the services
    raw = str(text or "").strip()
    if not raw:
        return ""
    limit = owner_snapshot_service.TEXT_MAX
    if len(raw) > limit:
        head = raw[: limit - 1]
        head = head.rsplit(None, 1)[0] if any(ch.isspace() for ch in head) else ""
        raw = head.rstrip() + "…"
    try:
        scrubbed = owner_snapshot_service.scrub_free_text(raw)
    except owner_snapshot_service.SnapshotPolicyError:
        log.warning("owner_jobs.reply_withheld reason=scrub_refused")
        return ""
    scrubbed = _AT_WORD_RE.sub(owner_snapshot_service.EMAIL_PLACEHOLDER, scrubbed).replace("@", "(at)")
    return _clean_text(scrubbed, REPLY_TEXT_MAX_CHARS).strip()


def build_result(op: dict) -> dict:
    """The POST /api/jobs/{id}/result body for a finished operation."""
    from . import owner_snapshot_service
    op_status = str(op.get("status") or "")
    final = TERMINAL_OPERATION.get(op_status, "failed")
    result: dict = {"status": op_status if _RESULT_STATUS_RE.fullmatch(op_status) else "unknown"}
    errors = op.get("errors") if isinstance(op.get("errors"), list) else []
    text = op.get("receipt") or ""
    if not str(text).strip() and final == "failed" and errors:
        text = errors[-1]
    reply = reply_text(text)
    if reply:
        result["replyText"] = reply
    result["artifactNames"] = _names(owner_snapshot_service.deliverable_names(op), ARTIFACT_NAME_MAX_CHARS)
    tools = op.get("tools_used") if isinstance(op.get("tools_used"), list) else []
    result["toolsUsed"] = _names(sorted({str(t) for t in tools}), TOOL_NAME_MAX_CHARS)
    try:
        spend = float(op.get("spend_usd") or 0.0)
    except (TypeError, ValueError):
        spend = 0.0
    result["spendUsd"] = round(min(max(spend, 0.0), SPEND_MAX_USD), 4) if spend == spend else 0.0
    needs = op.get("needs_input") if isinstance(op.get("needs_input"), list) else []
    result["openQuestions"] = min(len(needs), COUNT_MAX)
    result["errorCount"] = min(len(errors), COUNT_MAX)
    return {"status": final, "result": result}


def refusal_result(reason: str, text: str) -> dict:
    return {"status": "failed", "result": {"status": reason, "replyText": reply_text(text), "errorCount": 0}}


# ---------------------------------------------------------------------------
# The store: <data_dir>/owner_jobs.json
# ---------------------------------------------------------------------------

def _load() -> dict:
    with _store_lock:
        try:
            data = json.loads(JOBS_PATH.read_text(encoding="utf-8") or "{}")
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("owner_jobs.load_failed type=%s", type(exc).__name__)
            return {}
        return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    with _store_lock:
        guard_real_state_write(JOBS_PATH)
        JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_PATH.with_name("." + JOBS_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, JOBS_PATH)


def current_job() -> Optional[dict]:
    current = _load().get("current")
    return current if isinstance(current, dict) else None


def _last_intended_status(current: dict) -> str:
    for item in reversed(current.get("outbox") or []):
        if item.get("kind") == "status":
            return str(item["body"].get("status"))
    return str(current.get("last_status") or "claimed")


def _enqueue_status(current: dict, status: str) -> bool:
    if current.get("phase") in ("reporting", "refused") or _last_intended_status(current) == status:
        return False
    body: dict = {"status": status}
    operation_id = str(current.get("operation_id") or "")
    if _OPERATION_ID_RE.fullmatch(operation_id):
        body["operationId"] = operation_id
    current.setdefault("outbox", []).append({"kind": "status", "body": body})
    current["phase"] = status
    return True


def _mark_operation(operation_id: str, outcome: dict, detail: str) -> None:
    """Record what became of a job's result on the operation itself."""
    if not operation_id:
        return
    ops = state_store.load_list("operations")
    changed = False
    for op in ops:
        if isinstance(op, dict) and op.get("id") == operation_id:
            op["owner_workspace_result"] = outcome
            now = _dt.datetime.now().isoformat(timespec="seconds")
            op.setdefault("steps", []).append({
                "name": "owner_workspace_result", "status": "failed" if outcome.get("status") == "rejected" else "completed",
                "started_at": now, "completed_at": now, "detail": detail})
            changed = True
    if changed:
        state_store.save("operations", ops)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class JobsEngine:
    """Claims, runs and reports Owner Workspace jobs. Drive it with tick()
    (tests, with a fake clock) or run_forever() (the app's event loop)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], _dt.datetime] = _utcnow) -> None:
        self._clock = clock
        self._wall = wall
        self._next_poll = clock() + STARTUP_DELAY_SECONDS
        self._retry_at = 0.0
        self._failures = 0
        self._allowed: Optional[bool] = None
        self._last_poll_iso = ""
        self._last_error = ""
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake_event: Optional[asyncio.Event] = None
        self._stopped = False

    # -- lifecycle ---------------------------------------------------------

    def attach(self) -> None:
        state_store.add_save_listener(self.on_store_saved)
        operator_service.add_run_listener(self.on_run_event)

    def detach(self) -> None:
        state_store.remove_save_listener(self.on_store_saved)
        operator_service.remove_run_listener(self.on_run_event)

    def poll_now(self) -> None:
        """Tests: make the next tick claim (or retry) without waiting."""
        self._next_poll = self._clock()
        self._retry_at = 0.0

    def _wake(self) -> None:
        loop, event = self._loop, self._wake_event
        if loop is not None and event is not None and not loop.is_closed():
            loop.call_soon_threadsafe(event.set)

    async def run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self.recover()
        while not self._stopped:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — the loop never dies on one bad tick
                log.exception("owner_jobs.tick_failed")
                self._next_poll = self._clock() + POLL_SECONDS
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._delay())
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()

    def stop(self) -> None:
        self._stopped = True
        self._wake()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _delay(self) -> float:
        now = self._clock()
        current = current_job()
        if current and current.get("outbox"):
            return max(0.5, self._retry_at - now)
        if current:
            return POLL_SECONDS
        return max(1.0, min(self._next_poll - now, NOT_ALLOWED_POLL_SECONDS))

    async def wait_for_job(self) -> None:
        """Tests: let the running job's task finish (or park)."""
        if self._task is not None:
            await self._task

    # -- recovery ----------------------------------------------------------

    def recover(self) -> None:
        """After a restart: a job whose operation already ended reports its
        result; a job whose operation is parked keeps waiting (the inbox or
        Dismiss can still end it); a job whose run died with the process is
        reported failed ("interrupted")."""
        with _store_lock:
            data = _load()
            current = data.get("current")
            if not isinstance(current, dict) or current.get("phase") in ("reporting", "refused"):
                return
            op = next((o for o in state_store.load_list("operations")
                       if isinstance(o, dict) and (o.get("job_id") == current.get("job_id")
                                                   or (current.get("operation_id") and o.get("id") == current.get("operation_id")))),
                      None)
            if op is not None and op.get("status") in TERMINAL_OPERATION:
                self._observe_locked(data, current, op)
            elif op is not None and op.get("status") == "awaiting_input":
                current["operation_id"] = str(op.get("id") or "")
                _enqueue_status(current, "awaiting_approval")
            else:
                current.setdefault("outbox", []).append({"kind": "result", "body": refusal_result(
                    "interrupted", "Ridian Operator closed before this command finished. Nothing more will "
                                   "happen on the PC; send it again if it is still needed.")})
                current["phase"] = "reporting"
                log.info("owner_jobs.interrupted job=%s", current.get("job_id"))
            _save(data)

    # -- observation (listeners) --------------------------------------------

    def on_store_saved(self, name: str, data: Any) -> None:
        if name != "operations" or not isinstance(data, list):
            return
        current = current_job()
        if not current:
            return
        op = next((o for o in data if isinstance(o, dict) and (
            o.get("job_id") == current.get("job_id")
            or (current.get("operation_id") and o.get("id") == current.get("operation_id")))), None)
        if op is not None:
            self.observe_operation(op)

    def on_run_event(self, operation_id: str, phase: str, record: dict) -> None:
        if phase != "resumed":
            return
        with _store_lock:
            data = _load()
            current = data.get("current")
            if not isinstance(current, dict) or current.get("operation_id") != operation_id:
                return
            if _enqueue_status(current, "running"):
                _save(data)
        self._wake()

    def observe_operation(self, op: dict) -> None:
        with _store_lock:
            data = _load()
            current = data.get("current")
            if not isinstance(current, dict):
                return
            if self._observe_locked(data, current, op):
                _save(data)
        self._wake()

    def _observe_locked(self, data: dict, current: dict, op: dict) -> bool:
        if current.get("phase") in ("reporting", "refused"):
            return False
        if op.get("job_id") != current.get("job_id") and op.get("id") != current.get("operation_id"):
            return False
        if not current.get("operation_id") and op.get("id"):
            current["operation_id"] = str(op["id"])
        status = str(op.get("status") or "")
        if status == "awaiting_input":
            return _enqueue_status(current, "awaiting_approval")
        if status in TERMINAL_OPERATION:
            current.setdefault("outbox", []).append({"kind": "result", "body": build_result(op)})
            current["phase"] = "reporting"
            log.info("owner_jobs.finished job=%s operation=%s status=%s", current.get("job_id"),
                     current.get("operation_id"), status)
            return True
        return False

    # -- the job run -------------------------------------------------------

    def _job_started(self, job_id: str, operation_id: str) -> None:
        with _store_lock:
            data = _load()
            current = data.get("current")
            if not isinstance(current, dict) or current.get("job_id") != job_id:
                return
            current["operation_id"] = operation_id
            _enqueue_status(current, "running")
            _save(data)
        self._wake()

    def _job_ended_without_operation(self, job_id: str, reason: str, text: str) -> None:
        with _store_lock:
            data = _load()
            current = data.get("current")
            if not isinstance(current, dict) or current.get("job_id") != job_id or current.get("phase") == "reporting":
                return
            current.setdefault("outbox", []).append({"kind": "result", "body": refusal_result(reason, text)})
            current["phase"] = "reporting"
            _save(data)
        self._wake()

    async def _run_job(self, job_id: str, command: str) -> None:
        started = {"id": ""}
        errors: list[str] = []

        async def emit(event: dict) -> None:
            kind = event.get("event")
            payload = event.get("data") or {}
            if kind == "start" and not payload.get("resumed") and payload.get("id") and not started["id"]:
                started["id"] = str(payload["id"])
                self._job_started(job_id, started["id"])
            elif kind == "error":
                errors.append(str(payload.get("message") or ""))

        try:
            snapshot = await operator_service.run_operation(
                command=command, emit=emit, origin={"source": SOURCE, "job_id": job_id})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a crashed run is a failed job, never a stuck one
            log.exception("owner_jobs.run_crashed job=%s", job_id)
            if not started["id"]:
                self._job_ended_without_operation(job_id, "error", f"Ridian Operator could not run this command ({type(exc).__name__}).")
            else:
                op = next((o for o in state_store.load_list("operations") if o.get("id") == started["id"]), None)
                if op is not None:
                    self.observe_operation(op)
                else:
                    self._job_ended_without_operation(job_id, "error", f"The run stopped unexpectedly ({type(exc).__name__}).")
            return
        if not started["id"]:
            self._job_ended_without_operation(job_id, "not_started",
                                              errors[-1] if errors else "Ridian Operator could not start this command.")
            return
        if isinstance(snapshot, dict) and snapshot:
            self.observe_operation(snapshot)

    # -- HTTP ----------------------------------------------------------------

    def _post(self, site: str, path: str, token: str, body: Optional[dict]) -> httpx.Response:
        with sync_service._client() as client:
            return client.post(site + path, headers=sync_service._bearer(token),
                               json=body if body is not None else {})

    def _connection(self) -> tuple[dict, str]:
        conn = sync_service._load()
        if conn.get("status") != "connected" or not conn.get("device_token"):
            return {}, ""
        return conn, sync_service._unseal(conn.get("device_token"))

    def _backoff(self, retry_after: float = 0.0) -> float:
        self._failures = min(self._failures + 1, 10)
        wait = min(BACKOFF_MAX_SECONDS, POLL_SECONDS * (2 ** (self._failures - 1)))
        return max(wait, retry_after)

    def _unauthorized(self, conn: dict) -> str:
        sync_service._disconnect_locally(str(conn.get("connection_id") or "") or None, "unauthorized")
        log.warning("owner_jobs.unauthorized — the site refused this device's token; disconnected")
        self._allowed = None
        return "unauthorized"

    # -- one step ------------------------------------------------------------

    async def tick(self) -> str:
        conn, token = self._connection()
        current = current_job()
        if current and (not conn or current.get("connection_id") != conn.get("connection_id")):
            self._finish(current, "abandoned", "The Owner Workspace connection ended before this job was reported.")
            current = None
        if not conn:
            self._allowed = None
            return "disconnected"
        if not token:
            return "token_unreadable"
        now = self._clock()
        if current and current.get("outbox"):
            if now < self._retry_at:
                return "waiting"
            return await self._flush(conn, token)
        if current:
            return "busy"
        if now < self._next_poll:
            return "waiting"
        return await self._claim(conn, token)

    async def _claim(self, conn: dict, token: str) -> str:
        site = str(conn.get("site") or "")
        now = self._clock()
        self._last_poll_iso = _iso(self._wall())
        try:
            resp = await asyncio.to_thread(self._post, site, CLAIM_PATH, token, None)
        except httpx.HTTPError as exc:
            self._next_poll = now + self._backoff()
            self._last_error = f"Could not reach {sync_service._host(site)} ({type(exc).__name__})."
            log.warning("owner_jobs.claim_unreachable type=%s", type(exc).__name__)
            return "network_error"
        status = resp.status_code
        if status == 401:
            return self._unauthorized(conn)
        if status == 403:
            self._allowed = False
            self._failures = 0
            self._last_error = ""
            self._next_poll = now + NOT_ALLOWED_POLL_SECONDS
            log.info("owner_jobs.not_allowed — polling every %d s", int(NOT_ALLOWED_POLL_SECONDS))
            return "not_allowed"
        if status == 429:
            self._next_poll = now + self._backoff(sync_service._retry_after(resp))
            self._last_error = "The Owner Workspace is limiting how fast this PC takes commands."
            return "rate_limited"
        if status == 204:
            self._allowed, self._failures, self._last_error = True, 0, ""
            self._next_poll = now + POLL_SECONDS
            return "no_job"
        if status != 200:
            self._next_poll = now + self._backoff()
            self._last_error = f"The Owner Workspace answered HTTP {status}."
            log.warning("owner_jobs.claim_failed status=%s", status)
            return "server_error"
        self._allowed, self._failures, self._last_error = True, 0, ""
        self._next_poll = now + POLL_SECONDS
        return await self._accept(conn, token, sync_service._json(resp))

    async def _accept(self, conn: dict, token: str, job: dict) -> str:
        job_id = str(job.get("id") or "")
        if not _UUID_RE.fullmatch(job_id):
            log.warning("owner_jobs.claim_malformed — no usable job id; ignored")
            return "malformed"
        created = _parse_iso(job.get("createdAt"))
        command = job.get("command")
        current = {
            "job_id": job_id, "connection_id": str(conn.get("connection_id") or ""),
            "created_at": _iso(created), "claimed_iso": _iso(self._wall()),
            "operation_id": "", "phase": "starting", "last_status": "claimed", "outbox": [],
        }
        refusal = None
        if created is None:
            refusal = ("invalid", "Refused: the command arrived without a readable creation time. Nothing was run.")
        elif self._wall() - created > STALE_AFTER:
            refusal = ("stale", "Refused: this command was more than an hour old when it reached the PC. "
                                "Nothing was run; send it again if it is still needed.")
        elif not isinstance(command, str) or not command.strip():
            refusal = ("invalid", "Refused: the command was empty. Nothing was run.")
        elif len(command) > COMMAND_MAX_CHARS:
            refusal = ("too_long", f"Refused: the command is longer than {COMMAND_MAX_CHARS} characters. Nothing was run.")
        with _store_lock:
            data = _load()
            if refusal:
                current["phase"] = "refused"
                current["outbox"] = [{"kind": "result", "body": refusal_result(*refusal)}]
            data["current"] = current
            _save(data)
        if refusal:
            log.info("owner_jobs.refused job=%s reason=%s", job_id, refusal[0])
            return await self._flush(conn, token)
        log.info("owner_jobs.claimed job=%s chars=%d", job_id, len(command))
        self._task = asyncio.get_running_loop().create_task(self._run_job(job_id, command))
        return "claimed"

    async def _flush(self, conn: dict, token: str) -> str:
        site = str(conn.get("site") or "")
        outcome = "reported"
        while True:
            current = current_job()
            if not current or not current.get("outbox"):
                return outcome
            item = current["outbox"][0]
            path = (STATUS_PATH if item["kind"] == "status" else RESULT_PATH).format(id=current["job_id"])
            try:
                resp = await asyncio.to_thread(self._post, site, path, token, item["body"])
            except httpx.HTTPError as exc:
                self._retry_at = self._clock() + self._backoff()
                self._last_error = f"Could not reach {sync_service._host(site)} ({type(exc).__name__})."
                log.warning("owner_jobs.report_unreachable type=%s", type(exc).__name__)
                return "report_retry"
            status = resp.status_code
            if status == 401:
                return self._unauthorized(conn)
            data = sync_service._json(resp)
            if status == 403 and data.get("error") == "jobs_not_allowed":
                self._allowed = False
                self._retry_at = self._clock() + NOT_ALLOWED_POLL_SECONDS
                return "not_allowed"
            if status in (403, 404):
                self._finish(current, "orphaned", "The Owner Workspace no longer lists this job for this PC.")
                return "orphaned"
            if status == 429 or status >= 500 or 300 <= status < 400:
                self._retry_at = self._clock() + self._backoff(sync_service._retry_after(resp) if status == 429 else 0.0)
                self._last_error = f"The Owner Workspace answered HTTP {status}; the report will be sent again."
                return "report_retry"
            self._failures = 0
            self._last_error = ""
            if item["kind"] == "status":
                # 200 accepted; 409 means that step no longer applies (e.g. the
                # job was already further along) — drop it and go on.
                with _store_lock:
                    stored = _load()
                    cur = stored.get("current")
                    if isinstance(cur, dict) and cur.get("job_id") == current["job_id"] and cur.get("outbox"):
                        popped = cur["outbox"].pop(0)
                        if status == 200:
                            cur["last_status"] = popped["body"].get("status")
                        _save(stored)
                if status != 200:
                    log.info("owner_jobs.status_skipped status=%s", status)
                continue
            if status == 200:
                self._finish(current, "accepted", "")
                sync_service.notify("job_finished")
                return "result_accepted"
            if status in (400, 413, 415, 422):
                reason = str(data.get("reason") or data.get("error") or f"HTTP {status}")[:60]
                detail = str(data.get("detail") or "")[:120]
                _mark_operation(str(current.get("operation_id") or ""),
                                {"status": "rejected", "reason": reason, "detail": detail},
                                f"The Owner Workspace refused this job's result ({reason}"
                                + (f" at {detail}" if detail else "") + "). It was not sent again.")
                self._finish(current, "rejected", f"{reason} {detail}".strip())
                log.warning("owner_jobs.result_rejected job=%s reason=%s detail=%s", current["job_id"], reason, detail)
                return "result_rejected"
            # 409: the site already holds a final state for this job.
            self._finish(current, "conflict", f"HTTP {status}")
            return "result_conflict"

    def _finish(self, current: dict, outcome: str, detail: str) -> None:
        with _store_lock:
            data = _load()
            cur = data.get("current")
            if isinstance(cur, dict) and cur.get("job_id") == current.get("job_id"):
                data["current"] = None
            recent = [r for r in (data.get("recent") or []) if isinstance(r, dict)]
            recent.append({"job_id": current.get("job_id"), "operation_id": current.get("operation_id") or "",
                           "outcome": outcome, "detail": detail, "finished_iso": _iso(self._wall())})
            data["recent"] = recent[-RECENT_KEEP:]
            _save(data)
        self._next_poll = self._clock() + POLL_SECONDS
        self._retry_at = 0.0
        log.info("owner_jobs.done job=%s outcome=%s", current.get("job_id"), outcome)

    # -- status ----------------------------------------------------------------

    def view(self) -> dict:
        connected = sync_service.is_connected()
        current = current_job()
        if not connected:
            state, text = "off", ""
        elif current:
            state, text = "running", "Running a command from the Owner Workspace"
        elif self._allowed is False:
            state, text = "not_allowed", NOT_ALLOWED_TEXT
        elif self._allowed:
            state, text = "accepting", "Accepting commands from the Owner Workspace"
        else:
            state, text = "checking", "Checking the Owner Workspace for commands"
        return {
            "state": state,
            "text": text,
            "last_poll_iso": self._last_poll_iso,
            "last_error": self._last_error if connected else "",
            "current": ({"job_id": current.get("job_id"), "operation_id": current.get("operation_id") or "",
                         "phase": current.get("phase")} if current else None),
            "recent": [{k: r.get(k) for k in ("job_id", "operation_id", "outcome", "finished_iso")}
                       for r in (_load().get("recent") or [])[-5:] if isinstance(r, dict)],
        }


# ---------------------------------------------------------------------------
# The process-wide engine
# ---------------------------------------------------------------------------

_engine: Optional[JobsEngine] = None
_loop_task: Optional[asyncio.Task] = None


def start_engine() -> JobsEngine:
    """Called from the app lifespan (inside the running event loop)."""
    global _engine, _loop_task
    if _engine is None:
        _engine = JobsEngine()
        _engine.attach()
        _loop_task = asyncio.get_running_loop().create_task(_engine.run_forever())
    return _engine


def stop_engine() -> None:
    global _engine, _loop_task
    engine, task = _engine, _loop_task
    _engine, _loop_task = None, None
    if engine is not None:
        engine.detach()
        engine.stop()
    if task is not None and not task.done():
        task.cancel()


def use_engine(engine: Optional[JobsEngine]) -> Optional[JobsEngine]:
    """Install an engine without its loop (tests). Returns the previous one."""
    global _engine
    previous, _engine = _engine, engine
    return previous


def status_view() -> dict:
    engine = _engine
    if engine is None:
        return {"state": "off" if not sync_service.is_connected() else "checking",
                "text": "", "last_poll_iso": "", "last_error": "", "current": None, "recent": []}
    return engine.view()
