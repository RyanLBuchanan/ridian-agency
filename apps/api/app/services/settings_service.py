"""Local settings persistence — a small JSON file next to .env.

Local-only, no encryption. The file is git-ignored and lives at
``apps/api/local_settings.json``. Treat it the same as ``.env`` in terms of
disk handling.

Precedence model: local settings always take priority over environment
variables. This lets the desktop Settings panel feel like the source of
truth while still allowing operators who prefer .env to keep using it as a
fallback (and dev/CI to override via env at startup).

Never returns ``smtp_password`` through the public-view function — the API
layer uses that view to build GET /settings.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("ridian.settings")

# apps/api/app/services/settings_service.py  ->  apps/api/local_settings.json
SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "local_settings.json"
)

SETTABLE_KEYS: tuple[str, ...] = (
    "operator_name",
    "operator_email",
    "default_to_email",
    "company_name",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from_email",
)

PUBLIC_KEYS: tuple[str, ...] = tuple(k for k in SETTABLE_KEYS if k != "smtp_password")


def load_settings() -> dict[str, str]:
    """Read the JSON file. Returns {} if the file is missing or malformed."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        text = SETTINGS_PATH.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        data = json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("settings.load_failed type=%s", type(exc).__name__)
        return {}
    if not isinstance(data, dict):
        log.warning("settings.bad_format")
        return {}
    return {
        str(k): ("" if v is None else str(v))
        for k, v in data.items()
        if k in SETTABLE_KEYS
    }


def save_settings(updates: dict[str, Any]) -> dict[str, str]:
    """Merge ``updates`` into the on-disk settings, return the full new dict.

    Rules:
      - Unknown keys are ignored.
      - ``smtp_password``: blank/missing means "keep the existing saved value"
        (so the GUI never has to round-trip the password back to the server).
      - All other keys: a present-but-blank value clears the field.
    """
    current = load_settings()
    new = dict(current)
    for k in SETTABLE_KEYS:
        if k not in updates:
            continue
        val = updates[k]
        if k == "smtp_password" and (val is None or val == ""):
            continue  # preserve existing
        new[k] = "" if val is None else str(val)

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(new, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log.info(
        "settings.saved keys=%s",
        sorted(k for k in new.keys() if k != "smtp_password"),
    )
    return new


def public_view() -> dict[str, Any]:
    """Settings as exposed by GET /settings — never includes smtp_password."""
    s = load_settings()
    out: dict[str, Any] = {k: s.get(k, "") for k in PUBLIC_KEYS}
    out["smtp_password_configured"] = bool(s.get("smtp_password"))
    return out


def get_effective_value(env_key: str) -> str | None:
    """Resolve a setting by its env-var name.

    Looks up the lowercase variant in the local settings file first, then
    falls back to the actual environment variable. Returns ``None`` if
    neither is set. Whitespace is trimmed.
    """
    snake = env_key.lower()
    s = load_settings()
    val = (s.get(snake) or "").strip()
    if val:
        return val
    env_val = (os.getenv(env_key) or "").strip()
    return env_val or None
