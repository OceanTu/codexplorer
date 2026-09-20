# Side Chat Coverage Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in this task. Steps use checkbox syntax.

**Goal:** Recover missing local side chats and collect future measured usage without duplicate charges.

**Architecture:** Keep diagnostic observations separate from accounted usage. A local OTLP JSON receiver adds measured evidence, reconciled against native rollout records. The existing dashboard gains a compact disclosure list and collection health details.

**Tech Stack:** Python 3.9+ standard library, SQLite, native JavaScript.

**Spec:** docs/superpowers/specs/2026-09-20-sidechat-coverage.md

## Global Constraints
- Python 3.9+; no product dependencies.
- Preserve per-request conversation interaction and existing totals.
- Unknown usage is never zero; context samples never become billed tokens.
- Local collection only; preserve existing Codex configuration.

## Review Focus
- Source DB rotation/recreation must not skip observations.
- A later rollout must supersede duplicate telemetry without doubling costs.
- Missing cache counters must not silently become paid uncached input.
- Unrelated same-project chats must not be automatically merged.
- Repeated enable/disable must preserve user configuration edits.

### Task 1: Historical diagnostic recovery
Files: codex_usage/supplemental.py, store.py, importer.py, api.py, tests/test_supplemental.py.
Interface: scan_diagnostics(con, home) -> report; supplemental(con, params, session=None) -> list.
- [ ] Add tests through Importer.scan and API.overview asserting recovered prompt, unknown usage, stable totals and idempotence.
```python
self.assertEqual(API(con).overview({})['supplemental'][0]['preview'], 'OCR status?')
self.assertEqual(API(con).overview({})['summary']['total_tokens'], 0)
```
- [ ] Run unittest discover -s tests -p test_supplemental.py; expect missing supplemental assertion failure.
- [ ] Add schema migration, bounded incremental parser, independent observations and API results; record source identity and cursor anchor to detect recreation.
- [ ] Test log rotation, malformed Rust strings, filtering, grouping and ordinary rollout exclusion; run entire Python suite.
- [ ] Commit historical recovery.

### Task 2: Local telemetry and configuration
Files: codex_usage/telemetry.py, app.py, lifecycle.py, supplemental.py, tests/test_telemetry.py.
Interfaces: ingest(con, payload), configure(home, data, port, enabled), telemetry_status(con).
- [ ] Capture a minimal real ephemeral run locally to establish actual field names, keep raw evidence in ignored .data.
- [ ] Add behavioral tests for replay, complete/incomplete counts, same response in rollout, missing response IDs and native records arriving later.
```python
self.assertEqual(API(con).overview({})['summary']['requests'], 1)
```
- [ ] Verify tests fail before implementing ingestion and reconciliation.
- [ ] Add guarded POST /v1/logs and configuration writer; tests exercise HTTP rejection and round-trip ingest, managed block and conflict preservation.
- [ ] Run full Python suite and commit.

### Task 3: Display, release and end-to-end validation
Files: static/app.js, static/style.css, tests/test_supplemental_frontend.js, packaging/README.md, scripts/build_release.py, VERSION.
- [ ] Add JS render tests: escaped recovered prompt, unknown cost, compact details, no aggregation timeline.
- [ ] Verify failed render expectation before implementing panels and association control.
- [ ] Integrate packaging allowlist and documentation, update version, run Python suite, all JS tests and syntax checks.
- [ ] Validate real history against an index copy; confirm OCR prompt appears without changing accounted totals. Run real ephemeral telemetry comparison.
- [ ] Obtain final fresh code review while performing release checks, address important findings with failing regression tests first.
- [ ] Build and verify ZIP, update installed version, enable local telemetry config, verify service and explain pending Codex restart.

## Execution record
User confirmed the two-source plan and requested proceeding. Execute within this task; no additional design approval required under session authorization. One isolated worktree; no changes to running Codex until final local telemetry setup.
