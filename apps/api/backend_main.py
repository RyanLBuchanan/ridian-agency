"""Frozen backend entrypoint (v4.2) — PyInstaller builds THIS file.

Imports the FastAPI ``app`` OBJECT and hands it to ``uvicorn.run``
programmatically. Never launch by module string ("app.main:app") here:
PyInstaller cannot trace that dynamic import, and the frozen exe would
ship without the application inside it. Dev mode does not use this file —
`npm start` / the .bat keep launching uvicorn from the venv exactly as
before.
"""

from __future__ import annotations

import multiprocessing


def main() -> None:
    # --noconsole builds have NO console: sys.stdout/stderr are None, and
    # uvicorn's first log write would crash the server silently. Give them
    # devnull handles; real forensics go to the rotating file log in the
    # APPDATA state dir (configured by app.main at import).
    import os
    import sys

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

    # The static import chain from app.main pulls every service/tool module,
    # which is exactly what lets PyInstaller bundle the whole backend.
    # Optional legacy-state migration BEFORE any service reads its files.
    from app.services.runtime_paths import maybe_migrate_on_first_run

    maybe_migrate_on_first_run()

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    # Required under PyInstaller on Windows: without it, any accidental
    # child-process spawn re-executes the exe and forks the server.
    multiprocessing.freeze_support()
    main()
