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
    # Anthropic powers all agents/workflows; the OpenAI key remains only for
    # voice-input transcription (Whisper — Anthropic has no transcription API).
    "anthropic_api_key",
    "anthropic_model",
    "anthropic_research_model",
    "anthropic_script_model",
    "openai_api_key",
    "openai_model",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from_email",
    "google_drive_root_folder_id",
    "operator_auto_upload_drive",
    # v3.2: hard per-run dollar ceiling. Blank = the $1.00 default (an
    # untouched field is blank, so this is the only way a default can apply);
    # "off" = no ceiling, deliberately. Parsed by
    # operator_service.resolve_cost_ceiling at operation intake.
    "operator_run_cost_ceiling_usd",
    "appearance",
)

# Secrets — never returned by the public view, and preserved-on-blank when
# the GUI submits an empty field (so the renderer never round-trips them).
SECRET_KEYS: frozenset[str] = frozenset({
    "smtp_password", "openai_api_key", "anthropic_api_key",
})

PUBLIC_KEYS: tuple[str, ...] = tuple(k for k in SETTABLE_KEYS if k not in SECRET_KEYS)

# Settings whose values we mirror into os.environ so SDKs that read env vars
# directly (the Anthropic SDK reading ANTHROPIC_API_KEY, the OpenAI SDK
# reading OPENAI_API_KEY for Whisper) see the value.
_SDK_ENV_MAP: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_model": "ANTHROPIC_MODEL",
    "anthropic_research_model": "ANTHROPIC_RESEARCH_MODEL",
    "anthropic_script_model": "ANTHROPIC_SCRIPT_MODEL",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_model": "OPENAI_MODEL",
}


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
        # Secrets: blank/missing means "keep the existing saved value" so the
        # GUI never has to round-trip the secret back to the server.
        if k in SECRET_KEYS and (val is None or val == ""):
            continue
        new[k] = "" if val is None else str(val)

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(new, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log.info(
        "settings.saved keys=%s",
        sorted(k for k in new.keys() if k not in SECRET_KEYS),
    )
    return new


def public_view() -> dict[str, Any]:
    """Settings as exposed by GET /settings — never includes secrets.

    Each secret is reported as a ``<name>_configured: bool`` flag so the GUI
    can render an appropriate hint without ever seeing the value.
    """
    s = load_settings()
    out: dict[str, Any] = {k: s.get(k, "") for k in PUBLIC_KEYS}
    out["smtp_password_configured"] = bool(s.get("smtp_password"))
    out["openai_api_key_configured"] = bool(s.get("openai_api_key"))
    out["anthropic_api_key_configured"] = bool(
        s.get("anthropic_api_key") or os.getenv("ANTHROPIC_API_KEY")
    )
    return out


def apply_to_environment() -> None:
    """Mirror SDK-relevant settings into ``os.environ``.

    The OpenAI SDK reads ``OPENAI_API_KEY`` from the environment directly,
    so settings stored in ``local_settings.json`` need to be reflected there.
    Settings take precedence over whatever ``.env`` already loaded: a
    present value in the file overrides ``os.environ``.

    Only mutates env for keys with a non-empty value in the settings file.
    Absent settings leave ``os.environ`` untouched so ``.env`` and shell env
    still act as fallback.
    """
    s = load_settings()
    for snake, env_key in _SDK_ENV_MAP.items():
        val = (s.get(snake) or "").strip()
        if val:
            os.environ[env_key] = val


def get_bool_setting(key: str, default: bool = False) -> bool:
    """Read a boolean setting stored as a string ("true"/"false"/"1"/"0").

    Returns ``default`` for missing / empty / unrecognized values so callers
    can stay simple. Used for v1.4+ toggles like ``operator_auto_upload_drive``.
    """
    raw = (load_settings().get(key) or "").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    return default


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
