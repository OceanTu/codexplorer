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
- [x] Add tests through Importer.scan and API.overview asserting recovered prompt, unknown usage, stable totals and idempotence.
```python
self.assertEqual(API(con).overview({})['supplemental'][0]['preview'], 'OCR status?')
self.assertEqual(API(con).overview({})['summary']['total_tokens'], 0)
```
- [x] Run unittest discover -s tests -p test_supplemental.py; expect missing supplemental assertion failure.
- [x] Add schema migration, bounded incremental parser, independent observations and API results; record source identity and cursor anchor to detect recreation.
- [x] Test log rotation, malformed Rust strings, filtering, grouping and ordinary rollout exclusion; run entire Python suite.
- [x] Commit historical recovery.

### Task 2: Local telemetry and configuration
Files: codex_usage/telemetry.py, app.py, lifecycle.py, supplemental.py, tests/test_telemetry.py.
Interfaces: ingest(con, payload), configure(home, data, port, enabled), telemetry_status(con).
- [x] Capture a minimal real ephemeral run locally to establish actual field names, keep raw evidence in ignored .data.
- [x] Add behavioral tests for replay, complete/incomplete counts, same response in rollout, missing response IDs and native records arriving later.
```python
self.assertEqual(API(con).overview({})['summary']['requests'], 1)
```
- [x] Verify tests fail before implementing ingestion and reconciliation.
- [x] Add guarded POST /v1/logs and configuration writer; tests exercise HTTP rejection and round-trip ingest, managed block and conflict preservation.
- [x] Run full Python suite and commit.

### Task 3: Display, release and end-to-end validation
Files: static/app.js, static/style.css, tests/test_supplemental_frontend.js, packaging/README.md, scripts/build_release.py, VERSION.
- [x] Add JS render tests: escaped recovered prompt, unknown cost, compact details, no aggregation timeline.
- [x] Verify failed render expectation before implementing panels and association control.
- [x] Integrate packaging allowlist and documentation, update version, run Python suite, all JS tests and syntax checks.
- [x] Validate real history against an index copy; confirm OCR prompt appears without changing accounted totals. Run real ephemeral telemetry comparison.
- [x] Obtain final fresh code review while performing release checks, address important findings with failing regression tests first.
- [ ] Build and verify ZIP, update installed version, enable local telemetry config, verify service and explain pending Codex restart.

## Execution record
User confirmed the two-source plan and requested proceeding. Execute within this task; no additional design approval required under session authorization. One isolated worktree; no changes to running Codex until final local telemetry setup.


### Implementation ledger
- Historical recovery: tests were observed failing before implementation, now pass; real diagnostics recover the exact OCR question, five context samples and four exec calls. Copied native usage count and sum are unchanged.
- Telemetry: actual ephemeral CLI emitted completed events with string input/output counters and integer cached/reasoning counters, but no response/turn ID. A startup prewarm completion preceded the real completion.
- Ruling: count telemetry only when both event and diagnostic sample have a unique match on thread, model, token total and a narrow time interval, and no persisted thread exists — prevents known prewarm and cross-source duplication — may undercount unmatched events; show these as pending rather than zero.
- Ruling: keep an incomplete Turn visible when only some samples are matched — prevents falsely claiming complete coverage — counts remain partial until later evidence arrives.
- UI regression first failed on missing supplementalPanel, then passed. Existing Turn lazy expansion and multi-agent chart tests remain green.
- Verification: Python baseline 62 tests passed; first integrated suite 79 tests passed. Added coverage since then is verified again before release.
- Real app-server ephemeral validation: model gpt-5.6-luna, two response.completed events; one prewarm excluded, one diagnostic-matched request totals 9,137 tokens (9,132 input, 5 output, 5,888 cached), identical to app-server thread/tokenUsage/updated. Current app-server sends timeUnixNano=0; use timezone-qualified event.timestamp instead. Replay ignores observedTimeUnixNano, with regression RED→GREEN.
- Installation integration verifies telemetry on after deleting the extracted package, configuration retained across upgrade, and own block/token removed on uninstall. Full suite 82 passed before the final timestamp regression; final suite follows review.

- Final review: independent fresh-context reviewer identified three P2 issues. All fixed with focused regressions observed failing before implementation: in-place diagnostic reset with reused IDs and identical checkpoint body, literal thread_settings inside user text, quoted TOML OTel keys. No deferred findings or declined judgments.
- Browser verification: the original session shows a collapsed supplemental section; opening the single OCR row displays the exact input, 35,742 → 46,733 → 59,301 → 66,080 → 68,533 context observations, four exec calls, unknown usage, and the explicitly selected parent session. Collection page renders local telemetry controls and source limits.
