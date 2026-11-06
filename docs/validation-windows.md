# Native Windows validation — 2026-11-04

- Native Windows Python 3.13: `py -3.13 -X utf8 -B scripts/run_tests.py` —
  99 tests passed in 109.768 seconds.
- Python 3.9: `py -3.9 -X utf8 -B -m unittest discover -s tests -p test_windows.py -v`
  — seven platform tests passed.
- Four `tests/*.js` suites passed with Node; all `static/*.js` syntax checks passed.
- Python compileall and `git diff --check` passed.
- Both macOS and Windows ZIPs built; deterministic ZIP and manifest verification
  are covered by tests. macOS execution was not available locally; CI now includes
  macOS and Windows runners.
- Installer integration covers repeat installation, deletion of downloaded
  packages, hooks independent of the service, migration, occupied ports, upgrade,
  database rollback after failed startup, settings preservation, uninstall and
  reinstall. Windows Startup script was actually executed with cscript.
- Unicode/space paths, percent/exclamation/ampersand characters in CMD launchers,
  cross-process lock contention, native source hook migration and stale process
  ownership checks passed.

Installed the verified source package with native Python 3.13 under
`%LOCALAPPDATA%\Programs\Token Lens`, with autostart disabled. The initial
LocalAppData-root installation was retired after discovering MSIX redirection;
its generated files were removed. Programs avoids that redirection, and the
installer now canonicalizes an existing installation root before writing hooks.

Doctor reports the service running and hooks configured. The listener is only
`127.0.0.1:8765`. The dashboard, favicon, CSS and both JavaScript files returned
HTTP 200. All seven real history files completed scanning without errors, with
814 usage records at the verification snapshot. All 21 installed payload files
match the generated Windows ZIP byte for byte. Browser opening was invoked through
the installed management command.

Hook trust remains a user action in Codex; writing hooks.json does not assert
trust. Periodic history scanning works independently. No telemetry exporter was
enabled, and no model/API request is needed for collection.

## Push preflight — 2026-11-06

The equal-size truncation fixture now explicitly advances its modification time.
Windows can assign identical timestamps to rapid writes, which previously made
this fixture intermittently indistinguishable from an unchanged file. This keeps
the existing incremental-scan contract and makes the rewrite test deterministic.
