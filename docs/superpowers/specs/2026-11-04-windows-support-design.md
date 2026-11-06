# Windows support

The requested outcome is a native Windows installation of the existing Token Lens
project, with no WSL, administrator privileges, or third-party Python dependencies.
Keep Python 3.9+ and existing macOS behavior. Use the installed native Python 3.13
for this machine rather than the MSYS Python found first on PATH.

Use msvcrt byte-range locks on Windows and flock elsewhere. Store Windows runtime
and data in LOCALAPPDATA/Programs/Token Lens; use an atomic relative version-pointer file
instead of privileged symlinks. Add CMD installation/management launchers, a Start
Menu shortcut, hidden background workers, browser opening, and optional per-user
Startup entry (off by default). Verify process ownership before stopping services.
Preserve verified manifests, database backup, rollback, existing hooks and settings.

Alternatives considered: WSL would not provide native Windows support; a bundled
executable would add a packaging toolchain and runtime distribution. Extending the
existing standard-library installer is sufficient and keeps the current design.

Verify lock contention across processes, Unicode/space paths, deterministic ZIPs,
installation after deleting the extracted package, repeat install, upgrade,
startup failure rollback, hook execution, autostart, and data-preserving uninstall.
Run existing Python and JavaScript tests. Install the verified Windows package
locally with login autostart disabled, and verify real history import and HTTP API.
