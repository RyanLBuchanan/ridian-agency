# Packaged backend startup diagnostics

The packaged desktop app starts `resources/backend/ridian-backend.exe` with
the existing hidden-window, working-directory and version environment options.
It waits up to 60 seconds for the existing service/data-directory identity
check to succeed before opening the main window. A second desktop launch
during this wait leaves the first launch in control.

Startup failures display fixed guidance and one diagnostic code:

| Code | Meaning | Suggested action |
| --- | --- | --- |
| `BACKEND_MISSING` | The bundled executable is absent. | Run the installer again to repair the installation. |
| `BACKEND_LAUNCH_FAILED` | Starting the backend failed. | Check whether Windows Security blocked the app; repair the installation if needed. |
| `BACKEND_EXITED` | The child exited before readiness. | Relaunch; if it repeats, repair the installation and report the code. |
| `BACKEND_STARTUP_TIMEOUT` | The backend did not pass readiness within 60 seconds. | Relaunch; check Windows Security and report the code if it repeats. |

Do not delete the application data folder as a troubleshooting step. These
diagnostics do not read or rewrite settings or memory. They include no child
output, exception text, resolved paths, environment values or health-response
contents. No diagnostic file is written.

A failed startup stops only the child created by that launch attempt. An
existing backend still requires the same identity check before reuse; it is
never owned or killed by this launch. Development mode still uses its external
backend. Successful packaged startup retains the existing drain-on-quit path.

## Regression checks

From the repository root:

```sh
node --check desktop/main.js
node --test desktop/tests/backend-startup.test.cjs
python -m pytest apps/api/tests/test_frozen_paths.py apps/api/tests/test_state_guard.py apps/api/tests/test_installer_icon.py apps/api/tests/test_run_checkpoint.py apps/api/tests/test_backup_restore.py apps/api/tests/test_audit_log.py apps/api/tests/test_ui_grounding_and_packaging.py apps/api/tests/test_single_instance.py apps/api/tests/test_dpapi_at_rest.py -q
```

The Node tests execute the supervisor with mocked Electron, process and network
boundaries and synthetic data. They verify successful launch options, readiness
and identity behavior, safe failure messages, deadlines and process ownership.
They do not launch an installed Windows application. Windows-only tests skipped
by pytest are **NOT RUN**, not passed.

Windows installer build, clean installation without Python/Node, actual upgrade
preservation, Windows DPAPI/single-instance behavior and tower verification
remain **NOT RUN** until exercised on Windows. A slow machine exceeding the
60-second readiness deadline will receive the timeout message. Native installer
and two-computer validation are still required before a release-readiness claim.
