# Phase 4: bounded imports, revision storage, and streamed investigations

The CLI and dashboard now use `Store`, not materialized ScanResults, for their normal workflow. Parsing/severity/timestamp/traceback/signature rules are still shared with the earlier phases. SQLite stores every accepted severity and every parse issue; only the deprecated `log_entries` compatibility projection remains limited to ERROR/CRITICAL/WARNING.

## Resource contract

| Budget | Default |
| --- | ---: |
| Raw bytes per source, including any BOM | 50 MiB |
| Decoded characters per physical line, including line endings | 65,536 |
| Event/issue outcomes per source | 250,000 |
| Physical lines per source, including blanks | 500,000 |
| Active source identities per run/request | 32 |
| Event batch and issue batch | At most 250 rows each |
| Default preview | 100 rows |
| Default maximum query page | 200 rows |
| Import hard ceiling on configured traceback assembly | 1,000 lines / 262,144 characters |
| Text/CSV/JSON export size | 200 MiB per artifact |
| Browser download | 5 MiB per artifact |
| Compatibility materialization | 20,000 event/issue rows and an 8 Mi-character retained-text budget |

`ResourceLimits` validates positive integer budgets. Batch/page/preview settings cannot exceed 1,000; preview cannot exceed maximum page size. Numeric budgets cannot exceed 2**31−1. Traceback defaults remain 200 lines and 65,536 characters; import options may not exceed the hard ceilings. Count limits include issues, including block-level issues for rejected tracebacks; the physical-line limit independently covers continuation/blank lines.

The importer hashes input in 64 KiB-or-smaller byte chunks, then parses through a UTF-8/BOM decoder with bounded `readline` calls. The second pass recomputes the byte fingerprint and fails if it differs. This uses two sequential passes for a new revision, rather than buffering a whole source. Unchanged revisions need a fingerprint pass but no parsing. Read/decoding/resource errors discard the staged request and are surfaced as ImportFailure.

Streamlit uploads are read via seek/read without `getvalue()`, whole-text decoding, or split-line copies in the application. Streamlit itself retains the uploaded bytes in memory. `.streamlit/config.toml` sets its upload size to 50 MiB when launched from the application directory; passing `--server.maxUploadSize 50` also applies the framework setting explicitly. The engine always enforces its own byte budget regardless of framework settings. These changes bound application buffers; they do not claim that the browser or Streamlit stores no complete upload.

The CLI exposes `--max-source-bytes`, `--max-line-chars`, `--max-records`, and `--batch-size`. Other budgets are available through `ResourceLimits` in the API. Raising budgets is an explicit configuration choice, not a promise that any machine can handle the resulting workload.

## Runs, identities, and revision policy

`Store.import_sources(inputs, options, run_id=None)` imports an entire request transactionally. No run ID creates a new run; `--run-id` reuses a named run. New sources are additive within a reused run, subject to its active-source cap. CLI investigation output describes the sources requested by that invocation; the compatibility issue snapshot describes all active run sources.

File identities use resolved paths with platform case normalization. Symlink paths resolve to the same identity; separate hardlink paths or different upload names remain distinct identities. Upload identities use their source names within a run. There is no global content deduplication across identities or runs.

For an existing identity, the source label, raw-byte SHA-256, parsing options, parser/signature versions, and ingestion-limit policy determine reuse. Unchanged input returns its completed source revision ID without inserting events. A different byte fingerprint, options/policy/version, or display label creates a new revision. Presentation-only changes such as filters or batch size do not redefine parsed evidence.

Changed revisions are staged before the old active revision is deactivated, all in one transaction. Prior revisions/events/issues remain on disk. A selection pins exact revision IDs, so later imports cannot rewrite its evidence or event IDs. Genuine repeated source records remain separate event rows. Reimport behavior is about source identity/revision, not deduplication of log messages.

