from datetime import datetime, timezone
from io import StringIO
import json

import pytest

from log_analyzer import LogFormat, NaivePolicy, ScanOptions, Severity
from log_analyzer.analysis import analyze_lines, analyze_log_file, analyze_upload
from log_analyzer.parsers import get_parser
from parser import parse_line

HEADER = "2026-10-15 12:00:00 ERROR Request failed\n"
MARKER = "Traceback (most recent call last):\n"
FRAME = '  File "app.py", line 3, in run\n'
CODE = "    work()\n"
TERMINAL = "ValueError: invalid argument\n"
TRACE = HEADER + MARKER + FRAME + CODE + TERMINAL


def scan(text, options=ScanOptions()):
    return analyze_lines(StringIO(text, newline=""), "input", options)


def assert_accounting(result):
    assert result.total_lines == result.parsed_count + result.continuation_lines + result.rejected_count
    assert result.parsed_count == len(result.events) == sum(result.counts.values())
    assert result.physical_lines >= result.total_lines
    for event in result.events:
        assert event.timestamp.tzinfo is timezone.utc
        assert event.end_line >= event.start_line


@pytest.mark.parametrize("stamp,expected", [
    ("2026-10-15 12:00:00", "2026-10-15T12:00:00+00:00"),
    ("2026-10-15T12:00:00Z", "2026-10-15T12:00:00+00:00"),
    ("2026-10-15 12:00:00.1+05:30", "2026-10-15T06:30:00.100000+00:00"),
    ("2026-10-15T12:00:00.123456-04:00", "2026-10-15T16:00:00.123456+00:00"),
    ("2026-01-01T00:15:00+01:00", "2025-12-31T23:15:00+00:00"),
    ("2026-12-31T23:30:00-01:00", "2027-01-01T00:30:00+00:00"),
    ("2024-02-29T23:59:59.000001Z", "2024-02-29T23:59:59.000001+00:00"),
    ("0001-01-01T00:00:00Z", "0001-01-01T00:00:00+00:00"),
])
def test_text_json_timestamp_equivalence(stamp, expected):
    text = scan(f"{stamp} warn: slow\n")
    structured = scan(json.dumps({"timestamp": stamp, "level": "warn:", "message": "slow"}) + "\n", ScanOptions(format="jsonl"))
    for result in (text, structured):
        assert_accounting(result)
        assert result.parsed_count == 1
        event = result.events[0]
        assert event.timestamp.isoformat() == expected
        assert event.original_timestamp == stamp
        assert event.timestamp_assumed == (not (stamp.endswith("Z") or "+" in stamp[10:] or "-" in stamp[10:]))
        assert event.level == Severity.WARNING
    assert text.counts == structured.counts
    assert text.events[0].timestamp == structured.events[0].timestamp


@pytest.mark.parametrize("stamp", [
    "2026-02-29T12:00:00Z", "2026-10-15T24:00:00Z", "2026-10-15T12:00:60Z",
    "2026-10-15T12:00:00.1234567Z", "2026-10-15T12:00:00+24:00",
    "2026-10-15T12:00:00+01:60", "2026-10-15T12:00:00-00:99",
    "2026-10-15T12:00:00+0530", "2026-10-15t12:00:00z", "2026-10-15",
    "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
])
def test_invalid_timestamps_do_not_escape_parser(stamp):
    for format in LogFormat:
        raw = f"{stamp} ERROR failed" if format == LogFormat.TEXT else json.dumps({"timestamp": stamp, "level": "ERROR", "message": "failed"})
        result = scan(raw, ScanOptions(format=format))
        assert result.parsed_count == 0
        assert result.rejected_count == 1
        assert_accounting(result)


@pytest.mark.parametrize("format", list(LogFormat))
def test_naive_policy_is_explicit_and_recorded(format):
    stamp = "2026-10-15T12:00:00"
    raw = f"{stamp} INFO ready" if format == LogFormat.TEXT else json.dumps({"timestamp": stamp, "level": "INFO", "message": "ready"})
    assumed = scan(raw, ScanOptions(format=format))
    rejected = scan(raw, ScanOptions(format=format, naive_policy="reject"))
    assert assumed.events[0].timestamp_assumed is True
    assert rejected.options.naive_policy == NaivePolicy.REJECT
    assert "Naive timestamp rejected" in rejected.issues[0].reason
    aware = raw.replace(stamp, stamp + "Z")
    assert scan(aware, ScanOptions(format=format, naive_policy="reject")).parsed_count == 1


