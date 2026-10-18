# Evidence-based diagnostic contract (Phase 5)

The production CLI, dashboard, and streamed exports use `log_analyzer.diagnostics`. It applies deterministic rules to an immutable, revision-pinned Store selection. No external service, model, anomaly detector, baseline comparison, or real-time monitoring is involved.

## Rule configuration

| CLI flag | Dashboard control | Default | Allowed |
| --- | --- | --- | --- |
| `--bucket-seconds` | UTC bucket seconds | 300 | Integer 1–86,400 |
| `--min-errors` | Minimum bucket errors | 5 | Integer 1–2,147,483,647 |
| `--error-rate` | Error rate threshold (fraction 0..1) | 0.5 | Finite decimal fraction 0–1, at most 18 decimal places |
| `--top-signatures` | Top recurring signatures | 10 | Integer 1–100 |
| `--fail-on-alert` | CLI only | Off | Exit 3 on alerts when processing succeeds |

`DiagnosticOptions` validates these settings before CLI writes or dashboard imports. Decimal input is limited to 32 characters and exponent magnitude 18. Rules are presentation settings: changing them reuses source revisions and the selected evidence. Dashboard export caching includes rule settings to avoid stale downloads. The CLI parser lives in `log_analyzer/cli.py`; `main.py` preserves the existing guarded entry point and programmatic `run`/`cli` interfaces.

## Windows, counts, and denominator

Bucket index is floor((UTC event time − 1970-01-01T00:00:00Z) / bucket_seconds). Calculation uses integer calendar seconds; fractional timestamps stay in the correct bucket, including before the epoch. Windows include their start and exclude their end. An event exactly at a boundary enters the next bucket. Investigation start/end filters remain inclusive and may select only part of a bucket.

Errors are logical ERROR + CRITICAL events. A supported multiline traceback counts once. The denominator is **all accepted logical events after every investigation filter**, including time, exact severity, and message predicates. Rejected evidence is excluded from numerator and denominator and its unfiltered count remains visible. Filtering only ERROR therefore gives rate 1 for occupied buckets; these are conditional rates, never service-wide error rates.

An alert requires both:

```text
errors >= min_errors
errors / denominator >= error_rate
```

Both comparisons are inclusive. Decimal thresholds are converted to exact integer fractions and cross-multiplied; rounded display rates do not decide alerts. JSON's observed numeric rate is a floating-point display value; exact numerator, denominator, and decimal threshold accompany it.

Only occupied buckets are emitted, chronologically. Empty selections emit zero buckets and alerts; division by zero cannot occur. Sparse time ranges do not generate a dense calendar. Missing buckets mean no selected accepted evidence, not measured zero traffic. Edge buckets may contain partial evidence. Valid calendar extremes are supported; a virtual boundary can extend to year 0000 or 10000, even though event timestamps remain valid Python UTC datetimes.

## Observations and evidence

Every alert contains its UTC window, error count, denominator and definition, rate, minimum count, decimal threshold, and a representative error event ID. IDs refer to the **combined selection** and resolve to original event/source/line-span/raw evidence in the full export or paginated investigation. The reference is representative, not a claim to list all contributing events. Full event evidence is exported independently.

Ranked recurring signatures require count ≥2. They retain existing conservative normalization and separate severities. Ranking is count descending, then first occurrence, severity, signature, and structured group key. At most `top_signatures` are returned, each with count, first/last observations, and up to three distinct-message representative IDs. All members and singleton groups remain accessible through existing group pages/full exports; repeated source records remain distinct occurrences.

Observed threshold crossings and repetitions are facts about available evidence. Each alert/ranked recurrence separately labels its hypothesis and suggested checks. The generic hypothesis is deliberately tentative; it does not identify a dependency, deployment, security incident, or root cause from frequency alone. No emerging-pattern or statistically unusual behavior is claimed.

## Output and status

The normal production status preserves Failed, Uninterpretable, Empty, Incomplete, and No matches precedence. Complete nonempty selections use Rule alerts or No rule alerts. A lack of alerts does not establish system health. A quality warning can coexist with diagnostic alerts; both remain visible.

Stored JSON and CSV metadata schema **3** add a `diagnostics` document containing rule version/options, selection and filters, comparison/denominator definitions, source rejection count, caveats, ranked recurring signatures, all occupied buckets, and all alerts. Text contains equivalent diagnostics plus full original events. CSV event columns remain unchanged; keep the sidecar. SQLite remains schema **1**, without a migration. Small-data compatibility APIs retain their earlier export/status contracts; an unsuccessful CLI import emits explicitly failed compatibility reports and never evaluates unavailable evidence.

CLI stdout includes a JSON `Diagnostics:` summary, bounded `Alert:` preview, revision provenance and ordinary totals. Use the JSON artifact for full machine-readable evidence rather than parsing human-oriented stdout. Per-source reports evaluate their own source selection; the combined artifact/dashboard evaluates combined evidence, so per-source rates may legitimately differ.

| Exit | Meaning |
| --- | --- |
| 0 | Complete processing with valid inputs; alerts allowed unless opted into exit 3 |
| 1 | Rejected/unavailable/uninterpretable input, resource/IO/storage/output failure, or unsafe paths |
| 2 | Invalid CLI arguments/settings |
| 3 | Successful processing with alerts and `--fail-on-alert` |

Quality and operational failures take precedence over alert exit 3. Complete valid input with a no-match selection exits 0. Diagnostic exit 3 occurs after evidence and artifacts are committed/generated and does not mean import rollback.

## Resource and evidence limits

SQLite groups occupied buckets on disk; results stream in configured query batches. Python retains bounded pages/top-signature representatives rather than one bucket per event/time interval. Export arrays stream completely subject to the existing per-artifact byte cap. UI bucket/alert pages retain bounded rows, though later pages rescan preceding aggregates; repeated summary/export queries can add latency. No new runtime or total-RSS benchmark is claimed. Existing disk-history growth, upload memory, per-file atomicity, and local path-preflight limits remain in [STORAGE.md](STORAGE.md).

Missing, rejected, selectively filtered, duplicated-at-source, or incomplete logs can bias rates. A rule crossing signals evidence to investigate; it does not measure system availability or demonstrate a cause.
