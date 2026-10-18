# Investigation filters, recurring groups, and exports

The small-data Investigation API combines original source scan results, a validated `FilterSpec`, a sorted tuple of selected immutable events, and error groups. Phase 4 production workflows use Store/StoredView for equivalent SQL-backed selection and group semantics, bounded pages, and streamed exports. Filtering does not mutate scans or discard their ingestion issues. Each source can retain its own parsing options. There is one selection pipeline for CLI reports, dashboard metrics/charts/tables/groups, and downloads.

## Filter contract

- Start and end are independently optional, **inclusive**, and compared against each logical event's header timestamp. An assembled traceback is selected as one event.
- User-facing filter times require a valid timestamp with explicit Z/offset; accepted offsets normalize to UTC. Programmatic datetime bounds must be timezone-aware. A reversed range raises ValueError before CLI writes or dashboard results are presented.
- Severity values are exact canonical names. `None` means all severities; an empty collection means no severities. ERROR selects ERROR only; include CRITICAL separately for both. CLI `--severity` may be repeated.
- Text is a literal substring of the entire decoded message, including traceback continuations. It is not a regular expression and does not search source names, raw JSON metadata, or rejected input. Whitespace in the query is meaningful. Matching uses Unicode `casefold()` by default; case-sensitive matching is optional.
- Time, severity, and text predicates combine with AND; multiple selected severity values combine with OR.

For example:

```python
from log_analyzer.analysis import analyze_log_file
from log_analyzer.filtering import FilterSpec, parse_filter_time
from log_analyzer.investigation import investigate

filters = FilterSpec(
    start=parse_filter_time("2026-10-15T12:00:01Z"),
    end=parse_filter_time("2026-10-15T12:00:02Z"),
    severities={"ERROR"},
    text="e42",
)
view = investigate([analyze_log_file("tests/fixtures/investigation.log")], filters)
# Three selected events, three errors, one group. Original repeated records remain.
```

Selected events sort by UTC timestamp, source string, start/end line, severity value, message, raw text, original timestamp, and assumption flag. Equal events retain their multiplicity. This orders timestamps as datetimes rather than formatted strings, so fractions and equivalent offsets work correctly.

## Selected data versus ingestion quality

Selection counts and charts describe only selected accepted events. Selected evidence-line coverage is `selected events + selected continuation lines`; it excludes rejected and unmatched input. Source physical/nonblank/parsed/rejected counters and parse issues describe the full original scan and are explicitly labeled **unfiltered ingestion quality**. Rejected lines have no trustworthy event timestamp or severity and are not passed through event filters.

Ingestion failures, wholly uninterpretable/empty input, and incomplete source quality retain precedence over normal count-based statuses. Complete source input with an empty selection says **No matches**, rather than Empty or No errors observed. Valid no-match CLI runs exit 0. Source failures or rejections still cause exit code 1 even if filters match no events. Invalid arguments cause exit code 2.

Phase 4 canonical SQLite revision storage retains all accepted levels/issues; the compatibility projection remains the unfiltered accepted ERROR/CRITICAL/WARNING snapshot; filters affect investigation outputs, not ingestion storage. Therefore a no-match view does not erase the source snapshot. Reports/exports do not form one transaction with that database replacement.

## Conservative signature rules

Version `labeled-ids-v1` groups selected ERROR and CRITICAL events separately. The only normalized values are:

- An unquoted UUID in canonical 8-4-4-4-12 hexadecimal shape.
- An unquoted hexadecimal identifier of 16–64 characters.

The value must immediately follow a request/correlation/trace ID label and `:` or `=`, with optional whitespace after the label/separator. Label spelling accepts `request_id`, `request-id`, `requestid`, and corresponding correlation/trace forms, case-insensitively. Label text, its case/spacing, and all surrounding message text are preserved in the signature. A trailing alphanumeric/underscore/hyphen prevents partial matching inside a larger value. Quoted, short, overlong, unlabeled, and other ID formats are deliberately not normalized.

Exception names, tracebacks, error/status codes, ordinary numbers, whitespace, case, and other message differences remain meaningful. This can split related failures; it reduces false merging. Grouping is syntactic and does not establish root cause.

The grouping key is `(canonical severity, structured literal/ID tokens)`. It is never the display string or its hash. A literal `<hex-id>` in a message therefore cannot collide with the typed placeholder introduced by normalization, even though their displayed signatures can look identical. A full SHA-256 group ID identifies the version/severity/token representation in exports; the structured key determines membership.

