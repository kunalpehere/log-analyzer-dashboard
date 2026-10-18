# Log Analyzer Dashboard

A local Python application for inspecting log files through a command-line workflow and a Streamlit dashboard. It provides validated text/JSON Lines parsing, UTC timestamps, bounded ingestion, persistent analysis runs/source revisions, paginated investigations, recurring-error groups, streamed text/CSV/JSON exports, configurable evidence-based diagnostics, and regression tests.

## Setup and run

Use CPython 3.12 (validated locally with 3.12.4). Other Python minor versions are intentionally excluded by setup metadata until validated. From this nested application directory, create a fresh environment and run these PowerShell commands:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m streamlit run dashboard.py
```

`requirements-dev.txt` includes the application dependencies and pytest. For source application use without tests, install `requirements.txt` instead. For an installed CLI/dashboard, use `python -m pip install -c requirements/constraints-py312.txt .` in a fresh environment; build isolation uses the pinned build backend. Runtime, test, build, and audit dependencies use exact tested versions constrained by `requirements/constraints-py312.txt`. This is a version constraint set, not a hash-verified artifact lock. It must be revalidated when dependencies change. The older development environment inherited system packages; the recorded release validation uses an isolated environment instead.

By default the CLI processes four shipped samples, writes per-file text reports plus combined text/CSV/JSON investigation exports under `reports/`, and replaces the unfiltered error/warning snapshot in `logs.db`:

```powershell
.\.venv\Scripts\python.exe main.py
```

Default input, report, and database locations are relative to the application directory, so running from another working directory also works. The dashboard reads three defaults (`app.log`, `server.log`, `database.log`) or a single upload. The dashboard imports defaults/uploads into a session-local SQLite store and reuses completed revisions when filters or pages change. Refresh sources checks content again. Tables and group evidence are paginated; totals describe the full selected investigation.

Importing any application module performs no analysis, database changes, report writes, or fixture generation.

Installing the project adds `log-analyzer` and `log-analyzer-dashboard` commands. The dashboard launcher binds to 127.0.0.1, sets a 50 MiB upload cap, and disables usage telemetry. Installed sample logs are read from package data; generated reports, databases, and session files use the current working directory. Source checkouts keep application-relative defaults for compatibility. Use explicit output paths when running examples. [Release setup and validation](RELEASE.md) covers fresh installs, private wheel checks, CI, dependency audits, benchmarks, and remaining release gates.

A Windows install under a deeply nested path can exceed filesystem path limits, including paths inside third-party packages. Use a shorter virtual-environment path, such as a workspace-root `.r`, rather than reusing an environment with a failed partial installation. A venv created over the old `.venv` is not proof of a clean install; create a new directory.


## Supported input

Select **text** or **jsonl** explicitly in the dashboard or with `--format`. The same selection applies to all selected files; there is no format autodetection. Both formats use UTF-8 with an optional leading BOM.

Text examples:

```text
2026-10-15 10:00:00 INFO Application ready
2026-10-15T10:00:01.123456Z WARN: Slow request
2026-10-15 15:30:02+05:30 FATAL Connection failed
```

JSON Lines requires one object per physical line, with string fields `timestamp`, `level`, and `message`:

```jsonl
{"timestamp":"2026-10-15T10:00:00Z","level":"INFO","message":"Application ready"}
{"timestamp":"2026-10-15T15:30:02.5+05:30","level":"FATAL","message":"Connection failed","request_id":"example-01"}
```

A nonempty message is required. Canonical levels are DEBUG, INFO, WARNING, ERROR, and CRITICAL. Levels are case-insensitive and may have one trailing colon; WARN maps to WARNING and FATAL to CRITICAL. Additional JSON fields remain available in raw evidence but do not change interpretation. Unknown levels, wrong field types, malformed JSON, duplicate JSON keys, nonstandard NaN/Infinity constants, and unpaired Unicode surrogates in messages are rejected.

Timestamps must have a valid calendar date and clock time, with a space or uppercase `T` separator, optional 1–6 fractional digits, and optional uppercase `Z` or `±HH:MM` offset. All accepted events normalize to timezone-aware UTC. **A timestamp without an offset is assumed UTC by default**; `--naive-policy reject` rejects it instead. The computer's timezone is never inferred. Each event records the original timestamp value and whether UTC was assumed.

Supported Python traceback blocks attach to a valid ERROR/CRITICAL text header. Line/character limits apply per event; incomplete, oversized, unsupported, or orphan blocks produce visible issues. The dashboard exposes the same options as the CLI. See [docs/FORMATS.md](FORMATS.md) for the exact boundaries and accounting rules.

For example, analyze the synthetic JSON Lines fixture into separate outputs:

```powershell
.\.venv\Scripts\python.exe main.py tests/fixtures/events.jsonl --format jsonl --naive-policy reject --report-dir reports/jsonl-example --database example.db
```

Other parsing options are `--no-tracebacks`, `--max-event-lines` (default 200), and `--max-event-chars` (default 65,536 decoded characters). `--help` lists all options. Programmatic callers pass the same immutable `ScanOptions` to `log_analyzer.analyze_log_file`, `analyze_upload`, or `analyze_lines`.

## Investigate, group, and export

The dashboard provides inclusive start/end times, exact severity selection, a literal message-text filter, and a case-sensitivity toggle. Filter timestamps require an explicit `Z` or offset and are normalized to UTC. Missing bounds mean unbounded time. No selected severities means no matching events; ERROR does not implicitly include CRITICAL. Text matching is case-insensitive by default and searches the full decoded message, including assembled traceback text.

The CLI exposes the same selection:

```powershell
.\.venv\Scripts\python.exe main.py tests/fixtures/investigation.log --start "2026-10-15T12:00:01Z" --end "2026-10-15T12:00:02Z" --severity ERROR --contains e42 --report-dir reports/investigation-example --database example.db
```

That synthetic fixture selects three events, including a genuine repeated source record, in one recurring-error group. Use repeated `--severity` flags for multiple levels and `--case-sensitive` for exact-case text matching. Reversed or invalid ranges fail before analysis/output writes. Valid input with no matches produces an explicit “No matches” status and empty exports.

Grouping covers selected ERROR and CRITICAL events, retaining their separate severities. It normalizes only UUIDs or 16–64-character hexadecimal values explicitly labeled request/correlation/trace IDs. It preserves exception types, ordinary numbers, error/status codes, case, whitespace, and original messages. Groups show counts, first/last UTC occurrence, representative source evidence, and all members. Their signatures describe recurring observations, not proven root causes.

By default each CLI run creates:

- A filtered `<source-stem>_report.txt` for each source.
- `investigation.txt`, `investigation.csv`, and `investigation.json` for the combined selection.
- `investigation.csv.metadata.json`, the CSV filter/source/group manifest, including for empty selections.

Use repeated `--export text`, `--export csv`, or `--export json` to choose combined formats; per-file text reports remain. Dashboard downloads expose the same three serializations plus CSV metadata. Small selections prepare downloads automatically; larger ones provide a Prepare full streamed exports action. Browser downloads are capped at 5 MiB per artifact, with complete larger files retained on disk. Selected rows, counts, groups, and full exports come from the same revision-pinned investigation. Detail tables and the timeline are bounded pages/previews; count charts cover the full selection. Ingestion failures/rejections stay visible in a separately labeled unfiltered quality section.

CSV contains every selected event, with group references where applicable. Formula-like text cells receive an apostrophe prefix, and `csv_escaped_fields` records exactly which cells were changed so they can be recovered. Keep the CSV metadata alongside it; JSON preserves original values directly. See [docs/INVESTIGATION.md](INVESTIGATION.md) for normalization, deterministic ordering, membership IDs, CSV escaping, and export contracts.

## Bounded processing and persistent runs

Normal CLI/dashboard processing uses `log_analyzer.storage.Store`. It hashes sources incrementally, parses through bounded UTF-8 line readers, inserts events/issues in batches, and performs filtering/group aggregation in SQLite. It does not build a Python list of every event or every unique signature.

Defaults are 50 MiB per source, 65,536 decoded characters per physical line, 250,000 event/issue outcomes, 500,000 physical lines, 32 sources per run, batches of 250, previews of 100, and query pages up to 200 rows. Whole-request read/decoding/resource/storage failures roll back staged imports and preserve completed investigations. Streamlit itself retains uploaded bytes; the app avoids additional whole-upload copies and also supplies a 50 MiB framework upload setting in `.streamlit/config.toml`.

A CLI invocation creates a new run unless `--run-id` is supplied. Within a run, unchanged source identity/bytes/parsing policy reuse the completed revision; changed input creates a new revision. Prior selections retain their original revision IDs and evidence. Genuine repeated log events remain distinct. To reuse an investigation's inputs with another filter:

```powershell
.\.venv\Scripts\python.exe main.py tests/fixtures/investigation.log --run-id example --report-dir reports/example --database example.db
.\.venv\Scripts\python.exe main.py tests/fixtures/investigation.log --run-id example --contains E42 --report-dir reports/example --database example.db
```

Resource flags include `--max-source-bytes`, `--max-line-chars`, `--max-records`, and `--batch-size`. The API also exposes page/preview/export/download budgets. Larger budget values are explicit choices rather than a guarantee of available resources.

SQLite schema version 1 adds runs, immutable source revisions, all accepted severities, issues, cached selections, and SQL group summaries. Migration preserves the initial legacy issue snapshot in `legacy_log_entries_archive` without inventing timezone metadata. Changed imports and published-snapshot updates are transactional; historical revisions remain available through bounded run/history APIs.

Exports are generated incrementally with a 200 MiB per-artifact limit and staged atomic replacement per file. Export schema version 3 records source hashes, revision/run IDs, selection provenance, diagnostic settings, occupied buckets, alerts, and ranked recurring signatures. SQLite remains schema version 1. CSV/metadata pairs and multiple exports do not form one transaction with each other or the database import. An export failure can leave a completed stored investigation available for recovery/re-export.

Older in-memory APIs and compatibility database readers remain for small callers, capped at 20,000 rows plus retained-text budgets. Use Store rather than constructing large ScanResults. Disk usage grows with run/revision/selection/export history; no automatic destructive retention cleanup is performed. See [docs/STORAGE.md](STORAGE.md) for exact limits, snapshot semantics, migration, pagination, allocation measurements, and remaining boundaries.

## Counting and status

Each completed scan distinguishes physical lines, nonblank lines, logical parsed events, accepted continuation lines, rejected lines, and counts for every canonical level. A five-line traceback event counts once at its header severity, with four accepted continuation lines. A rejected block can be one issue covering several rejected physical lines. The accounting invariant is:

```text
nonblank lines = parsed events + accepted continuation lines + rejected lines
parsed events = DEBUG + INFO + WARNING + ERROR + CRITICAL
error total = ERROR + CRITICAL
```

INFO is counted directly. CRITICAL retains its own severity in detail tables and storage. Original repeated events are preserved.

A scan is `success` when nonblank input was read, `empty` when it has no nonblank input, or `failed` when reading or decoding fails. A successful read can still contain rejected records. Failed reads discard partial results and expose the failure rather than reporting partial totals as complete.

The production CLI, dashboard, and streamed reports share this status policy, in priority order:

| Evidence | Status |
| --- | --- |
| Any failed source | Failed |
| No parsed records, with rejected input | Uninterpretable |
| No nonblank input | Empty |
| Some valid input, plus rejections or an empty source | Incomplete |
| Valid source input, but no selected events | No matches |
| At least one bucket meets both configured thresholds | Rule alerts |
| No occupied bucket meets both thresholds | No rule alerts |

These labels describe selected evidence. They do not establish system health or a root cause. Rejection and failure statuses take precedence; error totals and diagnostics remain visible. Older small-data compatibility helpers retain their earlier count-summary policy; normal CLI/dashboard processing uses the diagnostic rules.

## Diagnostic rules and automation

Defaults use 300-second UTC buckets, at least 5 ERROR/CRITICAL events, and an error rate of at least 0.5. Both thresholds must be met. Buckets are anchored at 1970-01-01 00:00:00 UTC and include their start but exclude their end. The denominator is every accepted event **after all current filters**, including severity and message filters; a selection of errors alone therefore has a conditional rate of 1. Rejected lines are excluded and reported separately. No accepted evidence means no bucket; missing buckets are not evidence of zero traffic. Edge windows may be partial.

```powershell
.\.venv\Scripts\python.exe main.py tests/fixtures/investigation.log --bucket-seconds 60 --min-errors 3 --error-rate 0.5 --top-signatures 10 --fail-on-alert --export json --report-dir reports/diagnostic-example --database example.db
```

The dashboard exposes the same rules. Each alert records its UTC window, error count, denominator and definition, observed rate, thresholds, and a representative original event ID. Observations, hypotheses, and suggested next steps are labeled separately. The top recurring signatures have counts, first/last observations, and representative event IDs; the existing full group evidence remains available. JSON and CSV metadata stream all buckets/alerts, while CSV event columns remain unchanged. Text reports include the same diagnostic facts. Stored export schema 3 replaces the old count-only status labels; failed-import compatibility reports retain their earlier schema and explicitly failed status without evaluating diagnostics.

CLI output includes a JSON `Diagnostics:` summary and bounded `Alert:` previews; use `--export json` for a complete machine-readable artifact. `--fail-on-alert` returns exit 3 when rules trigger. Input/output/storage problems retain exit 1, and invalid arguments retain exit 2. With the flag omitted, complete processing returns 0 even when diagnostic alerts exist. See [docs/DIAGNOSTICS.md](DIAGNOSTICS.md) for exact rules and limitations.

## Reports and SQLite

`write_report(result, output_path, protected_inputs=..., filters=...)` requires an explicit destination. Resolved-path and existing-file identity checks reject destinations that refer to an input, including aliases through filesystem links. The CLI checks every report and database destination against every selected input before writing, and rejects colliding report names.

Investigation reports contain filters/parsing settings, selected event/continuation counts, UTC timestamps, original evidence and source spans for every selected severity, recurring groups, unfiltered source quality/rejections, and the shared selected status. Report output failures are returned by the CLI as failures. Shipped reports were retained as historical examples; running the CLI regenerates them using the new format.

Canonical revision storage retains every accepted severity and every issue with original evidence. The legacy `log_entries` projection retains ERROR, CRITICAL, and WARNING events with UTC timestamp, original timestamp, assumption flag, source, line span, raw text, level, and message. Legacy date/time fields now contain UTC values for new events. Write operations add evidence columns to old tables transactionally; old rows retain NULL metadata rather than receiving an invented timezone. `fetch_all_entries` and `fetch_event_evidence` retain their small-data contracts with explicit row/text caps. Store pages and streamed iterators serve larger investigations. A snapshot replacement uses one transaction, and connections close on failure. If any selected source is missing, unreadable, empty, or wholly uninterpretable, the CLI preserves the previous database snapshot. Mixed valid/rejected input stores the valid error/warning evidence while displaying its incomplete status. The compatibility issue projection remains an **unfiltered ingestion snapshot**: investigation filters do not delete its accepted source events, including when a selection is empty. Each export destination and CSV sidecar is checked against every input and the database before any CLI output writes.

CLI exit code 0 means processing completed without rejected input or operational failures. Exit code 1 indicates rejected input, an unavailable/uninterpretable source, unsafe destinations, or an output/storage failure. Invalid CLI arguments produce exit code 2. Finding errors in logs alone does not cause exit code 1. With `--fail-on-alert`, a successful analysis with rule alerts exits 3; input/output failures take precedence. A no-match selection on complete valid inputs exits 0; filters cannot conceal ingestion failures/rejections from the exit code. The import commits independently from exports; individual destination files are replaced only after complete staged generation. Multiple export files and CSV/metadata pairs are not one combined transaction; output failures are reported explicitly.

## Tests and sample baseline

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The recorded clean-environment run passed **382 tests with no failures or skips**. The exact dependency audit found no known advisories for the 68 constrained distributions after the documented backend upgrade. Coverage, installed-package checks and measured limitations are recorded in [docs/PHASE6_VALIDATION.json](PHASE6_VALIDATION.json) and [docs/RELEASE.md](RELEASE.md). These are dated local results, not a hosted CI badge or a security guarantee.

Tests use isolated outputs under `.test-tmp/` and verify that shipped logs, reports, and the database remain unchanged. The directory is reserved for pytest and may be cleared by pytest. Dashboard smoke tests use Streamlit AppTest; install both application dependencies to run them. Filesystem-link tests skip when the operating system denies link creation.

| Input | Nonblank lines | Errors | Warnings |
| --- | ---: | ---: | ---: |
| app.log | 20 | 7 | 4 |
| server.log | 15 | 5 | 4 |
| database.log | 13 | 5 | 3 |
| big_test.log | 10,000 | 526 | 1,476 |

The three dashboard defaults total 48 lines, 17 errors, and 11 warnings. All four CLI defaults total 10,048 lines, 543 errors, and 1,487 warnings.

Fixture generation is optional and explicit:

```powershell
.\.venv\Scripts\python.exe generate_logs.py
```

This overwrites `logs/big_test.log` with 10,000 entries using seed 0. Its counts will differ from the shipped baseline. Tests generate fixtures in temporary paths and never regenerate shipped data. Programmatic generation supports `generate_logs(output_path, seed=...)`.

In restricted environments where the system temporary directory is unwritable, set temporary paths before running installs or tests:

```powershell
New-Item -ItemType Directory -Force .runtime-tmp | Out-Null
$env:TEMP = (Resolve-Path .runtime-tmp).Path
$env:TMP = $env:TEMP
```

## Structure and remaining scope

- `log_analyzer/models.py`: immutable options/events and explicit result/issue contracts.
- `log_analyzer/parsers/`: parser protocol, text and JSON Lines parsers, common UTC/severity validation.
- `log_analyzer/ingestion.py`: source spans, raw evidence, and bounded traceback assembly.
- `log_analyzer/analysis.py`: the sole iterable/file/upload accounting pipeline.
- `log_analyzer/filtering.py`: validated UTC/severity/literal-text selection and stable ordering.
- `log_analyzer/grouping.py`: conservative structured signatures and evidence membership.
- `log_analyzer/investigation.py`: the shared selected event set, counts, and groups.
- `log_analyzer/exporting.py`, `paths.py`: small-data serializers and safe destinations.
- `log_analyzer/limits.py`, `streams.py`: validated budgets, bounded hashing/decoding, and source adapters.
- `log_analyzer/storage.py`: versioned imports, runs/revisions, SQL selection/group caches, and paginated queries.
- `log_analyzer/stored_export.py`: incremental query-backed exports with size limits/staged replacement.
- `scripts/benchmark_storage.py`: reproducible Python-allocation and storage measurements.
- `parser.py`, `analyzer.py`: compatibility entry points delegating to the shared package.
- `status_policy.py`: shared status decisions.
- `report_writer.py`: text exports and input-path protection.
- `database.py`: SQLite helpers and transactional snapshot replacement.
- `main.py`, `log_analyzer/cli.py`: guarded CLI workflow and validated arguments.
- `log_analyzer/diagnostics.py`: shared evidence-based UTC bucket/count/rate rules.
- `log_analyzer/runtime.py`, `launcher.py`: source/installed locations and local dashboard entry point.
- `pyproject.toml`, `requirements/`: private package metadata and tested version constraints.
- `dashboard.py`: metrics, parsing quality, detail tables, and Plotly charts.
- `generate_logs.py`: guarded, seeded example generator.
- `tests/`: deterministic regression and dashboard runtime tests.

The production import/query/export path has bounded Python batches and previews, with exact group state stored in SQLite. Diagnostic rules, private packaging, dependency constraints, release checks and CI configuration are implemented. Retained history and caches still grow on disk; SQLite/native/browser allocations and total process RSS are not claimed constant. Retention/history UI, broader performance evidence, and actual hosted CI execution remain outside the locally validated scope. See [DEVELOPMENT_PLAN.md](../DEVELOPMENT_PLAN.md) for the roadmap and validation record.

## Authors and attribution

The inherited README credits **Swarananjali** and **Nandini**. Their original examples, fixture message catalog, shipped logs, and reference reports are retained. Subsequent local extensions add parsing, UTC normalization, investigations, grouping, persistent storage, diagnostics, exports, packaging, and regression tests, with coding-agent assistance.

The matching upstream and inherited asset records are documented in [ATTRIBUTION.md](ATTRIBUTION.md) and [UPSTREAM_PROVENANCE.json](UPSTREAM_PROVENANCE.json).
