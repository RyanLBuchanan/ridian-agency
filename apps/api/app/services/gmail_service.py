"""Gmail Drafts integration (approval-by-construction).

Scope: ``gmail.compose`` only. The app can CREATE and EDIT drafts in the
user's Gmail Drafts folder. It CAN NOT send mail. Drafts sit in the
operator's Gmail Drafts UI until they decide to send — per the memo:
"creating Gmail drafts (Gmail Drafts are not sent — they sit waiting for
him)" goes under "No approval needed."

Security model mirrors google_drive_service:
- Reuses the same OAuth token at apps/api/google_token.json (git-ignored).
- No endpoint ever returns the token, the body, or the recipient of a draft.
- The renderer only sees safe metadata: draft id, the operator's email,
  the Gmail compose URL to open the draft directly.

Failure paths surface honestly:
- No google_credentials.json → "Google credentials missing."
- Token doesn't include gmail.compose scope (user connected pre-v1.3) →
  "Insufficient scope. Reconnect Google in Settings."
- 403/4xx from Gmail API → bubble the HTTP status with a clean message.
- Network errors → caller decides how to surface.
"""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage
from typing import Optional

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .google_drive_service import (
    SCOPES,
    GoogleDriveError,
    _load_credentials,
)

log = logging.getLogger("ridian.gmail")

# The Gmail compose URL pattern Gmail itself uses to deep-link into a draft.
# Format: https://mail.google.com/mail/u/0/#drafts?compose=<draft_id>
# Works in the user's default browser; opens the draft ready for review/send.
_GMAIL_DRAFT_URL = "https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"


class GmailError(Exception):
    """Raised by helpers when a Gmail call is invalid or fails.

    ``detail`` is safe to surface to the operator (never includes tokens,
    full message bodies, or recipient lists). ``status`` is the HTTP status
    the API layer should map to.
    """

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def _build_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _has_compose_scope(creds: Optional[Credentials]) -> bool:
    if not creds:
        return False
    granted = set(creds.scopes or [])
    return "https://www.googleapis.com/auth/gmail.compose" in granted


def is_compose_ready() -> bool:
    """Cheap presence check the renderer can poll to show a 'Gmail connected'
    status pill. True only if the saved token grants gmail.compose."""
    try:
        creds = _load_credentials()
    except Exception:
        return False
    return _has_compose_scope(creds)


def get_user_email() -> Optional[str]:
    """Best-effort lookup of the connected user's email via the Gmail profile.
    Returns None if Gmail isn't connected or the call fails."""
    creds = _load_credentials()
    if not _has_compose_scope(creds):
        return None
    try:
        service = _build_service(creds)
        prof = service.users().getProfile(userId="me").execute()
        return prof.get("emailAddress")
    except Exception as exc:  # noqa: BLE001
        log.info("gmail.profile_lookup_failed type=%s", type(exc).__name__)
        return None


def _build_mime(to: str, subject: str, body: str, *, from_email: Optional[str]) -> str:
    """Build an RFC 5322 message + return it base64url-encoded for Gmail API."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    if from_email:
        msg["From"] = from_email
    msg.set_content(body or "")
    raw = base64.urlsafe_b64encode(bytes(msg)).decode("ascii").rstrip("=")
    return raw


def create_draft(
    to: str,
    subject: str,
    body: str,
    *,
    from_email: Optional[str] = None,
) -> dict:
    """Create a Gmail draft and return safe metadata.

    Args:
        to: Recipient email address. Required.
        subject: Draft subject. Empty becomes "(no subject)".
        body: Plain-text draft body.
        from_email: Optional From header (defaults to the connected account).

    Returns:
        {
            "draft_id": str,
            "compose_url": str,   # opens the draft in Gmail web
            "to": str,            # echo so the renderer can show "draft to X"
        }

    Raises:
        GmailError: with a renderer-safe ``detail`` + appropriate HTTP status.
    """
    if not to or "@" not in to:
        raise GmailError("draft_gmail rejected: recipient email is required.", status=400)

    try:
        creds = _load_credentials()
    except RefreshError as exc:
        raise GmailError(
            "Google token refresh failed. Reconnect Google in Settings.",
            status=400,
        ) from exc

    if not creds or not creds.valid:
        raise GmailError(
            "Google is not connected. Open Settings → Connect Google Drive first.",
            status=400,
        )
    if not _has_compose_scope(creds):
        raise GmailError(
            "Gmail compose permission is missing. Disconnect + reconnect "
            "Google in Settings so the new gmail.compose scope is granted.",
            status=400,
        )

    try:
        service = _build_service(creds)
    except Exception as exc:  # noqa: BLE001
        log.warning("gmail.build_service_failed type=%s", type(exc).__name__)
        raise GmailError(f"Gmail service unavailable ({type(exc).__name__}).", status=502) from exc

    raw = _build_mime(to, subject, body, from_email=from_email)

    try:
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", "?")
        log.warning("gmail.draft_create_failed status=%s", status)
        raise GmailError(
            f"Gmail draft create failed (HTTP {status}).",
            status=502 if str(status).startswith("5") else 400,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("gmail.draft_create_unexpected type=%s", type(exc).__name__)
        raise GmailError(
            f"Gmail draft create failed ({type(exc).__name__}).",
            status=502,
        ) from exc

    draft_id = draft.get("id", "")
    log.info("gmail.draft_created id=%s to=%s subject=%r",
             draft_id, to.split("@")[0] + "@…", (subject or "")[:60])
    return {
        "draft_id": draft_id,
        "compose_url": _GMAIL_DRAFT_URL.format(draft_id=draft_id),
        "to": to,
    }