A completed empty or wholly rejected read can be stored as a diagnostic revision. It does not replace the last published compatibility snapshot. `published_sources()` returns the exact revision set associated with that published snapshot, independently of current run slots. Resource/IO/decoding/storage failures roll back the full requested import; no partial new sources or changed active slots are committed. Failed attempts are reported to the caller rather than persisted as successful runs.

`list_runs`, `run_sources`, `source_history`, `published_sources`, and `source` expose run/revision metadata. History/run APIs are paginated. Revisions are discoverable even when current run slots point to newer input.

## Versioned SQLite migration and compatibility

Store schema version 1 is recorded in `PRAGMA user_version` and `schema_migrations`. Initialization rejects future versions and inconsistent required-table sets. Migration is transactional, including legacy evidence-column additions and new tables/indexes. It preserves unrelated tables.

The initial `log_entries` contents are copied on disk to `legacy_log_entries_archive` before later imports can replace that compatibility projection. Original legacy values are not assigned invented UTC metadata. `page_legacy_archive()` returns bounded, truncated display previews; full archived evidence remains in the archive table. Later successful imports can update `log_entries` while the initial archive and prior revision history remain intact.

New tables represent runs, source revisions, all accepted events, parse issues, cached selections, selected-event positions, exact SQL group summaries, and published-snapshot settings. Event indexes cover source/time and grouping; membership indexes cover selection/group/position. SQLite connections enable foreign keys, use file-backed temporary storage, and request a 4 MiB page cache per connection. These settings are not a hard cap on all SQLite native allocations or process RSS.

`log_entries` is updated by SQL `INSERT … SELECT` inside the successful import transaction; no full Python row list is built. Existing compatibility writers remain for older small-data callers and operate on that projection only. New integrations should use Store for canonical revision evidence rather than mixing manual compatibility-table writes with run storage.

## Query snapshots, filters, and pages

`store.view(source_ids, filters)` applies the Phase 3 predicates in SQL: inclusive canonical UTC microsecond keys, exact levels, and literal `instr` text matching. A registered Unicode casefold function preserves case-insensitive Python matching semantics; SQL LIKE/regex are not substituted.

Selections store sorted event positions on disk. Exact recurring-group counts/first/last state is also aggregated and stored on disk, so Python does not retain one dictionary entry per unique signature. The structured grouping key includes the signature version and severity. Filters do not mutate ingestion evidence or published snapshots.

Repeated requests for the same revision set and filters reuse a completed selection. Source/group/event/issue pages are bounded and validated; representative group examples are limited to three distinct messages. Per-source pages retain the combined selection's event IDs, matching group references and combined exports. Per-source standalone reports intentionally have their own local investigation IDs.

The dashboard reuses its imported revisions across filter/page/download reruns. For default files, size/mtime changes trigger refresh; an explicit **Refresh sources** performs a new fingerprint check even when metadata appears unchanged. Upload tokens/name and parsing options control refresh. A new source set starts a new UI run. The current view remains visibly failed on import errors rather than showing stale successful metrics, while completed revisions stay stored.

Count charts cover the entire selection. Timeline and detail tables are explicitly bounded previews or pages. Large selections require **Prepare full streamed exports**; preview-sized selections prepare downloads automatically. Exports are cached per immutable selection. Artifacts larger than the browser cap remain complete on disk with their local path displayed; they are not silently truncated.

## Incremental exports

`write_stored_export` streams text, JSON, CSV, and the CSV metadata manifest from query cursors. Event arrays, issue arrays, group members, and examples are emitted incrementally. CSV buffers one row and retains the Phase 3 reversible formula-prefix handling. Export schema version 3 retains selection/run/revision/fingerprint provenance and adds diagnostics; selected events, counts, filters, signatures, and membership semantics remain compatible with Phase 3.

All input/database/SQLite-journal/WAL/SHM aliases are protected by CLI preflight; exporters additionally protect their own database and selected inputs. Outputs and CSV sidecars are checked before writes. Each artifact is staged with exclusive creation and size accounting, then atomically replaced after successful generation. Resource/write errors leave the prior destination intact and remove only staging files owned by that attempt. A pre-existing staging-name collision is never deleted. A CSV/metadata pair or a multi-artifact bundle is not one transaction, and exports are not atomic with the already completed database import. An export failure does not discard a successfully stored investigation.

