# Integrated Windows validation gate

Main ce9e362 (preference retention, PR #13) is merged non-destructively into
PR #12 (startup diagnostics). The resulting installer includes both changes.
The workflow stays manual-only; no new run is authorized by this integration.

## Procedure prepared for the next approved run

1. Run mocked startup tests, portable evidence gates, and the native regression
   suite including test_project_preference_retention.py. Fail on any skip/error/
   failure, or if any of the seven named PowerShell preflight cases is absent.
2. Build a pinned pre-fix installer from f286d5e using a separate checkout, then
   build the combined candidate. Both use the same installed dependency tools;
   neither signs with credentials or publishes. This tests a same-version
   installer replacement, not a new released version or arbitrary downgrades.
3. Install the old NSIS package into fresh runner scratch. Do not launch it.
   Seed its actual _internal legacy preference file with synthetic hidden/pinned
   choices. Seed the separate scratch data directory with synthetic settings
   and contacts containing provenance. No credentials or provider calls.
4. Run the actual candidate NSIS installer over that installation. Its preflight
   must migrate preferences before uninstalling the old package. The installer
   hook follows RIDIAN_SANDBOX/RIDIAN_DATA_DIR so CI and backend share scratch
   storage; normal installs keep the existing roaming location.
5. Verify settings/memory bytes, migrated preferences and removal of the legacy
   bundle file. Compare installed backend/resources against candidate build.
6. Launch the installed candidate without Python/Node on the child PATH. Verify
   health/version/data identity, desktop window and backend process ownership.
   Read recent/hidden project APIs to verify hidden filtering and pinned status.
7. Close the desktop normally, require backend shutdown, recheck synthetic data
   bytes and installer size/SHA-256, then upload evidence and installer for seven
   days. Job-object cleanup kills owned descendants on failures.

The native preflight tests cover first install, migration, existing destination,
identical copies, conflicts, malformed input and simulated removal of old files.
Python regression tests separately exercise rollback after copy verification fails.
Actual installer upgrade preservation remains NOT RUN until step 4 completes on
Windows. Physical tower/clean-PC testing and arbitrary downgrade compatibility
remain outside this CI procedure.

## Controls and current evidence

Standard windows-2022, one job, 40-minute job timeout, contents:read,
checkout credentials not persisted, no caches, no secret inputs, publish never,
seven-day artifact retention. Two builds and one baseline-to-candidate upgrade
are expected to take roughly 8–15 minutes, bounded by 40 minutes total. Preserve
Owner-verified $0 budget/usage-blocked limits. No paid or larger runner.

Linux: 898 passed, 5 failed, 29 skipped in the full Python suite. All five
failures reproduce on unchanged main ce9e362: companion stale pairing pruning,
owner-job result contract, two snapshot/date assertions, and sidebar layout.
Targeted startup/packaging/state/preference suite: 62 passed, 17 Windows skips.
23 mocked startup cases pass. Electron UI layout/cold-start harnesses could not
execute here because Electron is not installed; three portable JS checks pass.
Native Windows results from the previous run apply only to its older code.
