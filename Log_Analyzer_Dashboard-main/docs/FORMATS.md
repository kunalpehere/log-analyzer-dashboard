# Supported formats and ingestion contract

Choose `text` or `jsonl` before scanning. All entry points consume the same `ScanOptions`, parsers, and ingestion pipeline. Files and uploads use UTF-8 with an optional BOM. The BOM is removed by decoding; `raw_text` preserves the remaining decoded text, including leading/trailing spaces and original LF, CRLF, or CR line endings. This is decoded evidence, not a byte-for-byte copy of the original file encoding.

## Timestamps and severity

Supported timestamp grammar:

```text
YYYY-MM-DD[space or T]HH:MM:SS[.1-to-6-digits][Z or ±HH:MM]
```

Dates and clock times must be valid. Uppercase `T` and `Z` are required when used. Offsets require a colon, hours 00–23, and minutes 00–59. Leap seconds, timezone names, offset seconds, compact offsets, and more than six fractional digits are rejected. Values whose UTC conversion would overflow Python's supported calendar range are rejected.

Every event timestamp is a UTC-aware `datetime`. Naive policy `utc` assumes UTC and sets `timestamp_assumed=True`; policy `reject` rejects timestamps lacking an offset. The default is `utc` for compatibility with shipped samples. This assumption is recorded evidence about interpretation, not proof of the source's original timezone. Explicit `Z`/offset values set `timestamp_assumed=False`.

Levels are DEBUG, INFO, WARNING, ERROR, and CRITICAL, case-insensitive, with one optional trailing colon. WARN aliases WARNING; FATAL aliases CRITICAL. No other aliases are guessed. CRITICAL retains its own count, while the error total includes ERROR and CRITICAL.

## Text records

```text
2026-10-15 12:00:00 ERROR Request failed
2026-10-15T12:00:00.123456+05:30 ERROR Request failed
```

A timestamp, level, and nonempty message are required. Whitespace separates fields; internal message spacing is retained. Leading/trailing line whitespace is removed for interpretation, while raw evidence retains it. The parsed timestamp value uses a single space when date/time occupied separate tokens; the original spacing remains available in `raw_text`.

## JSON Lines records

```jsonl
{"timestamp":"2026-10-15T12:00:00Z","level":"ERROR","message":"Request failed","request_id":"abc"}
```

Each nonblank physical line must be one object. Required keys `timestamp`, `level`, and `message` must contain strings; the message must contain non-whitespace content. Message content is retained exactly after JSON decoding, including escaped newlines. Severity strings must not contain surrounding whitespace. Extra fields remain in the original raw JSON and are otherwise ignored. There is no field-name guessing, nested field extraction, epoch-number conversion, or message coercion.

Arrays, scalars, missing/wrong-type fields, malformed/trailing JSON, duplicate keys (including nested objects), nonstandard NaN/Infinity constants, and unpaired surrogate codepoints in messages are rejected. A message containing escaped newlines is still one logical event on one physical source line. JSON records never consume neighboring traceback-looking lines, even when text traceback assembly is enabled.

## Supported Python traceback assembly

A text ERROR/CRITICAL header can be followed immediately by the exact unindented marker:

```text
2026-10-15 12:00:00 ERROR Request failed
Traceback (most recent call last):
  File "app.py", line 3, in run
    work()
ValueError: invalid argument
```

The marker may instead be the entire header message (`ERROR Traceback (most recent call last):`). After the marker, nonblank indented lines are treated as frame/code/caret evidence. One unindented terminal exception line ends the event. Supported terminals are dotted Python identifiers followed by a colon and message, or bare identifiers ending in Error, Exception, Exit, or Interrupt. Custom exception classes with a colon are accepted without importing or executing them.

A valid next header or EOF closes a non-traceback event. A traceback missing its terminal exception is rejected as a block when EOF, a blank line, an unsupported unindented line, or a new valid header arrives. Blank lines terminate blocks and remain separate physical blank lines. A frame or terminal without a supported marker is rejected rather than attached to the preceding error. INFO/WARNING/DEBUG records do not own tracebacks. Exception chains, ExceptionGroup tree layouts, prefixed frame lines, and other multiline formats are not assembled; unsupported portions remain visible as issues. Detection is intentionally syntactic and conservative; it does not establish an exception's type or a root cause.

`tracebacks=False` disables assembly: valid headers remain events and continuation lines are rejected individually.

## Limits, recovery, and accounting

Defaults are `max_event_lines=200` and `max_event_chars=65536`. Limits include the header, marker, frames, terminal, and decoded line-ending characters; character limits count Unicode characters, not UTF-8 bytes. A single-line event is also checked against the character limit.

If adding a continuation would exceed either limit, the buffered event and overflow line are rejected with visible reasons. The remaining unsupported continuation lines are rejected individually, and the next valid header starts a new event. Incomplete/oversized blocks do not contribute an accepted ERROR event. Their original header and continuation evidence remains in issues, and the overall status is incomplete or uninterpretable.

Each accepted event contains source, inclusive start/end physical line numbers, raw text, UTC timestamp, original timestamp value, assumption flag, severity, and message. Each issue contains source, inclusive physical span, raw text, and reason. Spans are contiguous and never include blank lines. An iterable passed to `analyze_lines` must yield one physical line per item.

Completed scans satisfy:

```text
physical lines = blank lines + nonblank lines
nonblank lines = accepted events + accepted continuation lines + rejected nonblank lines
accepted events = sum(canonical severity counts)
```

`parsed_count` counts logical events; `continuation_lines` counts accepted nonblank lines after event headers; `rejected_count` counts rejected physical lines, which can exceed the number of issues. Read/decoding failures discard partial results, retain the selected options, and return a failed scan.

These are per-event assembly bounds. Phase 4 adds source-byte/physical-line/record limits, bounded application batches, paginated queries, and streamed exports. Streamlit still retains upload bytes; disk history and SQLite/native allocations are not claimed constant. See [STORAGE.md](STORAGE.md).

## Interfaces and storage

The dashboard and `main.py` expose format selection, naive policy, traceback enablement, and both limits. `main.run(..., options=ScanOptions(...))` and the public file/upload/iterable analysis functions use the same contracts. Compatibility modules `parser.py` and `analyzer.py` delegate to the package.

The dashboard and Phase 3 text/CSV/JSON investigation exports expose every selected event, with UTC/original timestamp metadata and raw source spans. Source ingestion issues remain visible separately, even when filters select no events. Phase 4 canonical revision storage retains all accepted severities and issues. Its legacy compatibility projection retains the unfiltered ERROR/CRITICAL/WARNING scope. Tables show bounded pages, while streamed exports include the full selected evidence. See [INVESTIGATION.md](INVESTIGATION.md) for filter and export contracts.

Write operations add nullable evidence columns to legacy SQLite tables in the same transaction as the operation. Existing rows are preserved on an additive upgrade and are never silently assigned UTC metadata. Replacing a snapshot intentionally replaces its old rows. Phase 4 now provides schema versioning, run/source revisions, an archive of initial legacy evidence, transactional imports, and pinned query snapshots; see [STORAGE.md](STORAGE.md).
