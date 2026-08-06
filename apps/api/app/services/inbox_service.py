"""Inbox triage — READ ONLY (v6.0 Phase 5).

Reads recent inbox threads and sorts them into four buckets:

  - needs_reply    : they spoke last; the ball is in the operator's court
  - waiting_on     : the operator spoke last; waiting on someone else
  - gone_quiet     : no message in 7+ days and still unresolved
  - from_contacts  : any thread with a party in the contacts store
                     (pipeline mail, surfaced first everywhere)

By construction there is NO send, modify, trash, label, or delete path in
this module — the only Gmail calls are threads().list and threads().get,
pinned by introspection the same way the QuickBooks create-only surface
and the read-only Calendar surface are pinned. Drafting a reply remains
the job of draft_gmail, behind its existing recipient-provenance and
approval gates; triage never drafts and never sends.

Classification is a PURE function of normalized threads + the contacts
store + a clock, so the same input always produces the same buckets.
"""

from __future__ import annotations

import datetime as _dt
import logging
from email.utils import parseaddr
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from . import memory_service, pipeline_service
from .google_drive_service import _load_credentials

log = logging.getLogger("ridian.inbox")

GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Bounded by construction: triage never walks the whole mailbox.
DEFAULT_QUERY = "in:inbox -in:chats newer_than:30d"
DEFAULT_MAX_THREADS = 25
QUIET_AFTER_DAYS = 7
_HEADERS = ["From", "To", "Cc", "Subject", "Date"]


