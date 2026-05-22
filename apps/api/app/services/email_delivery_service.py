"""SMTP delivery for approved draft emails.

Configuration is read from environment variables at call time (loaded by
python-dotenv at app startup). The service is intentionally cautious:

- Never returns or logs SMTP_PASSWORD.
- Never echoes the raw exception string from the SMTP server (which on some
  servers can include the auth username or other state). Errors are reduced
  to the exception class name and our own short reason text.
- Reports which environment variables are missing so the API layer can
  surface a precise 503 to the operator.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from .settings_service import get_effective_value

log = logging.getLogger("ridian.email")

REQUIRED_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM_EMAIL",
)

DEFAULT_SUBJECT = "Ridian Agency Draft Email Output"


@dataclass
class EmailDeliveryResult:
    ok: bool
    detail: str
    to_email: Optional[str] = None


def missing_smtp_keys() -> list[str]:
    return [k for k in REQUIRED_KEYS if not get_effective_value(k)]


def send_email(
    subject: str,
    body: str,
    to_email: Optional[str] = None,
) -> EmailDeliveryResult:
    if not (body or "").strip():
        return EmailDeliveryResult(ok=False, detail="Email body is empty.")

    missing = missing_smtp_keys()
    if missing:
        return EmailDeliveryResult(
            ok=False,
            detail=(
                "SMTP not configured. Missing environment variable(s): "
                + ", ".join(missing)
                + ". See apps/api/.env.example."
            ),
        )

    recipient = (to_email or "").strip() or get_effective_value("DEFAULT_TO_EMAIL") or ""
    if not recipient:
        return EmailDeliveryResult(
            ok=False,
            detail="No recipient. Provide to_email in the request or set a default recipient in Settings.",
        )

    host = get_effective_value("SMTP_HOST") or ""
    port_raw = get_effective_value("SMTP_PORT") or "587"
    try:
        port = int(port_raw)
    except ValueError:
        return EmailDeliveryResult(
            ok=False, detail=f"SMTP_PORT must be an integer (got: {port_raw!r})."
        )

    username = get_effective_value("SMTP_USERNAME") or ""
    password = get_effective_value("SMTP_PASSWORD") or ""
    sender = get_effective_value("SMTP_FROM_EMAIL") or ""

    msg = EmailMessage()
    msg["Subject"] = (subject or "").strip() or DEFAULT_SUBJECT
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
                s.login(username, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                if s.has_extn("starttls"):
                    s.starttls(context=ctx)
                    s.ehlo()
                s.login(username, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        log.warning("smtp.auth_failed code=%s", exc.smtp_code)
        return EmailDeliveryResult(
            ok=False,
            detail="SMTP authentication failed. Verify SMTP_USERNAME and SMTP_PASSWORD.",
        )
    except smtplib.SMTPException as exc:
        log.warning("smtp.error type=%s", type(exc).__name__)
        return EmailDeliveryResult(
            ok=False, detail=f"SMTP error: {type(exc).__name__}."
        )
    except (OSError, ssl.SSLError) as exc:
        log.warning("smtp.network_error type=%s", type(exc).__name__)
        return EmailDeliveryResult(
            ok=False, detail=f"Network error reaching SMTP server ({type(exc).__name__})."
        )

    log.info("email.sent to=%s", recipient)
    return EmailDeliveryResult(ok=True, detail="Email sent.", to_email=recipient)
