import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import database
from analyzer import analyze_lines
from generate_logs import generate_logs
from main import DEFAULT_LOG_FILES, run

ROOT = Path(__file__).resolve().parents[1]


def test_imports_do_not_write_or_execute(tmp_path):
    script = """
import builtins, io, pathlib, sqlite3
original_open = builtins.open
original_io_open = io.open

def guard(original):
    def guarded(file, mode='r', *args, **kwargs):
        if any(flag in mode for flag in 'wax+'):
            raise AssertionError('Import attempted a write: ' + str(file))
        return original(file, mode, *args, **kwargs)
    return guarded
builtins.open = guard(original_open)
io.open = guard(original_io_open)
def no_connect(*args, **kwargs):
    raise AssertionError('Import attempted SQLite access')
sqlite3.connect = no_connect
import parser, analyzer, status_policy, report_writer, database, main, generate_logs, dashboard
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    outcome = subprocess.run([sys.executable, "-B", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_cli_sample_regression_from_another_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "test.db"
    reports = tmp_path / "reports"
    assert run(report_dir=reports, db_path=db_path) == 0
    text = capsys.readouterr().out
    assert "Total lines: 10048" in text
    assert "Total errors: 543" in text
    assert "Total warnings: 1487" in text
    assert len(database.fetch_all_entries(db_path)) == 2030
    assert len(list(reports.glob("*_report.txt"))) == 4
    assert all(path.is_absolute() for path in DEFAULT_LOG_FILES)


@pytest.mark.parametrize("content", [None, "", "malformed input"])
def test_cli_failed_empty_or_rejected_source_preserves_previous_db(tmp_path, content, capsys):
    db_path = tmp_path / "previous.db"
    original = analyze_lines(["2026-10-15 12:00:00 ERROR previous"], "previous")
    database.replace_entries([original], db_path)
    before = database.fetch_all_entries(db_path)
    source = tmp_path / "input.log"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    assert run([source], report_dir=tmp_path / "reports", db_path=db_path) == 1
    assert database.fetch_all_entries(db_path) == before
    assert "Database refresh skipped" in capsys.readouterr().out
    assert "No errors observed:" not in (tmp_path / "reports/input_report.txt").read_text()


def test_cli_mixed_rejections_are_visible_and_nonzero(tmp_path, capsys):
    source = tmp_path / "input.log"
    source.write_text("2026-10-15 12:00:00 ERROR evidence\nbad", encoding="utf-8")
    db_path = tmp_path / "test.db"
    assert run([source], report_dir=tmp_path / "reports", db_path=db_path) == 1
    assert len(database.fetch_all_entries(db_path)) == 1
    assert "Rejected lines: 1" in capsys.readouterr().out


def test_cli_preflights_cross_input_overwrites(tmp_path):
    source = tmp_path / "input.log"
    second = tmp_path / "input_report.txt"
    source.write_text("2026-10-15 12:00:00 ERROR evidence", encoding="utf-8")
    second.write_text("preserve me", encoding="utf-8")
    assert run([source, second], report_dir=tmp_path, db_path=tmp_path / "test.db") == 1
    assert second.read_text() == "preserve me"
    assert not (tmp_path / "test.db").exists()


def test_cli_db_cannot_overwrite_input(tmp_path):
    source = tmp_path / "input.log"
    source.write_text("preserve me", encoding="utf-8")
    assert run([source], report_dir=tmp_path / "reports", db_path=source) == 1
    assert source.read_text() == "preserve me"
    assert not (tmp_path / "reports").exists()


def test_cli_colliding_report_names_fail_before_write(tmp_path):
    first = tmp_path / "first/input.log"
    second = tmp_path / "second/input.log"
    first.parent.mkdir()
    second.parent.mkdir()
    for path in (first, second):
        path.write_text("2026-10-15 12:00:00 INFO ready", encoding="utf-8")
    assert run([first, second], report_dir=tmp_path / "reports", db_path=tmp_path / "test.db") == 1
    assert not (tmp_path / "reports").exists()


def test_database_refresh_rolls_back_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    old = analyze_lines(["2026-10-15 12:00:00 ERROR old"], "old")
    database.replace_entries([old], path)
    broken = analyze_lines(["2026-10-15 12:00:00 ERROR new"], "new")
    original_insert = database._insert
    def fail_after_insert(conn, source, entries):
        original_insert(conn, source, entries)
        raise sqlite3.OperationalError("simulated storage failure")
    monkeypatch.setattr(database, "_insert", fail_after_insert)
    with pytest.raises(sqlite3.OperationalError):
        database.replace_entries([broken], path)
    assert database.fetch_all_entries(path)[0][-1] == "old"


def test_database_preserves_repeated_events_and_quotes(tmp_path):
    path = tmp_path / "test.db"
    result = analyze_lines(["2026-10-15 12:00:00 FATAL can't connect"] * 2, "input")
    database.replace_entries([result], path)
    rows = database.fetch_all_entries(path)
    assert len(rows) == 2
    assert all(row[-2:] == ("CRITICAL", "can't connect") for row in rows)
    database.replace_entries([result], path)
    assert len(database.fetch_all_entries(path)) == 2
    database.clear_table(path)
    database.insert_entries(result.source, result.error_log, [], path)
    assert len(database.fetch_all_entries(path)) == 2


def test_generator_is_reproducible_in_isolation(tmp_path):
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    generate_logs(first, seed=42)
    generate_logs(second, seed=42)
    assert first.read_bytes() == second.read_bytes()
    assert len(first.read_text().splitlines()) == 10000