Groups rank by descending occurrence count, earliest occurrence, severity, display signature, and structured tokens. First/last timestamps use actual selected members. Up to three distinct original messages provide deterministic representative examples; all members remain accessible, including identical repeated source records.

## Event IDs and evidence

Every selected event receives a deterministic investigation-local ID such as `event-000001`, in selected order. Genuine duplicates receive separate IDs. Each row keeps source, original inclusive physical span, UTC/original timestamp, assumption flag, decoded message, and raw evidence. Error rows reference their group ID; other rows have no group.

Groups record all member event IDs and representative example IDs, referencing the same event table. IDs are scoped to one investigation serialization, not permanent global identities. A per-source report has its own local IDs; combined exports use combined-selection IDs. Stable input, options, and filters produce stable selected event/group payloads. Stored schema-2 exports additionally identify the exact persisted run/revision/selection; re-exporting the same pinned selection is stable. Different runs intentionally have different provenance IDs. No wall-clock export-generation timestamp is inserted.

## Export contract

The CLI always retains filtered per-source text reports. Normal exports stream through StoredView; current JSON/metadata schema version 3 retains raw source fingerprints and revision/run/selection provenance and adds Phase 5 diagnostics. The bounded in-memory serializer retains schema version 1 for compatibility. Combined output defaults to all three formats:

| Artifact | Contents |
| --- | --- |
| `investigation.txt` | Selected counts, filters, groups/members/examples, all selected event evidence, source options/quality, and status. |
| `investigation.json` | Schema version, filters, selected counts, all source quality/options/issues, signature version, groups and membership references, ordered original event rows, and status. |
| `investigation.csv` | Standard header plus one row per selected event, including group references/count/first/last where applicable. |
| `investigation.csv.metadata.json` | Matching CSV filter/source/status/group manifest, also written for zero-row selections. |

Repeated `--export text`, `--export csv`, or `--export json` selects combined formats. Choosing CSV always writes its metadata sidecar. The dashboard exposes equivalent text/CSV/JSON and CSV-metadata downloads, computed from the same selected view. Keep a CSV together with its metadata file. JSON preserves original text values directly.

CSV always quotes fields and uses CRLF record endings. Commas, quotes, Unicode, and embedded line endings survive CSV parsing. All text-valued cells are checked for common spreadsheet formula prefixes: `=`, `+`, `-`, or `@` after leading Unicode whitespace, or an initial tab/CR/LF. These receive one leading apostrophe. The `csv_escaped_fields` column is a JSON list of changed field names; remove exactly one leading apostrophe from those fields to recover originals. A pre-existing apostrophe that did not require escaping is not listed. This mitigates common formula-style cells; it is not a guarantee for every spreadsheet importer. Group signatures and source/raw fields receive the same treatment as messages.

Empty selections produce a header-only CSV, a populated metadata manifest, JSON with empty event/group arrays, and an explicit no-match text report when source input was valid. No synthetic data row is inserted to hold metadata.

All export and sidecar paths are checked against all selected input paths and the database before CLI output writes. Existing-file identity checks include filesystem aliases. Destination collisions are rejected. Serialization errors and write failures are surfaced. Phase 4 stages each file before atomic destination replacement; exports, sidecars, and the database still commit independently, so a bundle can contain artifacts from different completed attempts after a failure. Protection is local preflight, not a defense against concurrent hostile filesystem replacement.

## Remaining scope

Normal CLI/dashboard imports, queries, and exports are now database-backed with fingerprints, versioned runs/revisions, bounded batches/pages, exact SQL grouping, streamed serialization, and allocation regression checks. Small-data compatibility APIs still materialize results within explicit limits. Aggregate export-bundle atomicity, automatic disk retention/history UI, and total-RSS/browser profiling remain later work. See [STORAGE.md](STORAGE.md). No filters are inferred from filenames or machine timezones; there is no regex search, fuzzy clustering, anomaly detection, or root-cause inference in this phase.


Phase 5 production exports use schema 3: diagnostics add settings, occupied UTC buckets, threshold alerts, and ranked recurring signatures. Production status is rule-based with source-quality precedence; small-data compatibility exports retain their existing contracts. Event/group/filter and reversible CSV columns are unchanged. See [DIAGNOSTICS.md](DIAGNOSTICS.md).