@pytest.mark.parametrize("data,reason", [
    ({}, "timestamp"), ({"timestamp": 123, "level": "INFO", "message": "x"}, "timestamp"),
    ({"timestamp": "2026-10-15T12:00:00Z", "message": "x"}, "level"),
    ({"timestamp": "2026-10-15T12:00:00Z", "level": None, "message": "x"}, "level"),
    ({"timestamp": "2026-10-15T12:00:00Z", "level": "INFO", "message": False}, "message"),
    ({"timestamp": "2026-10-15T12:00:00Z", "level": "INFO", "message": "  "}, "nonempty"),
    ({"timestamp": "2026-10-15T12:00:00Z", "level": "TRACE", "message": "x"}, "severity"),
    ({"timestamp": "2026-10-15T12:00:00Z", "level": " INFO ", "message": "x"}, "whitespace"),
    ([], "object"), (None, "object"), (True, "object"), (42, "object"),
])
def test_json_contract(data, reason):
    result = scan(json.dumps(data), ScanOptions(format="jsonl"))
    assert result.parsed_count == 0
    assert reason.lower() in result.issues[0].reason.lower()
    assert_accounting(result)


@pytest.mark.parametrize("raw", [
    "{bad json}", '{"timestamp":"x","timestamp":"y"}',
    '{"timestamp":"2026-10-15T12:00:00Z","level":"INFO","message":"x","other":NaN}',
    '{"timestamp":"2026-10-15T12:00:00Z","level":"INFO","message":"x","other":Infinity}',
    '{"timestamp":"2026-10-15T12:00:00Z","level":"INFO","message":"\\ud800"}',
    '{"timestamp":"2026-10-15T12:00:00Z","level":"INFO","message":"x"} trailing',
])
def test_ambiguous_or_invalid_json_is_rejected(raw):
    result = scan(raw, ScanOptions(format="jsonl"))
    assert result.parsed_count == 0
    assert result.rejected_count == 1


def test_json_preserves_message_and_unknown_metadata_in_raw_evidence():
    raw = json.dumps({"timestamp": "2026-10-15T12:00:00Z", "level": "ERROR", "message": "  cafÃ©\nsecond line  ", "request_id": "abc"}) + "\r\n"
    result = scan(raw, ScanOptions(format="jsonl"))
    assert result.events[0].message == "  cafÃ©\nsecond line  "
    assert result.events[0].raw_text == raw
    assert result.physical_lines == result.parsed_count == 1
    assert result.continuation_lines == 0


def test_format_selection_is_explicit():
    raw = json.dumps({"timestamp": "2026-10-15T12:00:00Z", "level": "ERROR", "message": "failure"})
    assert scan(raw).rejected_count == 1
    assert scan("2026-10-15 12:00:00 ERROR failure", ScanOptions(format="jsonl")).rejected_count == 1
    assert get_parser(LogFormat.TEXT).__class__.__name__ == "TextParser"


def test_traceback_assembles_one_event_and_preserves_raw_span():
    content = TRACE + "\n2026-10-15T12:00:01Z INFO recovered\n"
    result = scan(content)
    assert_accounting(result)
    assert result.physical_lines == 7
    assert result.total_lines == 6
    assert result.parsed_count == 2
    assert result.continuation_lines == 4
    assert result.error_count == 1
    event = result.events[0]
    assert (event.start_line, event.end_line) == (1, 5)
    assert event.raw_text == TRACE
    assert event.message == "Request failed\n" + (MARKER + FRAME + CODE + TERMINAL).rstrip("\n")
    assert result.events[1].start_line == 7


@pytest.mark.parametrize("terminal", ["ValueError", "Exception", "SystemExit", "KeyboardInterrupt", "mypackage.CustomFailure: detail", "CustomProblem: detail"])
def test_supported_terminal_exception_forms(terminal):
    result = scan(HEADER + MARKER + FRAME + terminal + "\n")
    assert result.parsed_count == 1
    assert result.rejected_count == 0
    assert result.continuation_lines == 3


def test_traceback_marker_in_header_message():
    raw = "2026-10-15 12:00:00 FATAL " + MARKER + FRAME + TERMINAL
    result = scan(raw)
    assert result.parsed_count == result.error_count == 1
    assert result.events[0].raw_text == raw
    assert result.continuation_lines == 2


@pytest.mark.parametrize("tail", ["", "\n", "2026-10-15 12:00:01 INFO next\n", "unrecognized unindented line\n"])
def test_incomplete_traceback_is_rejected_as_a_block(tail):
    result = scan(HEADER + MARKER + FRAME + tail)
    assert_accounting(result)
    assert result.issues[0].line_number == 1
    assert result.issues[0].end_line == 3
    assert result.issues[0].raw_text == HEADER + MARKER + FRAME
    assert "Incomplete traceback" in result.issues[0].reason
    assert result.error_count == 0
    assert result.rejected_count >= 3


