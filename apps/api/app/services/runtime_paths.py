"""Frozen-vs-dev path resolution (v4.2, self-contained backend) + the
state-guard gate (v4.4).

THE contract (approved 2026-07-21): branch on ``sys.frozen`` only.
  - DEV (uvicorn from the venv): every path is EXACTLY what it was before
    this module existed — apps/api for writable state, source tree for
    static resources. Byte-identical behavior.
  - FROZEN (PyInstaller build): writable state lives in
    ``%APPDATA%\\Ridian Operator\\`` (settings, memory/state store, OAuth
    tokens, logs, outputs); static resources (prompts, static/) come from
    the bundle. Secrets are runtime config files in the data dir — NEVER
    frozen into the binary.

v4.4 state-guard gate (born of a 2026-07-24 diagnostic probe that hit the
REAL backend on port 8000 and overwrote real credentials): test and
diagnostic contexts are detected deterministically and REFUSED before any
byte lands in real state — the same refuse-before-the-artifact pattern as
the recipient/research-plan/memory gates.
  - ``in_test_context()``: pytest stamps PYTEST_CURRENT_TEST into the env
    for every running test; diagnostic harnesses set RIDIAN_SANDBOX=1.
  - ``guard_real_state_write()``: wired into every settings/credential
    writer; raises SandboxViolation when a test context targets a real
    store (dev apps/api, or this user's actual roaming APPDATA — resolved
    from the HOME dir so an APPDATA env redirect cannot mask it).
  - RIDIAN_SANDBOX=1 additionally REQUIRES RIDIAN_DATA_DIR (a non-real
    scratch dir — the only sanctioned exception to the sys.frozen-only
    contract) and refuses to bind the real backend port
    (``resolve_backend_port``): a probe aimed at 8000 can only ever reach
    the real app, and a sandboxed backend can only ever be reached on a
    port the harness explicitly chose.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Ridian Operator"

# apps/api/app/services/runtime_paths.py -> apps/api (the historical base
# for every writable file in dev mode).
_API_DIR = Path(__file__).resolve().parent.parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


class SandboxViolation(RuntimeError):
    """A test/diagnostic context tried to touch real state or the real port."""


def in_test_context() -> bool:
    """Deterministic test/diagnostic detection — no heuristics: pytest sets
    PYTEST_CURRENT_TEST for every running test; harnesses set RIDIAN_SANDBOX."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("RIDIAN_SANDBOX"))


def _real_store_dirs() -> tuple[Path, Path]:
    """The two REAL state locations, independent of env redirection: the dev
    store (apps/api) and this user's actual roaming APPDATA store (derived
    from HOME so an APPDATA override cannot disguise the real one)."""
    return (_API_DIR, Path.home() / "AppData" / "Roaming" / APP_DIR_NAME)


def guard_real_state_write(target: Path) -> None:
    """v4.4 state-guard gate: refuse-before-the-artifact.

    Wired into every settings/credential writer. In a test/diagnostic
    context a write aimed at a real store raises BEFORE any byte lands;
    outside test contexts this is a no-op (the real app is unaffected)."""
    if not in_test_context():
        return
    resolved = Path(target).resolve()
    for real in _real_store_dirs():
        try:
            resolved.relative_to(real.resolve())
        except ValueError:
            continue
        raise SandboxViolation(
            f"Refused: write to the real state store ({resolved}) from a "
            "test/diagnostic context. Sandbox the writer — monkeypatch its "
            "*_PATH constant, or run the process with RIDIAN_SANDBOX=1 and "
            "RIDIAN_DATA_DIR pointing at a scratch directory.")


DEFAULT_BACKEND_PORT = 8000


def resolve_backend_port() -> int:
    """Port for the backend to bind. RIDIAN_PORT overrides the default; a
    sandboxed process must NOT occupy the real port, so probes aimed at
    8000 can only ever reach the real app."""
    port = int(os.environ.get("RIDIAN_PORT", str(DEFAULT_BACKEND_PORT)))
    if os.environ.get("RIDIAN_SANDBOX") and port == DEFAULT_BACKEND_PORT:
        raise SandboxViolation(
            f"A sandboxed backend must not bind the real port "
            f"{DEFAULT_BACKEND_PORT} — set RIDIAN_PORT to a scratch port.")
    return port


def data_dir() -> Path:
    """Base for ALL writable state. Dev: apps/api (unchanged). Frozen:
    %APPDATA%/Ridian Operator (created on first use).

    RIDIAN_SANDBOX=1 (test/diagnostic harnesses only) is the single
    sanctioned exception to the sys.frozen-only contract: it REQUIRES
    RIDIAN_DATA_DIR naming a non-real scratch dir and refuses to resolve
    otherwise, so a sandboxed process cannot reach real state even by
    accident (fails at import of the first state-holding service)."""
    if os.environ.get("RIDIAN_SANDBOX"):
        override = (os.environ.get("RIDIAN_DATA_DIR") or "").strip()
        if not override:
            raise SandboxViolation(
                "RIDIAN_SANDBOX is set but RIDIAN_DATA_DIR is not — a sandboxed "
                "process must name its own data dir, never default to real state.")
        d = Path(override)
        for real in _real_store_dirs():
            if d.resolve() == real.resolve():
                raise SandboxViolation(
                    f"RIDIAN_DATA_DIR points at the real state store ({real}) — "
                    "pick a scratch directory.")
        d.mkdir(parents=True, exist_ok=True)
        return d
    if not is_frozen():
        return _API_DIR
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# Everything writable a legacy (repo-based) install may hold.
_MIGRATABLE = ("local_settings.json", "google_credentials.json",
               "google_token.json", "quickbooks_token.json", ".env", "state")


def migrate_legacy_state(src: Path, dst: Path) -> list[str]:
    """Byte-copy writable state from a legacy layout (a repo's apps/api)
    into the data dir. shutil.copy2/copytree ONLY — files are never parsed,
    filtered, or rewritten, so memory provenance stamps
    (written_by/source_op) survive BYTE-IDENTICAL by construction (pinned
    by test_frozen_paths). Existing destination files are never overwritten.
    Returns the names copied."""
    import shutil
    copied: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    for name in _MIGRATABLE:
        s, d = src / name, dst / name
        if not s.exists() or d.exists():
            continue
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        copied.append(name)
    return copied


def maybe_migrate_on_first_run() -> list[str]:
    """Frozen-only, opt-in: RIDIAN_MIGRATE_FROM=<legacy apps/api dir> copies
    state into APPDATA on launch. Unset (the clean-machine case) = no-op."""
    src = os.environ.get("RIDIAN_MIGRATE_FROM", "")
    if not (is_frozen() and src):
        return []
    return migrate_legacy_state(Path(src), data_dir())


def resource_base() -> Path:
    """Base for READ-ONLY bundled resources (prompt files, static/). Dev:
    apps/api (the source tree). Frozen: PyInstaller's bundle dir
    (sys._MEIPASS), where --add-data placed them at the same relative
    layout (app/agents/prompts, app/static)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _API_DIR
