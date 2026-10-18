import csv
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import database
from log_analyzer.analysis import analyze_log_file
from log_analyzer.exporting import render_export
from log_analyzer.filtering import FilterSpec, parse_filter_time
from log_analyzer.investigation import investigate
from main import cli, run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/investigation.log"


def filters():
    return FilterSpec(parse_filter_time("2026-10-15T12:00:01Z"), parse_filter_time("2026-10-15T12:00:02Z"), {"ERROR"}, "e42")


def test_cli_exports_match_shared_selection_and_keep_ingestion_snapshot(tmp_path, capsys):
    output = tmp_path / "output"
    database_path = tmp_path / "test.db"
    assert run([FIXTURE], report_dir=output, db_path=database_path, filters=filters()) == 0
    console = capsys.readouterr().out
    assert "Parsed records: 3" in console and "Total errors: 3" in console
    assert "Error groups: 1" in console
    document = json.loads((output / "investigation.json").read_text(encoding="utf-8"))
    view = investigate([analyze_log_file(FIXTURE)], filters())
    expected=json.loads(render_export(view, "json"))
    for key in ('selected','events','groups','filters'):
        assert document[key]==expected[key]
    assert document['status']['label']=='No rule alerts'
    assert document['schema_version']==3 and document['storage']['source_revision_ids']
    rows = list(csv.DictReader(StringIO((output / "investigation.csv").read_text(encoding="utf-8"))))
    assert len(rows) == 3
    assert document["groups"][0]["count"] == 3
    assert len(document["groups"][0]["example_event_ids"]) == 2
    assert len({row["event_id"] for row in rows}) == 3
    for path in (output / "investigation.txt", output / "investigation_report.txt"):
        report = path.read_text(encoding="utf-8")
        assert "Selected events: 3" in report
        assert "Total errors: 3" in report
        assert "code=E43" not in report
        assert "CRITICAL ->" not in report
    assert (output / "investigation.csv.metadata.json").exists()
    assert len(database.fetch_all_entries(database_path)) == 7
    assert "unfiltered source snapshot" in console


def test_real_cli_filters_from_another_directory(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    outcome = subprocess.run([
        sys.executable, "-B", str(ROOT / "main.py"), str(FIXTURE),
        "--start", "2026-10-15T17:30:01+05:30", "--end", "2026-10-15T12:00:02Z",
        "--severity", "ERROR", "--contains", "e42", "--export", "json",
        "--report-dir", str(tmp_path / "output"), "--database", str(tmp_path / "test.db"),
    ], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert outcome.returncode == 0, outcome.stderr
    assert "Parsed records: 3" in outcome.stdout
    assert (tmp_path / "output/investigation.json").exists()
    assert not (tmp_path / "output/investigation.csv").exists()
    assert not (tmp_path / "output/investigation.txt").exists()
    assert (tmp_path / "output/investigation_report.txt").exists()


@pytest.mark.parametrize("args", [
    ["--start", "2026-10-15T12:00:00"], ["--end", "bad"],
    ["--start", "2026-10-16T12:00:00Z", "--end", "2026-10-15T12:00:00Z"],
    ["--severity", "WARN"], ["--export", "xml"],
])
def test_cli_invalid_filters_fail_before_any_write(tmp_path, args):
    with pytest.raises(SystemExit) as error:
        cli([str(FIXTURE), *args, "--report-dir", str(tmp_path / "output"), "--database", str(tmp_path / "test.db")])
    assert error.value.code == 2
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "test.db").exists()


@pytest.mark.parametrize("filename", ["investigation.txt", "investigation.csv", "investigation.json", "investigation.csv.metadata.json"])
def test_all_combined_destinations_are_preflighted_against_inputs(tmp_path, filename):
    source = tmp_path / filename
    source.write_bytes(b"2026-10-15T12:00:00Z INFO original")
    before = source.read_bytes()
    assert run([source], report_dir=tmp_path, db_path=tmp_path / "test.db") == 1
    assert source.read_bytes() == before
    assert not (tmp_path / "test.db").exists()
    assert len(list(tmp_path.iterdir())) == 1


def test_exports_cannot_overwrite_database(tmp_path):
    db_path = tmp_path / "investigation.json"
    assert run([FIXTURE], report_dir=tmp_path, db_path=db_path) == 1
    assert not db_path.exists()
    assert not (tmp_path / "investigation_report.txt").exists()


def test_empty_filter_is_successful_and_does_not_empty_ingestion_database(tmp_path, capsys):
    assert run([FIXTURE], report_dir=tmp_path / "output", db_path=tmp_path / "test.db", filters=FilterSpec(text="no match")) == 0
    console = capsys.readouterr().out
    assert "Status: No matches:" in console
    document = json.loads((tmp_path / "output/investigation.json").read_text(encoding="utf-8"))
    assert document["events"] == [] and document["groups"] == []
    assert document["selected"]["event_count"] == 0
    assert len(database.fetch_all_entries(tmp_path / "test.db")) == 7


def test_filtering_does_not_hide_ingestion_failures_or_reset_previous_snapshot(tmp_path):
    db_path = tmp_path / "test.db"
    database.replace_entries([analyze_log_file(FIXTURE)], db_path)
    previous = database.fetch_all_entries(db_path)
    assert run([FIXTURE, tmp_path / "missing.log"], report_dir=tmp_path / "output", db_path=db_path, filters=FilterSpec(text="no match")) == 1
    document = json.loads((tmp_path / "output/investigation.json").read_text(encoding="utf-8"))
    assert document["status"]["label"] == "Failed"
    assert not document["events"]
    assert database.fetch_all_entries(db_path) == previous


def test_input_order_does_not_change_exported_event_order(tmp_path):
    first = tmp_path / "a.log"
    second = tmp_path / "b.log"
    first.write_bytes(b"2026-10-15T12:00:00Z ERROR same")
    second.write_bytes(b"2026-10-15T12:00:00Z ERROR same")
    a, b = analyze_log_file(first), analyze_log_file(second)
    assert render_export(investigate([a, b]), "json") == render_export(investigate([b, a]), "json")
