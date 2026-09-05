"""Pytest bootstrap: put apps/api on sys.path so `import app...` works
regardless of where pytest is invoked from."""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _portable_dpapi_for_non_windows(monkeypatch):
    """Let cross-platform tests exercise encrypted-store control flow.

    Production deliberately fails closed without Windows DPAPI. Tests that
    validate real DPAPI are already Windows-only; the remaining settings/QBO
    tests need a reversible, non-plaintext stand-in rather than failing before
    they reach the behavior under test.
    """
    if sys.platform == "win32":
        return
    from app.services import dpapi

    prefix = b"RIDIAN-TEST-DPAPI\0"

    def protect(data: bytes) -> bytes:
        return prefix + base64.urlsafe_b64encode(bytes(data)[::-1])

    def unprotect(blob: bytes) -> bytes:
        if not bytes(blob).startswith(prefix):
            raise dpapi.DpapiError("Test DPAPI blob is invalid.")
        try:
            return base64.urlsafe_b64decode(bytes(blob)[len(prefix):])[::-1]
        except Exception as exc:
            raise dpapi.DpapiError("Test DPAPI blob is invalid.") from exc

    monkeypatch.setattr(dpapi, "protect", protect)
    monkeypatch.setattr(dpapi, "unprotect", unprotect)
