"""Open a web destination in the operator's browser (v1.8).

Opening a URL is a real, useful action with no external blast radius — it
happens entirely on the operator's own machine, like opening a folder. So
it needs no approval, same as export_service's open-folder.

Two layers of safety keep an LLM-driven open honest:
  1. Scheme allowlist — only http/https ever launch. file://, javascript:,
     custom schemes, and bare shell strings are rejected. No URL is ever
     passed through a shell (subprocess uses the list form), so there's no
     command-injection surface.
  2. Known-destination shortcuts — "notebooklm", "drive", "gmail", etc.
     resolve to vetted https URLs so the planner doesn't have to guess
     (or hallucinate) the address.

Chrome targeting: when the caller asks for Chrome specifically we locate
chrome.exe at the standard Windows install paths and launch it directly;
if Chrome isn't found we fall back to the OS default browser and report
which was actually used. Non-Windows falls back to webbrowser.open.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from urllib.parse import urlparse

log = logging.getLogger("ridian.browser")

# Vetted destinations the planner can name by keyword instead of guessing
# a URL. Keys are matched case-insensitively after stripping non-alphanum.
KNOWN_DESTINATIONS: dict[str, str] = {
    "notebooklm": "https://notebooklm.google.com",
    "drive": "https://drive.google.com",
    "googledrive": "https://drive.google.com",
    "gmail": "https://mail.google.com",
    "calendar": "https://calendar.google.com",
    "googlecalendar": "https://calendar.google.com",
    "sheets": "https://sheets.google.com",
    "googlesheets": "https://sheets.google.com",
    "slides": "https://slides.google.com",
    "googleslides": "https://slides.google.com",
    "docs": "https://docs.google.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "perplexity": "https://www.perplexity.ai",
    "gemini": "https://gemini.google.com",
    "github": "https://github.com",
    "linkedin": "https://www.linkedin.com",
}


class BrowserError(Exception):
    """Renderer-safe failure. ``detail`` never contains anything sensitive."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


def _norm_key(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def resolve_destination(target: str) -> str:
    """Turn a keyword OR a URL into a launchable https(s) URL.

    Accepts:
      - a known-destination keyword ("notebooklm", "my drive" → drive, ...)
      - a full http(s) URL
      - a bare domain ("example.com" → https://example.com)
    Rejects anything that isn't http/https once resolved.
    """
    if not target or not target.strip():
        raise BrowserError("No destination given. Name a site or paste a URL.")
    raw = target.strip()

    # 1. Exact keyword match (normalized) — "notebooklm", "Drive", etc.
    key = _norm_key(raw)
    if key in KNOWN_DESTINATIONS:
        return KNOWN_DESTINATIONS[key]

    # 2. Anything with a scheme is a URL — validate it as one. Crucially this
    #    runs BEFORE fuzzy keyword matching, so a real URL that happens to
    #    contain a keyword as a substring (e.g. "docs.google.com/SPREADSHEETs")
    #    is never rewritten to a shortcut.
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            raise BrowserError(
                "Only http and https links can be opened (got "
                f"'{parsed.scheme or 'no scheme'}')."
            )
        if not parsed.netloc:
            raise BrowserError(f"\"{raw}\" doesn't have a valid web address.")
        return raw

    # 3. Fuzzy keyword match for natural phrasing ("open notebook lm please").
    for k, url in KNOWN_DESTINATIONS.items():
        if k in key:
            return url

    # 4. Bare domain → assume https.
    if "." in raw and " " not in raw:
        candidate = "https://" + raw
        parsed = urlparse(candidate)
        if parsed.netloc:
            return candidate

    raise BrowserError(
        f"\"{raw}\" isn't a site I recognize or a valid URL. "
        "Try a name like 'NotebookLM' or a full https:// address."
    )


def _find_chrome() -> str | None:
    """Locate chrome.exe on Windows, else `chrome`/`google-chrome` on PATH."""
    if sys.platform.startswith("win"):
        candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                         r"Google\Chrome\Application\chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                         r"Google\Chrome\Application\chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         r"Google\Chrome\Application\chrome.exe"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None
    for name in ("google-chrome", "chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return None


def open_url(target: str, browser: str = "chrome") -> dict:
    """Open ``target`` in the requested browser. Returns safe metadata.

    Args:
        target: keyword (e.g. "notebooklm") or http(s) URL.
        browser: "chrome" to prefer Chrome, anything else → OS default.

    Returns:
        {"url": str, "browser_used": "chrome"|"default", "opened": bool}
    """
    url = resolve_destination(target)
    want_chrome = (browser or "").strip().lower() in ("chrome", "google chrome", "googlechrome")

    if want_chrome:
        chrome = _find_chrome()
        if chrome:
            try:
                # List form, no shell → no injection surface. Detached so the
                # backend request doesn't block on the browser process.
                subprocess.Popen(
                    [chrome, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("browser.opened browser=chrome url=%s", url)
                return {"url": url, "browser_used": "chrome", "opened": True}
            except Exception as exc:  # noqa: BLE001
                log.warning("browser.chrome_launch_failed type=%s", type(exc).__name__)
                # fall through to default

    # OS default browser.
    try:
        opened = webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        raise BrowserError(f"Could not open a browser ({type(exc).__name__}).", 500) from exc
    if not opened:
        raise BrowserError("No browser is available to open the link.", 500)
    log.info("browser.opened browser=default url=%s", url)
    return {"url": url, "browser_used": "default", "opened": True}
