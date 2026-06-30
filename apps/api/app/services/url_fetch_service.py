"""Fetch a SPECIFIC web page and extract its real text — SSRF-guarded.

This is what lets Ridian ground a deliverable in a page the operator names
(e.g. a chamber's membership/benefits pages) instead of general web-search
guesses. Unlike ``browser_service`` (which only *launches* a URL on the
operator's own machine), this service performs a **server-side fetch**, so it
needs real SSRF protection:

  1. Scheme allowlist — only http/https (no file://, ftp://, data:, ...).
  2. No embedded credentials (user:pass@host rejected).
  3. DNS-resolve the host and reject if ANY resolved IP is loopback, private,
     link-local (incl. 169.254.169.254 cloud-metadata), CGNAT, reserved,
     multicast, or unspecified. Checking the *resolved* IP — not just the
     literal — defeats hostnames that point at internal addresses.
  4. Port allowlist — 80/443 only.
  5. Redirects are NOT auto-followed: each hop is re-validated through the
     same guard, capped at MAX_REDIRECTS.
  6. Content-Type must be HTML.
  7. Hard byte cap + connect/read timeouts.

Residual risk (documented, Phase-2 hardening): there is a small TOCTOU window
between our DNS check and httpx's own resolution (DNS rebinding). The robust
fix is to pin the connection to the validated IP with SNI; that is deliberately
out of scope for Phase 1. Redirect re-validation already blocks the common
"public URL 302s to an internal address" case.

Extraction is layered: trafilatura (best main-content extraction) →
BeautifulSoup+lxml fallback → a stdlib HTMLParser last resort, so the service
keeps working even if an optional extractor isn't installed.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger("ridian.urlfetch")

# --- limits / policy (module-level so tests can monkeypatch) ----------------
ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
MAX_BYTES = 3_000_000          # 3 MB hard cap on downloaded page bytes
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 40_000        # cap on extracted text handed to the model
TIMEOUT = httpx.Timeout(15.0, connect=5.0, read=10.0)
USER_AGENT = (
    "RidianCommandCenter/1.0 (+https://ridiantechnologies.com; research fetch)"
)
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


class ReadUrlError(Exception):
    """Renderer-safe failure. ``detail`` never leaks internal addresses."""

    def __init__(self, detail: str, status: int = 400):
        self.detail = detail
        self.status = status
        super().__init__(detail)


# ---------------------------------------------------------------------------
# SSRF validation
# ---------------------------------------------------------------------------

def _ip_is_blocked(ip_str: str) -> bool:
    """True if an IP must never be fetched (internal / special-use)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # un-parseable → refuse
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so it can't smuggle a
    # loopback/private v4 past the v6 checks.
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return True
    if ip.version == 4 and ip in _CGNAT_NET:
        return True
    return False


