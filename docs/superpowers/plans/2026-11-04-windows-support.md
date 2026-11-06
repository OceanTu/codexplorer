# Windows Support Implementation Plan

**Goal:** Adapt and install Token Lens natively on Windows.
**Architecture:** Add a small platform module; reuse the existing versioned installer.
**Tech Stack:** Python 3.9+ standard library, CMD, SQLite, existing web frontend.
**Spec:** ../specs/2026-11-04-windows-support-design.md

## Constraints and review focus

No third-party runtime dependencies or elevated privileges. Keep macOS support.
Exercise real cross-process contention, Chinese/space paths, a foreign occupied
port, stale PIDs, failed upgrade recovery, and independent installed runtime.

## Tasks

- [x] Add Windows regression tests and observe failure: native app startup,
  lock contention/release, deterministic Windows package verification.
- [x] Add `codex_usage/platform_support.py` for locks, background options,
  command quoting and current-version resolution; update app and lifecycle.
- [x] Extend installer/build with `--platform windows`, CMD entries, UTF-8 I/O,
  and rollback using a version-pointer file. Adapt integration tests to Windows.
- [x] Run Python and JS suites, fix observed portability failures, add Windows CI
  and document installation, management, data paths, autostart and hooks.
- [x] Build and install locally, verify health, history import, management and
  launchers. Leave source changes reviewable on codex/windows-support.

Execution result: 99 Python tests pass on native Python 3.13; seven platform
regressions pass on Python 3.9. All four JS suites and syntax checks pass.
Code review identified native Windows hook migration parsing; fixed with a
failing-then-passing regression. The installed runtime matches all 21 payload
files in the Windows ZIP. Real history import completed without errors.

Environment finding: a packaged Windows host can redirect LocalAppData writes.
Use LocalAppData/Programs/Token Lens and canonicalize the root after creation.
This keeps installed launchers and hook configuration consistent.