class InboxError(Exception):
    """Raised when a Gmail read is unavailable or fails. ``detail`` is safe
    to surface (never message bodies or tokens)."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def _build_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _has_read_scope(creds: Optional[Credentials]) -> bool:
    if not creds:
        return False
    return GMAIL_READ_SCOPE in set(creds.scopes or [])


def is_inbox_ready() -> bool:
    """True only if the saved token actually grants gmail.readonly."""
    try:
        creds = _load_credentials()
    except Exception:  # noqa: BLE001
        return False
    return _has_read_scope(creds)


# ---------------------------------------------------------------------------
# Pure layer: address handling, normalization, classification
# ---------------------------------------------------------------------------

def _addr(header_value: str) -> str:
    """Bare lowercase address from a From/To header value."""
    _name, addr = parseaddr(str(header_value or ""))
    return addr.strip().lower()


def _addrs(header_value: str) -> list[str]:
    out = []
    for part in str(header_value or "").split(","):
        a = _addr(part)
        if a and a not in out:
            out.append(a)
    return out


def _headers_of(message: dict) -> dict:
    hs = ((message.get("payload") or {}).get("headers")) or []
    return {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in hs}


def normalize_thread(raw: dict, me: str) -> dict:
    """Gmail thread -> the flat shape classification works on. Only header
    metadata and Gmail's own snippet are kept; message bodies are never
    read or stored."""
    me = (me or "").strip().lower()
    messages = list(raw.get("messages") or [])
    parties: list[str] = []
    subject = ""
    last_from = ""
    last_ms = 0
    for m in messages:
        h = _headers_of(m)
        if not subject:
            subject = h.get("subject", "")
        for a in _addrs(h.get("from", "")) + _addrs(h.get("to", "")) + _addrs(h.get("cc", "")):
            if a and a != me and a not in parties:
                parties.append(a)
        try:
            ms = int(m.get("internalDate") or 0)
        except (TypeError, ValueError):
            ms = 0
        if ms >= last_ms:
            last_ms = ms
            last_from = _addr(h.get("from", ""))
    last_dt = (_dt.datetime.fromtimestamp(last_ms / 1000)
               if last_ms else None)
    return {
        "id": raw.get("id", ""),
        "subject": subject or "(no subject)",
        "parties": parties,
        "last_from": last_from,
        "from_me": bool(me) and last_from == me,
        "last_message_at": last_dt.isoformat(timespec="minutes") if last_dt else "",
        "_last_dt": last_dt,
        "message_count": len(messages),
        "snippet": str((messages[-1] if messages else {}).get("snippet", ""))[:200],
        "unread": "UNREAD" in (raw.get("messages", [{}])[-1].get("labelIds") or []
                               if messages else []),
        "link": f"https://mail.google.com/mail/u/0/#inbox/{raw.get('id', '')}",
    }


def _contact_index() -> dict:
    """{lowercased email: {name, contact_id, in_pipeline}} from the contacts
    store. Contacts are the app's only identity source — exact address
    match only, so a lookalike domain never counts as a known contact."""
    active_contact_ids = {
        d.get("contact_id") for d in pipeline_service.list_deals()
        if d.get("stage") in pipeline_service.ACTIVE_STAGES}
    index: dict = {}
    for c in memory_service.list_contacts():
        email = str(c.get("email") or "").strip().lower()
        if not email:
            continue
        index[email] = {"contact_id": c.get("id", ""),
                        "name": c.get("name", ""),
                        "company": c.get("company", ""),
                        "in_pipeline": c.get("id") in active_contact_ids}
    return index


def classify(threads: list[dict], *, now: Optional[_dt.datetime] = None,
             quiet_after_days: int = QUIET_AFTER_DAYS,
             contacts: Optional[dict] = None) -> dict:
    """PURE: normalized threads -> the four buckets. Same input, same
    output — no clock or store access beyond what is passed in."""
    now = now or _dt.datetime.now()
    contacts = _contact_index() if contacts is None else contacts
    quiet_before = now - _dt.timedelta(days=quiet_after_days)

    enriched = []
    for t in threads:
        match = None
        for a in ([t.get("last_from")] if t.get("last_from") else []) + list(t.get("parties") or []):
            if a in contacts:
                match = {**contacts[a], "email": a}
                break
        last_dt = t.get("_last_dt")
        enriched.append({
            **{k: v for k, v in t.items() if not k.startswith("_")},
            "contact": match,
            "days_quiet": ((now - last_dt).days if last_dt else None),
        })

    def _rank(row: dict) -> int:
        contact = row.get("contact") or {}
        if contact.get("in_pipeline"):
            return 0                      # pipeline mail surfaces first
        return 1 if contact else 2        # then known contacts, then the rest

    def _order(rows: list[dict]) -> list[dict]:
        # Two stable passes: newest-first within each group, then group rank.
        by_recency = sorted(rows, key=lambda r: r.get("last_message_at") or "",
                            reverse=True)
        return sorted(by_recency, key=_rank)

    needs_reply, waiting_on, gone_quiet, from_contacts = [], [], [], []
    for r in enriched:
        if r["from_me"]:
            waiting_on.append(r)          # the operator spoke last
        else:
            needs_reply.append(r)         # they spoke last — operator's move
        if r["days_quiet"] is not None and r["days_quiet"] >= quiet_after_days:
            gone_quiet.append(r)
        if r.get("contact"):
            from_contacts.append(r)

    return {
        "needs_reply": _order(needs_reply),
        "waiting_on": _order(waiting_on),
        "gone_quiet": _order(gone_quiet),
        "from_contacts": _order(from_contacts),
        "quiet_after_days": quiet_after_days,
        "checked": len(enriched),
        "quiet_before": quiet_before.isoformat(timespec="minutes"),
    }


# ---------------------------------------------------------------------------
# The only API surface: reads
# ---------------------------------------------------------------------------

def _fetch_thread_ids(query: str, max_threads: int) -> list[str]:
    """threads().list — bounded by query and count. One of exactly two
    Gmail calls in this module."""
    creds = _load_credentials()
    if not _has_read_scope(creds):
        raise InboxError(
            "Gmail read access is not granted — Connect (or Reconnect) Google "
            "in Settings to grant it.", status=400)
    try:
        resp = _build_service(creds).users().threads().list(
            userId="me", q=query,
            maxResults=max(1, min(int(max_threads), 100)),
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise InboxError(f"Gmail read failed: {exc}", status=502) from exc
    return [t.get("id", "") for t in (resp.get("threads") or []) if t.get("id")]


def _fetch_thread(thread_id: str) -> dict:
    """threads().get in METADATA format — headers only, never bodies. The
    other of exactly two Gmail calls in this module."""
    creds = _load_credentials()
    if not _has_read_scope(creds):
        raise InboxError("Gmail read access is not granted.", status=400)
    try:
        return _build_service(creds).users().threads().get(
            userId="me", id=thread_id, format="metadata",
            metadataHeaders=_HEADERS,
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise InboxError(f"Gmail thread read failed: {exc}", status=502) from exc


def _me_address() -> str:
    from . import gmail_service
    return (gmail_service.get_user_email() or "").strip().lower()


def triage(*, now: Optional[_dt.datetime] = None, query: str = "",
           max_threads: int = DEFAULT_MAX_THREADS,
           quiet_after_days: int = QUIET_AFTER_DAYS) -> dict:
    """Read recent inbox threads and classify them. READ ONLY."""
    me = _me_address()
    ids = _fetch_thread_ids(query or DEFAULT_QUERY, max_threads)
    threads = [normalize_thread(_fetch_thread(tid), me) for tid in ids]
    result = classify(threads, now=now, quiet_after_days=quiet_after_days)
    return {**result, "account": me, "query": query or DEFAULT_QUERY}