def _resolve_and_check(host: str, port: int) -> None:
    """Resolve ``host`` and reject if ANY resolved IP is non-public."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ReadUrlError(f"Could not resolve the host for {host!r}.")
    if not infos:
        raise ReadUrlError(f"Could not resolve the host for {host!r}.")
    for info in infos:
        ip = info[4][0]
        if _ip_is_blocked(ip):
            # Don't echo the internal IP back to the renderer.
            raise ReadUrlError(
                "Refusing to fetch that address — it resolves to a non-public "
                "(internal/loopback) host. Only public web pages can be read."
            )


def validate_url(raw: str) -> str:
    """Validate a URL against every SSRF rule. Returns the cleaned URL or raises."""
    if not raw or not raw.strip():
        raise ReadUrlError("No URL was given.")
    url = raw.strip()
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ReadUrlError(
            "Only http and https pages can be read "
            f"(got '{parsed.scheme or 'no scheme'}')."
        )
    if parsed.username or parsed.password:
        raise ReadUrlError("URLs with embedded credentials are not allowed.")

    host = parsed.hostname
    if not host:
        raise ReadUrlError("That URL has no host.")
    if host.lower() == "localhost":
        raise ReadUrlError("Refusing to fetch localhost.")

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise ReadUrlError(f"Only ports 80 and 443 are allowed (got {port}).")

    _resolve_and_check(host, port)
    return url


# ---------------------------------------------------------------------------
# Extraction (layered, import-guarded)
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class _TextStripper(HTMLParser):
    """Stdlib last-resort extractor: drop scripts/styles, keep text."""

    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip())


def _extract_title(html: str) -> str:
    m = _TITLE_RE.search(html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _extract_trafilatura(html: str, url: str) -> str:
    try:
        import trafilatura  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    try:
        out = trafilatura.extract(
            html, url=url, include_comments=False, include_tables=True,
            favor_recall=True,
        )
        return (out or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _extract_bs4(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    try:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:  # noqa: BLE001 — lxml missing → stdlib parser
            soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "template", "svg",
                         "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        text = soup.get_text("\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    except Exception:  # noqa: BLE001
        return ""


def _extract_stdlib(html: str) -> str:
    try:
        p = _TextStripper()
        p.feed(html)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(p.parts)).strip()
    except Exception:  # noqa: BLE001
        return ""


def extract_main_text(html: str, url: str = "") -> tuple[str, str]:
    """Return ``(title, text)`` using the best available extractor."""
    title = _extract_title(html)
    text = (_extract_trafilatura(html, url)
            or _extract_bs4(html)
            or _extract_stdlib(html))
    return title, (text or "").strip()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _charset_from_content_type(content_type: str) -> str:
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.IGNORECASE)
    return m.group(1) if m else "utf-8"


def fetch_and_extract(raw_url: str, *, _transport: "httpx.BaseTransport | None" = None) -> dict:
    """Fetch ``raw_url`` (SSRF-guarded) and return extracted page text.

    Returns ``{"url", "title", "text", "chars", "truncated"}``. Raises
    ``ReadUrlError`` (renderer-safe) on any policy violation or fetch failure.
    ``_transport`` is a test seam for httpx.MockTransport; production passes None.
    """
    url = (raw_url or "").strip()
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    redirects = 0

    while True:
        validate_url(url)  # re-validate EVERY hop (incl. redirect targets)
        try:
            with httpx.Client(
                follow_redirects=False, timeout=TIMEOUT, headers=headers,
                transport=_transport,
            ) as client:
                with client.stream("GET", url) as resp:
                    # --- redirects: re-validate the next hop, capped ---
                    if resp.status_code in (301, 302, 303, 307, 308):
                        redirects += 1
                        if redirects > MAX_REDIRECTS:
                            raise ReadUrlError("Too many redirects.")
                        loc = resp.headers.get("location")
                        if not loc:
                            raise ReadUrlError("Got a redirect with no destination.")
                        url = urljoin(url, loc)
                        continue
                    if resp.status_code >= 400:
                        raise ReadUrlError(f"The page returned HTTP {resp.status_code}.")

                    ctype = (resp.headers.get("content-type", "")
                             .split(";")[0].strip().lower())
                    if ctype and ctype not in ALLOWED_CONTENT_TYPES:
                        raise ReadUrlError(
                            f"That URL isn't an HTML page (content type "
                            f"'{ctype}'); only web pages can be read."
                        )

                    clen = resp.headers.get("content-length")
                    if clen and clen.isdigit() and int(clen) > MAX_BYTES:
                        raise ReadUrlError("That page is too large to fetch.")

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ReadUrlError("That page exceeded the size limit while downloading.")
                        chunks.append(chunk)

                    charset = _charset_from_content_type(resp.headers.get("content-type", ""))
                    final_url = str(resp.url)
        except ReadUrlError:
            raise
        except httpx.TimeoutException:
            raise ReadUrlError("Timed out fetching that page.")
        except httpx.HTTPError as exc:
            raise ReadUrlError(f"Could not fetch that page ({type(exc).__name__}).")

        html = b"".join(chunks).decode(charset, errors="replace")
        break

    title, text = extract_main_text(html, final_url)
    if not text:
        return {"url": final_url, "title": title, "text": "", "chars": 0, "truncated": False}

    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS].rstrip() + "\n\n[... truncated ...]"
    return {
        "url": final_url,
        "title": title,
        "text": text,
        "chars": len(text),
        "truncated": truncated,
    }
