"""Tailscale-HTTPS listener (v6.9.7) — the ground Web Push stands on.

Browsers allow service workers (and therefore push subscriptions) ONLY in
a secure context. The companion's plain-HTTP LAN/tailnet URLs are not
one, so over http:// the phone can never subscribe — no library changes
that. Tailscale's own cert machinery ('tailscale cert') issues this PC a
real, publicly-trusted certificate for its ts.net name, which makes
https://<node>.<tailnet>.ts.net the ONE address where notifications can
be enabled — with zero trust-installation dance on the phone.

The listener serves the SAME app behind the SAME CompanionGate (real peer
addresses, so pairing rules are identical). TLS adds transport privacy;
it never adds access.

FAIL HONEST (item 6): every reason HTTPS is absent — no tailscale.exe,
tailnet not running, HTTPS not enabled on the tailnet, cert fetch failed
— lands in ``state["error"]`` and is shown in Settings. Nothing here is
a silent no-op. A sandboxed process never invokes the CLI or binds.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .runtime_paths import data_dir

log = logging.getLogger("ridian.companion.tls")

DEFAULT_TLS_PORT = 8443
_RENEW_BEFORE_DAYS = 30          # re-run 'tailscale cert' at startup when close
_CLI_TIMEOUT_SECONDS = 30

# Module state, read by /companion/status. "" url + "" error = not attempted
# (companion off / dev mode); "" url + error = attempted and failed, honestly.
state = {"url": "", "host": "", "error": ""}


def _tailscale_exe() -> Optional[str]:
    exe = shutil.which("tailscale")
    if exe:
        return exe
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidate = default / "Tailscale" / "tailscale.exe"
    return str(candidate) if candidate.exists() else None


def _dns_name(exe: str) -> str:
    """This node's ts.net name, from the CLI's own status. Every failure
    message NAMES THE FIX, not just the condition — 'Tailscale is NoState'
    tells the operator nothing to do; 'start Tailscale, then restart
    Ridian' does."""
    out = subprocess.run([exe, "status", "--json"], capture_output=True,
                         timeout=_CLI_TIMEOUT_SECONDS, text=True)
    if out.returncode != 0:
        raise RuntimeError(
            f"Could not ask Tailscale for its status "
            f"({out.stderr.strip()[:150]}) — start Tailscale from the "
            "system tray, then restart Ridian.")
    data = json.loads(out.stdout)
    if data.get("BackendState") != "Running":
        raise RuntimeError(
            f"Tailscale isn't running on this PC (state: "
            f"{data.get('BackendState', 'absent')}) — start Tailscale from "
            "the system tray and sign in, then restart Ridian.")
    name = str((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if not name:
        raise RuntimeError(
            "This PC has no MagicDNS name — enable DNS → MagicDNS in the "
            "Tailscale admin console, then restart Ridian.")
    return name


def _cert_paths(host: str) -> tuple[Path, Path]:
    tls_dir = data_dir() / "tls"
    return tls_dir / f"{host}.crt", tls_dir / f"{host}.key"


def _cert_days_left(cert_path: Path) -> float:
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return (cert.not_valid_after_utc
            - _dt.datetime.now(_dt.timezone.utc)).total_seconds() / 86400


def ensure_cert() -> tuple[str, Path, Path]:
    """(host, cert, key) with a certificate valid for >= 30 days. Reuses the
    stored one when it is; otherwise invokes 'tailscale cert' (which is
    also how renewal happens — at startup, no cron)."""
    exe = _tailscale_exe()
    if not exe:
        raise RuntimeError(
            "Tailscale isn't installed on this PC (tailscale.exe not found) "
            "— install it from tailscale.com and sign in, then restart "
            "Ridian.")
    host = _dns_name(exe)
    cert, key = _cert_paths(host)
    if cert.exists() and key.exists():
        try:
            if _cert_days_left(cert) > _RENEW_BEFORE_DAYS:
                return host, cert, key
        except Exception as exc:  # noqa: BLE001 — unreadable = refetch
            log.warning("tls.cert_unreadable type=%s", type(exc).__name__)
    cert.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        [exe, "cert", "--cert-file", str(cert), "--key-file", str(key), host],
        capture_output=True, timeout=_CLI_TIMEOUT_SECONDS, text=True)
    if out.returncode != 0:
        raise RuntimeError(
            f"'tailscale cert' failed for {host}: {out.stderr.strip()[:250]} "
            "— enable DNS → HTTPS Certificates in the Tailscale admin "
            "console, then restart Ridian.")
    log.info("tls.cert_obtained host=%s", host)
    return host, cert, key


def _make_server(app, port: int, cert: Path, key: Path):
    """Factory split out so tests can substitute a fake server."""
    import uvicorn
    return uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=port, log_level="info",
        ssl_certfile=str(cert), ssl_keyfile=str(key)))


_BIND_WAIT_SECONDS = 15


def start_if_possible(app, port: int = DEFAULT_TLS_PORT) -> bool:
    """Called by the frozen entrypoint AFTER the companion opted onto the
    network. Never raises; every failure lands in state['error'].

    v6.9.8: the URL is claimed only AFTER the listener actually bound
    (server.started), never merely after the thread spawned — a bind or
    ssl failure inside the thread must surface as an error in Settings,
    not as an https URL with nothing behind it."""
    if os.environ.get("RIDIAN_SANDBOX"):
        state["error"] = "sandboxed process — HTTPS listener not started."
        return False
    try:
        host, cert, key = ensure_cert()
    except Exception as exc:  # noqa: BLE001 — the message IS the surface
        state["error"] = str(exc)
        log.warning("tls.unavailable %s", exc)
        return False

    try:
        server = _make_server(app, port, cert, key)
    except Exception as exc:  # noqa: BLE001
        state["error"] = (f"HTTPS listener could not be configured: {exc} — "
                          "check backend.log, then restart Ridian.")
        log.warning("tls.config_failed %s", exc)
        return False
    thread = threading.Thread(target=server.run, daemon=True, name="ridian-tls")
    thread.start()
    import time as _time
    deadline = _time.monotonic() + _BIND_WAIT_SECONDS
    while not getattr(server, "started", False):
        if not thread.is_alive():
            state["error"] = (f"HTTPS listener died before binding port {port} "
                              "— close whatever else is using that port (or "
                              "set RIDIAN_TLS_PORT to a free one), then "
                              "restart Ridian. Details in backend.log.")
            log.warning("tls.bind_failed port=%s", port)
            return False
        if _time.monotonic() > deadline:
            state["error"] = (f"HTTPS listener did not confirm its bind on "
                              f"port {port} within {_BIND_WAIT_SECONDS}s — "
                              "restart Ridian; if it repeats, check "
                              "backend.log.")
            log.warning("tls.bind_timeout port=%s", port)
            return False
        _time.sleep(0.2)
    state["host"] = host
    state["url"] = f"https://{host}:{port}/companion"
    state["error"] = ""
    log.info("tls.listening host=%s port=%s", host, port)
    return True
