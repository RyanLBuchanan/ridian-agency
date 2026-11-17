# Project preference retention

Hidden and pinned sidebar choices now use `runtime_paths.data_dir()` alongside
existing settings: `%APPDATA%/Ridian Operator/hidden_projects.json` when frozen.
Development retains its existing location. Tests use the suite scratch store.
No tower data is read or modified by these tests.

The old frozen location was `resources/backend/_internal/hidden_projects.json`.
A new backend cannot recover it after an older installer removes that directory.
The NSIS `customInit` hook therefore runs the bundled PowerShell preflight BEFORE
installation/uninstallation. In electron-builder 25.1.8 it runs after
`initMultiUser` resolves the registered installation path. Both that path and
any `/D` override are checked, including the older non-_internal layout.

Preflight validates the JSON lists, rejects differing legacy/destination copies,
creates a missing destination exclusively, flushes it and verifies SHA-256.
Failure blocks the upgrade; a failed new copy is removed. Existing destination
files are never overwritten. Legacy sources remain untouched until verification;
only the normal subsequent installer replacement can remove the old bundle.
Unknown/malformed legacy files also block installation for manual recovery.

The backend provides a fallback copy when first started from an unchanged legacy
bundle and includes preferences in opt-in `RIDIAN_MIGRATE_FROM` migration.
An existing persistent file is authoritative during normal app use, so later
preference changes are not reset from the retained stale legacy file.

Rollback of a failed migration retains the original. Rolling back the application
to an older release does NOT teach that release the persistent path: retain a
separate copy of preferences before a deliberate application downgrade. This
change does not modify old installers. Already-deleted preferences cannot be
recovered without a backup. Resolve conflicts explicitly outside the installer;
no automatic merge or choice of one user's preferences is attempted.

Validation: run `python -m pytest apps/api/tests/test_project_preference_retention.py
apps/api/tests/test_projects.py apps/api/tests/test_frozen_paths.py
apps/api/tests/test_state_guard.py -q`. Seven PowerShell preflight cases require
Windows and skip elsewhere. A native installer build and actual old-to-new
upgrade remain required before release. No workflow run is authorized by this PR.