For example:

```powershell
.\.venv\Scripts\python.exe main.py tests/fixtures/investigation.log --run-id example --report-dir reports/example --database example.db
.\.venv\Scripts\python.exe main.py tests/fixtures/investigation.log --run-id example --contains E42 --report-dir reports/example --database example.db
```

The second invocation reuses the source revision if bytes/options/policy are unchanged, filters stored events, and streams fresh selected exports.

## Compatibility APIs and remaining boundaries

The older `analyze_*`, `investigate`, `group_errors`, `render_export`, and legacy database readers remain for small in-memory callers and existing regression contracts. Analyzer/selection/signature/read materialization now has explicit record/text budgets and raises or returns a failed scan on limit breach; it does not return an unlabeled truncated result. CLI and dashboard production paths use Store. Callers should not first construct a huge in-memory ScanResult and expect the storage pipeline to undo that allocation.

Disk usage grows with retained runs, revisions, legacy archives, cached selections/groups, and exports. No automatic destructive retention cleanup is introduced. Session caches live under `.runtime-tmp/dashboard-*`; CLI storage and report paths are explicit. Group cardinality can still affect SQLite work, disk use, native allocations, and runtime. Pagination is bounded but deep OFFSET pages can be slower. There is no claim of constant total memory, constant disk, or constant query time.

Full export-bundle transactions, OS-native/browser RSS profiling, a history-management UI, retention controls, dependency locking, release CI, and vulnerability checks are outside Phase 4. Phase 5 now adds deterministic diagnostics/alerts and expanded CLI workflows as documented below.

## Allocation measurement

Run the standard-library harness in a fresh directory:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_storage.py --directory .runtime-tmp/benchmark-new --counts 2000 20000
```

It generates reproducible synthetic logs with one unique error signature per event, then measures import, SQL selection/group aggregation, and bounded first pages with tracemalloc. Fixture creation and schema initialization are outside the measurement. Results identify Python/platform, dataset bytes, options, elapsed time, Python peak allocations, batch/page sizes, and database size. SQLite native allocations and OS/browser RSS are explicitly excluded. Use a fresh directory: reusing one would mix retained history into the disk measurement. The test suite includes a smaller 1,000/10,000-event allocation regression with explicit page/batch and Python-allocation budgets.


Recorded validation on 2026-10-15 is saved in [PHASE4_BENCHMARK.json](PHASE4_BENCHMARK.json). With default text parsing, UTC policy, 200-line/65,536-character traceback settings, and default ResourceLimits:

| Events / unique groups | Input bytes | Peak traced Python bytes | Elapsed seconds | Database bytes |
| ---: | ---: | ---: | ---: | ---: |
| 2,000 | 146,890 | 354,826 | 0.6858 | 4,792,320 |
| 20,000 | 1,488,890 | 335,818 | 16.5015 | 47,509,504 |

Both samples held at most 250 events in an import batch and returned 100-row event/group previews. The tenfold unique-signature increase did not produce a corresponding Python allocation increase in this measured path. Disk grew with the retained data and aggregates. These observations do not establish constant total memory, an end-to-end browser memory bound, a throughput SLA, or improvement over an earlier measured implementation.


Phase 5 uses the existing SQLite schema 1 and immutable selection IDs. Diagnostics aggregate occupied buckets in SQLite and stream results in configured batches. No persistent diagnostic tables or migrations are required. Stored exports now use schema 3; the dashboard keys export reuse by both selection and diagnostic settings, so changing rules does not reimport evidence or reuse stale exports. Disk-backed grouping still grows with evidence; repeated diagnostic/export queries may add latency. See [DIAGNOSTICS.md](DIAGNOSTICS.md).