@pytest.mark.parametrize("content", [MARKER, FRAME, TERMINAL, "    orphan code\n"])
def test_orphan_continuations_are_visible(content):
    result = scan(content)
    assert result.parsed_count == 0
    assert result.rejected_count == 1
    assert "Orphan" in result.issues[0].reason


def test_indentation_without_marker_does_not_attach_to_previous_error():
    result = scan(HEADER + FRAME + TERMINAL)
    assert result.parsed_count == 1
    assert result.events[0].raw_text == HEADER
    assert result.rejected_count == 2
    assert_accounting(result)


def test_info_record_cannot_own_a_traceback():
    result = scan(HEADER.replace("ERROR", "INFO") + MARKER + FRAME + TERMINAL)
    assert result.parsed_count == 1
    assert result.rejected_count == 3
    assert result.continuation_lines == 0


def test_disabled_traceback_assembly_does_not_lose_lines():
    result = scan(TRACE, ScanOptions(tracebacks=False))
    assert result.parsed_count == 1
    assert result.rejected_count == 4
    assert result.continuation_lines == 0
    assert_accounting(result)


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_traceback_line_limit_rejects_whole_event_and_recovers(limit):
    result = scan(TRACE + "2026-10-15 12:00:01 INFO next\n", ScanOptions(max_event_lines=limit))
    assert result.parsed_count == 1
    assert result.error_count == 0
    assert result.rejected_count == 5
    assert any("limit exceeded" in issue.reason for issue in result.issues)
    assert result.events[0].message == "next"
    assert_accounting(result)


def test_exact_line_and_character_limit_boundaries():
    exact = scan(TRACE, ScanOptions(max_event_lines=5, max_event_chars=len(TRACE)))
    assert exact.parsed_count == 1 and not exact.issues
    short = scan(TRACE, ScanOptions(max_event_chars=len(TRACE) - 1))
    assert short.parsed_count == 0
    assert short.rejected_count == 5
    assert_accounting(short)


@pytest.mark.parametrize("format", list(LogFormat))
def test_single_oversized_record_is_rejected(format):
    raw = HEADER if format == LogFormat.TEXT else json.dumps({"timestamp": "2026-10-15T12:00:00Z", "level": "ERROR", "message": "too long"})
    result = scan(raw, ScanOptions(format=format, max_event_chars=10))
    assert result.rejected_count == 1
    assert "character limit" in result.issues[0].reason


@pytest.mark.parametrize("kwargs", [
    {"format": "auto"}, {"naive_policy": "local"}, {"max_event_lines": 0},
    {"max_event_chars": -1}, {"max_event_lines": True}, {"max_event_chars": 2.5}, {"tracebacks": 1},
])
def test_invalid_options_fail_before_analysis(kwargs):
    with pytest.raises(ValueError):
        ScanOptions(**kwargs)


@pytest.mark.parametrize("format", list(LogFormat))
@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_file_upload_iterable_parity_with_original_line_endings(tmp_path, format, ending):
    if format == LogFormat.TEXT:
        raw = TRACE.replace("\n", ending) + ending + "bad" + ending
    else:
        raw = json.dumps({"timestamp": "2026-10-15T12:00:00.5+05:30", "level": "FATAL", "message": "failure"}) + ending + "bad" + ending
    content = b"\xef\xbb\xbf" + raw.encode("utf-8")
    path = tmp_path / "input.log"
    path.write_bytes(content)
    options = ScanOptions(format=format)
    file_result = analyze_log_file(path, options)
    assert file_result == analyze_upload(content, str(path), options)
    assert file_result == analyze_lines(StringIO(raw, newline=""), str(path), options)
    assert file_result.events[0].raw_text.endswith(ending)
    assert_accounting(file_result)


def test_chained_traceback_separator_is_not_silently_attached():
    result = scan(TRACE + "\nDuring handling of the above exception, another exception occurred:\n" + MARKER + FRAME + TERMINAL)
    assert result.parsed_count == 1
    assert result.rejected_count == 4
    assert_accounting(result)


def test_json_message_with_newlines_does_not_assemble_neighboring_lines():
    raw = json.dumps({"timestamp": "2026-10-15T12:00:00Z", "level": "ERROR", "message": "failure\n" + MARKER + FRAME + TERMINAL})
    result = scan(raw + "\n" + FRAME, ScanOptions(format="jsonl"))
    assert result.parsed_count == 1
    assert result.rejected_count == 1
    assert result.events[0].end_line == 1
    assert_accounting(result)
