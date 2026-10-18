# Local change record

These are implementation milestones, not published releases. No Git tags, public package, hosted deployment, or release permission is implied.

## 0.6.0 — Phase 6, 2026-10-15

- Added Python 3.12 private packaging, console entry points and bundled sample inputs.
- Added dependency constraints, isolated installation/build/wheel checks and CI configuration.
- Preserved source defaults; installed outputs use the working directory rather than site-packages.
- Added architecture, attribution, provenance and release validation records.
- Made benchmark reruns refuse existing evidence instead of overwriting fixture inputs.

## Phase 5 — 2026-10-15

Added UTC bucket/count/rate diagnostics, evidence-linked alerts, ranked recurrences, tentative hypotheses, rule-based production status, CLI controls and opt-in exit 3. Streamed export schema is 3; SQLite schema remains 1.

## Phase 4 — 2026-10-15

Added bounded ingestion/resource limits, revision-backed transactional SQLite persistence, fingerprint reuse, exact SQL selection/grouping, bounded pages and streamed exports. Recorded Python-allocation measurements with explicit native/RSS limits.

## Phase 3 — 2026-10-15

Added UTC/severity/literal-text filters, conservative recurring signatures, representative/member evidence and consistent selected text/CSV/JSON exports.

## Phase 2 — 2026-10-15

Added typed shared events, JSON Lines, UTC timestamp policy, bounded supported Python tracebacks and original source evidence.

## Phases 0–1 — 2026-10-15

Audited the inherited implementation; corrected parsing/accounting/status and failed-input behavior; guarded side effects; added report/database path safety, transactional issue snapshots and automated regressions.

Inherited authors and unresolved release rights are recorded in [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md). Detailed commands and measured results are in [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).
