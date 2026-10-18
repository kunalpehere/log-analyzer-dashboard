from datetime import timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import database
from log_analyzer import ScanOptions
from log_analyzer.analysis import analyze_lines, analyze_log_file, analyze_upload
from main import cli, run
from report_writer import write_report

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/events.jsonl"


def test_json_file_upload_cli_report_and_storage_agree(tmp_path, capsys):
    options = ScanOptions(format="jsonl", naive_policy="reject")
    file_result = analyze_log_file(FIXTURE, options)
    assert file_result == analyze_upload(FIXTURE.read_bytes(), str(FIXTURE), options)
    report_dir = tmp_path / "reports"
    db_path = tmp_path / "test.db"
    assert run([FIXTURE], report_dir=report_dir, db_path=db_path, options=options) == 0
    output = capsys.readouterr().out
    assert "Parsed records: 3" in output
    assert "Total errors: 1" in output
    assert "Total warnings: 1" in output
    assert "Input format: jsonl; naive timestamp policy: reject" in output
    report = (report_dir / "events_report.txt").read_bytes().decode("utf-8")
    assert "2026-10-15T06:30:00.123456+00:00" in report
    assert "2026-10-15T12:00:00.123456+05:30" in report
    assert "assumed UTC: False" in report
    assert "Format: jsonl; naive timestamp policy: reject" in report
    assert "No rule alerts: No selected UTC bucket meets both configured thresholds." in report
    evidence = database.fetch_event_evidence(db_path)
    assert len(evidence) == 2
    for stored, event in zip(evidence, file_result.events[:2]):
        assert stored["timestamp_utc"] == event.timestamp.isoformat()
        assert stored["original_timestamp"] == event.original_timestamp
        assert stored["raw_text"] == event.raw_text
        assert stored["start_line"] == event.start_line
        assert stored["end_line"] == event.end_line
        assert bool(stored["timestamp_assumed"]) is event.timestamp_assumed
        assert stored["source"] == event.source


def test_traceback_evidence_survives_report_and_sqlite(tmp_path):
    raw = '2026-10-15 12:00:00.5+05:30 ERROR Failed\r\nTraceback (most recent call last):\r\n  File "app.py", line 1, in run\r\n    work()\r\nValueError: café\r\n'
    source = tmp_path / "trace.log"
    source.write_bytes(raw.encode("utf-8"))
    result = analyze_log_file(source)
    assert result.parsed_count == 1
    assert result.continuation_lines == 4
    report = write_report(result, tmp_path / "report.txt")
    report_bytes = report.read_bytes()
    assert raw.encode("utf-8") in report_bytes
    assert b"Continuation lines: 4" in report_bytes
    assert b"through line 5" in report_bytes
    db_path = tmp_path / "test.db"
    database.replace_entries([result], db_path)
    stored = database.fetch_event_evidence(db_path)[0]
    assert stored["raw_text"] == raw
    assert stored["timestamp_utc"] == "2026-10-15T06:30:00.500000+00:00"
    assert (stored["start_line"], stored["end_line"]) == (1, 5)


def legacy_database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE log_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT, date TEXT, time TEXT, level TEXT, message TEXT)")
        conn.execute("INSERT INTO log_entries (file,date,time,level,message) VALUES ('legacy','2024-01-01','12:00:00','ERROR','old')")


def test_legacy_schema_upgrade_is_additive_and_does_not_invent_timezone(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    before = database.fetch_all_entries(path)
    database.create_table(path)
    database.create_table(path)
    assert database.fetch_all_entries(path) == before
    stored = database.fetch_event_evidence(path)[0]
    for name in ("timestamp_utc", "original_timestamp", "timestamp_assumed", "start_line", "end_line", "raw_text"):
        assert stored[name] is None
    new = analyze_upload(b"2026-10-15 12:00:00 ERROR new", "new")
    database.insert_entries(new.source, new.error_log, [], path)
    evidence = database.fetch_event_evidence(path)
    assert evidence[-1]["timestamp_assumed"] == 1
    assert evidence[-1]["timestamp_utc"].endswith("+00:00")


def test_failed_schema_upgrade_and_snapshot_replacement_roll_back_together(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    before = database.fetch_all_entries(path)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("simulated write failure")
    monkeypatch.setattr(database, "_insert", fail)
    result = analyze_upload(b"2026-10-15 12:00:00 ERROR new", "new")
    with pytest.raises(sqlite3.OperationalError):
        database.replace_entries([result], path)
    assert database.fetch_all_entries(path) == before
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(log_entries)")}
    assert "timestamp_utc" not in columns


def test_utc_storage_sorting_crosses_dates_and_preserves_fractions(tmp_path):
    raw = [
        "2026-01-01T00:00:00.000002Z ERROR second",
        "2026-01-01T01:00:00+02:00 ERROR previous day",
        "2026-01-01T00:00:00.000001Z ERROR first",
    ]
    result = analyze_lines(raw, "input")
    path = tmp_path / "test.db"
    database.replace_entries([result], path)
    assert [row[-1] for row in database.fetch_all_entries(path)] == ["previous day", "first", "second"]


def test_actual_cli_json_command_from_another_directory(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    outcome = subprocess.run([
        sys.executable, "-B", str(ROOT / "main.py"), str(FIXTURE), "--format", "jsonl",
        "--naive-policy", "reject", "--report-dir", str(tmp_path / "reports"),
        "--database", str(tmp_path / "test.db"),
    ], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert outcome.returncode == 0, outcome.stderr
    assert "Parsed records: 3" in outcome.stdout
    assert len(database.fetch_event_evidence(tmp_path / "test.db")) == 2


@pytest.mark.parametrize("args", [["--format", "auto"], ["--naive-policy", "local"], ["--max-event-lines", "0"], ["--max-event-chars", "-1"]])
def test_cli_invalid_options_reject_before_writes(tmp_path, args):
    with pytest.raises(SystemExit) as error:
        cli([*args, "--report-dir", str(tmp_path / "reports"), "--database", str(tmp_path / "test.db")])
    assert error.value.code == 2
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "test.db").exists()


def test_cli_naive_rejection_preserves_database(tmp_path):
    path = tmp_path / "test.db"
    old = analyze_upload(b"2026-10-15T12:00:00Z ERROR old", "old")
    database.replace_entries([old], path)
    source = tmp_path / "naive.log"
    source.write_bytes(b"2026-10-15 12:00:00 ERROR new")
    assert cli([str(source), "--naive-policy", "reject", "--report-dir", str(tmp_path / "reports"), "--database", str(path)]) == 1
    assert database.fetch_all_entries(path)[0][-1] == "old"
    assert "Naive timestamp rejected" in (tmp_path / "reports/naive_report.txt").read_text()


def test_compatibility_modules_delegate_to_same_core():
    import analyzer
    import parser
    from log_analyzer.analysis import analyze_upload as shared
    from log_analyzer.models import Severity as shared_severity
    assert analyzer.analyze_upload is shared
    assert parser.Severity is shared_severity
