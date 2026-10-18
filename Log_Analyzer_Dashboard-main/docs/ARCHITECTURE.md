# Architecture and operating boundaries

The application is a local investigation tool. It reads explicit text/JSON Lines sources, preserves original event evidence, and applies deterministic filters, signatures, and count/rate rules. It has no external model, API server, authentication layer, live tailer, or statistical anomaly baseline.

```text
Files / seekable uploads
    -> bounded byte hashing and UTF-8 decoding
    -> explicit parser + bounded traceback assembly
    -> transactional SQLite runs / source revisions / events / rejected evidence
    -> immutable SQL selections / exact groups
    -> UTC diagnostic buckets and evidence-linked observations
    -> CLI / Streamlit / streamed text, CSV + metadata, JSON
```

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `models`, `parsers`, `ingestion` | Canonical severities, UTC policy, logical events, source spans, original text, parse issues, supported traceback limits |
| `limits`, `streams` | Validated resource budgets, bounded lines and batches, source identity and byte fingerprints |
| `storage` | Schema migration, all-or-nothing source imports, retained revisions, reimport reuse, pinned SQL selections, exact grouping, bounded pages |
| `filtering`, `grouping` | Shared literal predicates and conservative versioned signature rules |
| `diagnostics` | Occupied UTC buckets, explicit conditional denominator, exact threshold decisions, ranked recurrences, evidence and tentative hypotheses |
| `stored_export`, `paths` | Full streaming serialization, reversible CSV escaping, staging/size/path protections |
| `cli`, root `main.py` | Validated arguments, source orchestration, reports and automation exit codes |
| root `dashboard.py`, `launcher` | Session stores, controls, full totals, bounded pages/previews, prepared exports, local dashboard launch |
| `runtime` | Existing source-relative defaults in a checkout; packaged samples and writable working-directory outputs for wheel installations |
| `analysis`, `investigation`, `exporting`, root compatibility modules | Bounded small-data compatibility interfaces; production paths use Store |

## Decisions that affect correctness

Sources are explicit UTF-8 or UTF-8 with a leading BOM. No encoding/format/timezone guessing occurs. Naive timestamps are assumed UTC or rejected by policy. JSON messages and all raw evidence remain distinct from display/CSV representations. The format contract is in [FORMATS.md](FORMATS.md).

Identity is source-scoped, not global content deduplication. Identical bytes at different paths remain separate sources. Unchanged bytes/options/policy within the same run reuse a completed revision; changes create new revisions. Genuine repeated records remain distinct. Imports fingerprint before and during parsing and roll back on mutation/failure. A selection pins exact revision IDs and cannot drift after reimport.

Filtering precedes grouping and diagnostics. Group keys preserve severity and meaningful error codes; normalized identifiers are conservative and versioned. Diagnostic rates use accepted logical events after all filters, not line counts or inferred request counts. Rejections remain visible independently. Rule alerts do not establish root cause or health. See [INVESTIGATION.md](INVESTIGATION.md) and [DIAGNOSTICS.md](DIAGNOSTICS.md).

SQLite schema version is 1; normal streamed export schema is 3; bounded legacy exports retain their compatibility schema. Storage migrations and imports are transactional. File exports stage complete output and replace one destination at a time. A multi-format export bundle, CSV/metadata pair, and database import do not share a transaction.

## Resource and deployment scope

Python retains bounded pending events, insertion/query batches, and display pages. Exact evidence, groups, selection state, and retained revisions grow on disk. SQLite/native/OS/browser allocations are outside the Python tracemalloc measurement. Streamlit itself holds uploaded bytes. Deep pagination and repeated diagnostics queries may take longer; there is no constant-time/constant-total-memory claim.

The dashboard launcher binds to `127.0.0.1`, disables usage telemetry, and sets a 50 MiB framework upload limit. Core source/line/outcome/page/export budgets remain enforced separately. The source launch also reads `.streamlit/config.toml`. This is local deployment, not a hardened multi-user hosting configuration. Work with synthetic or appropriately handled logs; generated artifacts retain original messages.

Installed wheels contain inherited sample logs as package data. Generated reports/databases/session files go to the current working directory rather than site-packages. Source checkouts retain their earlier application-relative defaults. Explicit CLI output paths remain preferable for repeatable investigations.

Retention/history management, authentication, automatic cleanup, API/monitoring features, bundle-level transactions, and adversarial concurrent filesystem replacement protection remain outside the implementation. Operating budgets and measured limits are in [STORAGE.md](STORAGE.md).
