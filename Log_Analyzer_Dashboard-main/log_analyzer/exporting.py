"""Deterministic text/JSON/CSV exports from a single investigation snapshot."""

import csv
from dataclasses import asdict
from io import StringIO
import json
from pathlib import Path
from typing import Iterable

from .grouping import SIGNATURE_VERSION
from .investigation import Investigation
from .models import Severity
from .paths import validate_output_path
from status_policy import assess_investigation

CSV_COLUMNS = [
    "event_id", "timestamp_utc", "level", "source", "start_line", "end_line",
    "message", "raw_text", "original_timestamp", "timestamp_assumed",
    "group_id", "group_signature", "group_count", "group_first_seen_utc", "group_last_seen_utc",
    "csv_escaped_fields",
]


def metadata(view: Investigation) -> dict:
    groups = []
    for group, summary in zip(view.groups, view.group_rows):
        groups.append(dict(summary, signature_tokens=group.tokens,
                           event_ids=[f"event-{index + 1:06d}" for index in group.member_indices],
                           example_event_ids=[f"event-{index + 1:06d}" for index in group.example_indices]))
    return {
        "schema_version": 1,
        "filters": view.filters.as_dict(),
        "signature_version": SIGNATURE_VERSION,
        "event_order": "UTC timestamp, source, start/end line, severity, message, raw text, original timestamp, assumption flag",
        "event_id_scope": "one deterministic investigation export; genuine duplicates have distinct IDs",
        "selected": {"event_count": len(view.events), "error_count": view.error_count,
                     "warning_count": view.warning_count, "continuation_lines": view.continuation_lines,
                     "nonblank_evidence_lines": sum(event.nonblank_lines for event in view.events),
                     "counts": {level.value: count for level, count in view.counts.items()}},
        "status": asdict(assess_investigation(view)),
        "ingestion_quality_unfiltered": [
            {"source": result.source, "state": result.state.value, "failure": result.failure,
             "options": asdict(result.options), "physical_lines": result.physical_lines,
             "nonblank_lines": result.total_lines, "parsed_events": result.parsed_count,
             "continuation_lines": result.continuation_lines, "rejected_lines": result.rejected_count,
             "issues": [asdict(issue) for issue in result.issues]}
            for result in sorted(view.sources, key=lambda item: item.source)
        ],
        "csv_safety": {"formula_prefix": "apostrophe", "escaped_columns_field": "csv_escaped_fields",
                       "original_values": "JSON and text preserve originals; remove exactly one leading apostrophe from CSV fields listed in csv_escaped_fields"},
        "groups": groups,
    }


def render_metadata(view: Investigation) -> str:
    return json.dumps(metadata(view), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def csv_safe_cell(value) -> tuple[object, bool]:
    if isinstance(value, str):
        stripped = value.lstrip()
        if value.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
            return "'" + value, True
    return value, False


def render_csv(view: Investigation) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writeheader()
    groups = {group.signature_id: row for group, row in zip(view.groups, view.group_rows)}
    for event in view.event_rows:
        group = groups.get(event["group_id"], {})
        row = {key: event.get(key, "") for key in CSV_COLUMNS}
        row.update(group_signature=group.get("signature", ""), group_count=group.get("count", ""),
                   group_first_seen_utc=group.get("first_seen_utc", ""), group_last_seen_utc=group.get("last_seen_utc", ""))
        escaped = []
        for key in CSV_COLUMNS[:-1]:
            row[key], changed = csv_safe_cell(row[key])
            if changed:
                escaped.append(key)
        row["csv_escaped_fields"] = json.dumps(escaped, ensure_ascii=False)
        writer.writerow(row)
    return output.getvalue()


def render_text(view: Investigation) -> str:
    meta = metadata(view)
    selected = meta["selected"]
    status = meta["status"]
    lines = ["=" * 60, "LOG INVESTIGATION REPORT", "=" * 60,
             f"Parsed records: {len(view.events)}", f"Selected events: {len(view.events)}",
             f"Selected nonblank evidence lines: {selected['nonblank_evidence_lines']}",
             f"Continuation lines: {view.continuation_lines}",
             f"Total errors: {view.error_count}", f"Total warnings: {view.warning_count}",
             "Errors include ERROR and CRITICAL records.", "Normalized timezone: UTC",
             "Filters: " + json.dumps(meta["filters"], ensure_ascii=False, sort_keys=True)]
    lines.extend(f"{level.value}: {count}" for level, count in view.counts.items())
    lines.extend(["", "---SOURCE INGESTION QUALITY (UNFILTERED)---"])
    for result in sorted(view.sources, key=lambda item: item.source):
        lines.extend([f"Source: {result.source}; scan state: {result.state.value}",
                      f"Physical lines: {result.physical_lines}; nonblank lines: {result.total_lines}; parsed source events: {result.parsed_count}",
                      f"Rejected lines: {result.rejected_count}; parse issues: {len(result.issues)}",
                      f"Format: {result.options.format.value}; naive timestamp policy: {result.options.naive_policy.value}",
                      f"Tracebacks: {result.options.tracebacks}; maximum event lines: {result.options.max_event_lines}; maximum event characters: {result.options.max_event_chars}"])
        if result.failure:
            lines.append(f"Read failure: {result.failure}")
        lines.extend(f"line {issue.line_number}: through line {issue.end_line}; {issue.reason} | {issue.raw_text}" for issue in result.issues)
    lines.extend(["", f"---ERROR GROUPS ({SIGNATURE_VERSION})---"])
    for group in meta["groups"]:
        lines.extend([f"Group: {group['group_id']}; {group['level']}; count: {group['count']}",
                      f"Signature: {group['signature']}",
                      f"First: {group['first_seen_utc']}; last: {group['last_seen_utc']}",
                      "Members: " + ", ".join(group["event_ids"]),
                      "Examples: " + ", ".join(group["example_event_ids"])])
    lines.extend(["", "---SELECTED EVENT EVIDENCE---"])
    for row in view.event_rows:
        lines.extend([f"Event: {row['event_id']}; group: {row['group_id'] or 'none'}; source: {row['source']}",
                      f"line {row['start_line']}: through line {row['end_line']}; {row['timestamp_utc']} {row['level']} -> {row['message']}",
                      f"Original timestamp: {row['original_timestamp']}; assumed UTC: {row['timestamp_assumed']}",
                      "---BEGIN RAW---\n" + row["raw_text"] + "\n---END RAW---"])
    lines.extend(["", "---STATUS---", f"{status['label']}: {status['message']}", ""])
    return "\n".join(lines)


def render_export(view: Investigation, format: str) -> str:
    if format == "text":
        return render_text(view)
    if format == "csv":
        return render_csv(view)
    if format == "json":
        return json.dumps(dict(metadata(view), events=view.event_rows), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    raise ValueError(f"Unsupported export format: {format}")


def write_export(view: Investigation, output_path: str | Path, format: str, *, protected_inputs: Iterable[str | Path] = ()) -> Path:
    inputs = [result.source for result in view.sources] + list(protected_inputs)
    destination = validate_output_path(output_path, inputs)
    sidecar = None
    if format == "csv":
        sidecar = validate_output_path(str(destination) + ".metadata.json", [*inputs, destination])
    # Complete validation and serialization before any output file is written.
    content = render_export(view, format)
    manifest = render_metadata(view) if sidecar else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="")
    if sidecar:
        sidecar.write_text(manifest, encoding="utf-8", newline="")
    return destination
