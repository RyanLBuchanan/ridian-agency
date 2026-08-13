"""Windows DPAPI (user scope) via ctypes — encryption at rest for local
secrets (v6.2).

CryptProtectData binds the encryption key to the current Windows user's
logon credentials; the key material is derived and managed by the OS, so
it is stored separately from anything this app writes BY CONSTRUCTION —
no key file exists to co-locate with the ciphertext.

Fail-closed: a blob encrypted under a different Windows account (or a
corrupt blob) raises DpapiError with an honest message; callers surface
"reconnect / re-enter" instead of crashing or guessing.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class DpapiError(Exception):
    """Raised when protect/unprotect fails. Message is operator-safe."""


class _DATA_BLOB(ctypes.Structure):  # noqa: N801 — win32 struct name
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _out_bytes(blob: "_DATA_BLOB") -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(data: bytes) -> bytes:
    """Encrypt ``data`` for the CURRENT Windows user."""
    try:
        crypt32 = ctypes.windll.crypt32
    except AttributeError as exc:      # non-Windows: never write plaintext
        raise DpapiError("DPAPI requires Windows.") from exc
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise DpapiError("CryptProtectData failed.")
    return _out_bytes(blob_out)


def unprotect(data: bytes) -> bytes:
    """Decrypt a blob produced by :func:`protect` for the SAME Windows user.
    Raises DpapiError (honest, non-crashing) for another user's blob or a
    corrupt one."""
    try:
        crypt32 = ctypes.windll.crypt32
    except AttributeError as exc:
        raise DpapiError("DPAPI requires Windows.") from exc
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise DpapiError(
            "Cannot decrypt — the data was encrypted by a different Windows "
            "account, or the file is corrupt.")
    return _out_bytes(blob_out)
