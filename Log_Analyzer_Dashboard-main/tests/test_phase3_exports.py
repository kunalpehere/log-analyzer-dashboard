import csv
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path

import pytest

from log_analyzer.analysis import analyze_lines, analyze_log_file
from log_analyzer.exporting import CSV_COLUMNS, csv_safe_cell, render_export, render_metadata, write_export
from log_analyzer.filtering import FilterSpec
from log_analyzer.investigation import investigate


def make_view():
    result = analyze_lines([
        "2026-10-15T12:00:01Z ERROR ValueError code=E42 request_id=bbbbbbbbbbbbbbbb",
        "2026-10-15T12:00:00Z ERROR ValueError code=E42 request_id=aaaaaaaaaaaaaaaa",
        "2026-10-15T12:00:02Z WARNING slow",
        "2026-10-15T12:00:03Z INFO ready",
    ], "source.log")
    return investigate([result])


def read_csv(view):
    return list(csv.DictReader(StringIO(render_export(view, "csv"), newline="")))


def test_all_exports_share_selected_counts_order_groups_and_evidence():
    original = make_view()
    view = investigate(original.sources, FilterSpec(severities={"ERROR"}))
    document = json.loads(render_export(view, "json"))
    rows = read_csv(view)
    text = render_export(view, "text")
    manifest = json.loads(render_metadata(view))
    assert document["selected"]["event_count"] == len(rows) == len(view.events) == 2
    assert document["selected"]["error_count"] == 2
    assert manifest == {key: value for key, value in document.items() if key != "events"}
    assert [row["event_id"] for row in rows] == [row["event_id"] for row in document["events"]]
    assert [row["timestamp_utc"] for row in rows] == [row["timestamp_utc"] for row in document["events"]]
    assert document["groups"][0]["count"] == 2
    assert document["groups"][0]["event_ids"] == [row["event_id"] for row in rows]
    for row in document["events"]:
        assert row["message"] in text
        assert row["raw_text"] in text
        assert f"Event: {row['event_id']};" in text
        assert row["group_id"] == document["groups"][0]["group_id"]
    assert "Parsed records: 2" in text and "Total errors: 2" in text
    assert all(int(row["group_count"]) == 2 for row in rows)


@pytest.mark.parametrize("value", ["=SUM(1,2)", "+cmd", "-1+2", "@SUM(A1)", "   =2+2", "\ttext", "\rtext", "\ntext", " \t-1", "\v=2", "\u00a0=2"])
def test_formula_like_text_is_escaped_reversibly_in_every_text_field(value):
    source = make_view().sources[0]
    base = source.events[0]
    dangerous = replace(base, source=value, message=value, raw_text=value, original_timestamp=value)
    result = replace(source, source=value, events=[dangerous])
    view = investigate([result])
    row = read_csv(view)[0]
    escaped = json.loads(row["csv_escaped_fields"])
    for key in ("source", "message", "raw_text", "original_timestamp", "group_signature"):
        assert key in escaped
        assert row[key] == "'" + value
    original = json.loads(render_export(view, "json"))["events"][0]
    assert original["source"] == original["message"] == original["raw_text"] == value
    assert value in render_export(view, "text")


@pytest.mark.parametrize("value", ["ordinary", "2026-10-15T12:00:00+00:00", "'literal", " leading ordinary", "", "échec"])
def test_ordinary_csv_cells_are_not_changed(value):
    assert csv_safe_cell(value) == (value, False)


def test_csv_quotes_multiline_unicode_and_commas_without_losing_originals():
    base = make_view().sources[0]
    message = 'café, "quoted"\r\nsecond line'
    event = replace(base.events[0], message=message, raw_text=message)
    view = investigate([replace(base, events=[event])])
    row = read_csv(view)[0]
    assert row["message"] == message
    assert row["raw_text"] == message
    assert json.loads(row["csv_escaped_fields"]) == []
    assert '""quoted""' in render_export(view, "csv")


def test_literal_apostrophe_is_distinguished_from_added_escape():
    base = make_view().sources[0]
    event = replace(base.events[0], message="'=literal")
    view = investigate([replace(base, events=[event])])
    row = read_csv(view)[0]
    assert row["message"] == "'=literal"
    assert "message" not in json.loads(row["csv_escaped_fields"])


def test_empty_selection_has_headers_and_reproducible_settings(tmp_path):
    view = investigate(make_view().sources, FilterSpec(text="not found"))
    path = write_export(view, tmp_path / "empty.csv", "csv")
    assert list(csv.DictReader(StringIO(path.read_text(encoding="utf-8")))) == []
    assert next(csv.reader(StringIO(path.read_text(encoding="utf-8")))) == CSV_COLUMNS
    manifest = json.loads(Path(str(path) + ".metadata.json").read_text(encoding="utf-8"))
    assert manifest["filters"]["message_contains"] == "not found"
    assert manifest["selected"]["event_count"] == 0
    assert manifest["status"]["label"] == "No matches"
    assert manifest["ingestion_quality_unfiltered"][0]["parsed_events"] == 4
    assert json.loads(render_export(view, "json"))["events"] == []
    assert "No matches:" in render_export(view, "text")


@pytest.mark.parametrize("format", ["text", "csv", "json"])
def test_all_export_formats_reject_input_overwrite(tmp_path, format):
    source = tmp_path / "input.log"
    source.write_bytes(b"2026-10-15T12:00:00Z ERROR original")
    view = investigate([analyze_log_file(source)])
    before = source.read_bytes()
    with pytest.raises(ValueError, match="overwrite input"):
        write_export(view, source, format)
    assert source.read_bytes() == before


def test_csv_sidecar_collision_is_checked_before_csv_write(tmp_path):
    source = tmp_path / "export.csv.metadata.json"
    source.write_bytes(b"2026-10-15T12:00:00Z INFO original")
    view = investigate([analyze_log_file(source)])
    with pytest.raises(ValueError):
        write_export(view, tmp_path / "export.csv", "csv")
    assert not (tmp_path / "export.csv").exists()
    assert source.read_bytes().endswith(b"original")


def test_invalid_format_fails_before_writing(tmp_path):
    path = tmp_path / "bad.out"
    with pytest.raises(ValueError):
        write_export(make_view(), path, "xml")
    assert not path.exists()


def test_export_write_failure_is_explicit(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(Path, "write_text", denied)
    with pytest.raises(PermissionError):
        write_export(make_view(), tmp_path / "output.json", "json")


@pytest.mark.parametrize("format", ["text", "csv", "json"])
def test_serialization_is_deterministic(format):
    view = make_view()
    assert render_export(view, format) == render_export(view, format)
    result = view.sources[0]
    reordered = replace(result, events=list(reversed(result.events)))
    assert render_export(view, format) == render_export(investigate([reordered]), format)


def test_source_issues_are_metadata_and_never_filtered_events():
    result = analyze_lines(["bad input", "2026-10-15T12:00:00Z INFO ready"], "source")
    view = investigate([result], FilterSpec(text="absent"))
    document = json.loads(render_export(view, "json"))
    assert not document["events"] and not document["groups"]
    assert document["status"]["label"] == "Incomplete"
    assert document["ingestion_quality_unfiltered"][0]["rejected_lines"] == 1
    assert document["ingestion_quality_unfiltered"][0]["issues"][0]["raw_text"] == "bad input"
