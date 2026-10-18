import os
from pathlib import Path

import pytest

from analyzer import analyze_lines, analyze_log_file
from report_writer import validate_output_path, write_report
from status_policy import assess_results


def scan(errors=0, warnings=0):
    lines = ["2026-10-15 12:00:00 INFO ready"]
    lines += ["2026-10-15 12:00:01 ERROR failure"] * errors
    lines += ["2026-10-15 12:00:02 WARNING slow"] * warnings
    return analyze_lines(lines, "synthetic.log")


@pytest.mark.parametrize("errors,label", [(0, "No errors observed"), (1, "Minor"), (3, "Minor"), (4, "Minor"), (5, "Minor"), (6, "Moderate"), (10, "Moderate"), (11, "Critical")])
def test_status_and_report_share_boundaries(tmp_path, errors, label):
    result = scan(errors)
    status = assess_results([result])
    assert status.label == label
    output = write_report(result, tmp_path / f"report-{errors}.txt")
    assert f"{status.label}: {status.message}" in output.read_text(encoding="utf-8")


def test_unavailable_evidence_cannot_look_healthy(tmp_path):
    good = scan()
    empty = analyze_lines([], "empty")
    rejected = analyze_lines(["bad"], "bad")
    failed = analyze_log_file(tmp_path / "missing.log")
    assert assess_results([]).label == "Empty"
    assert assess_results([empty]).label == "Empty"
    assert assess_results([rejected]).label == "Uninterpretable"
    assert assess_results([good, rejected]).label == "Incomplete"
    assert assess_results([good, empty]).label == "Incomplete"
    assert assess_results([good, failed]).label == "Failed"
    assert assess_results([scan(warnings=1)]).label == "Warnings"


@pytest.mark.parametrize("suffix", [".txt", ".log"])
def test_input_overwrite_blocked(tmp_path, suffix):
    path = tmp_path / f"input{suffix}"
    path.write_text("2026-10-15 12:00:00 INFO original", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="overwrite input"):
        write_report(analyze_log_file(path), path.parent / "." / path.name)
    assert path.read_bytes() == before


def test_all_inputs_protected(tmp_path):
    other = tmp_path / "other.log"
    other.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError):
        write_report(scan(), other, protected_inputs=[other])
    assert other.read_text() == "original"


def test_hardlink_cannot_overwrite_input(tmp_path):
    source = tmp_path / "source.log"
    source.write_text("original", encoding="utf-8")
    destination = tmp_path / "report.txt"
    try:
        os.link(source, destination)
    except OSError as error:
        pytest.skip(f"Hardlinks unavailable: {error}")
    with pytest.raises(ValueError):
        validate_output_path(destination, [source])
    assert source.read_text() == "original"


def test_report_unicode_rejections_and_explicit_destination(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = analyze_lines(["2026-10-15 12:00:00 ERROR café  failure", "bad input"], "input.txt")
    output = write_report(result, tmp_path / "reports/nested/report.txt")
    text = output.read_text(encoding="utf-8")
    assert "café  failure" in text
    assert "line 2:" in text
    assert "bad input" in text
    assert "Rejected lines: 1" in text
    assert "Incomplete:" in text
    assert not (tmp_path / "input.txt").exists()


def test_failed_report_explains_read_failure(tmp_path):
    result = analyze_log_file(tmp_path / "missing.log")
    text = write_report(result, tmp_path / "failed.txt").read_text(encoding="utf-8")
    assert "Failed:" in text and "FileNotFoundError" in text


def test_report_write_failure_is_not_hidden(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("no write access")
    monkeypatch.setattr(Path, "write_text", denied)
    with pytest.raises(PermissionError):
        write_report(scan(), tmp_path / "report.txt")
