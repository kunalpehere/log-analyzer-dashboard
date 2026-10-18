from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from analyzer import ScanState, analyze_lines, analyze_log_file, analyze_upload
from parser import Severity

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name,expected", [
    ("app", (20, 7, 4)), ("server", (15, 5, 4)),
    ("database", (13, 5, 3)), ("big_test", (10000, 526, 1476)),
])
def test_shipped_baseline(name, expected):
    result = analyze_log_file(ROOT / "logs" / f"{name}.log")
    assert result.state == ScanState.SUCCESS
    assert (result.total_lines, result.error_count, result.warning_count) == expected
    assert result.parsed_count == result.total_lines
    assert result.rejected_count == 0


def test_explicit_accounting_and_source_lines():
    result = analyze_log_file(ROOT / "tests/fixtures/mixed.log")
    assert result.physical_lines == 10
    assert result.total_lines == 9
    assert result.parsed_count == 5
    assert result.rejected_count == 4
    assert result.total_lines == result.parsed_count + result.rejected_count
    assert result.counts == {level: 1 for level in Severity}
    assert result.error_count == 2
    assert [issue.line_number for issue in result.issues] == [6, 7, 8, 9]
    assert result.issues[0].raw_text == (ROOT / "tests/fixtures/mixed.log").read_bytes().decode("utf-8").splitlines(keepends=True)[5]
    assert result.entries[0]["source"] == result.source
    assert result.error_log[-1]["level"] == "CRITICAL"


@pytest.mark.parametrize("content", [b"", b" \n\t\r\n", b"\xef\xbb\xbf2026-10-15 12:00:00 warn: slow\r\n", b"2026-10-15 12:00:00 INFO ready\rbad\r\n", b"\xff"])
def test_file_upload_parity(tmp_path, content):
    path = tmp_path / "input.log"
    path.write_bytes(content)
    assert analyze_log_file(path) == analyze_upload(content, str(path))


def test_missing_and_empty_are_distinct(tmp_path):
    missing = analyze_log_file(tmp_path / "missing.log")
    empty = tmp_path / "empty.log"
    empty.write_text(" \n", encoding="utf-8")
    assert missing.state == ScanState.FAILED
    assert "FileNotFoundError" in missing.failure
    assert analyze_log_file(empty).state == ScanState.EMPTY


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("read failed"), UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")])
def test_read_errors_are_explicit(error):
    with patch("builtins.open", side_effect=error):
        result = analyze_log_file("input.log")
    assert result.state == ScanState.FAILED
    assert type(error).__name__ in result.failure
    assert result.parsed_count == 0


def test_failure_after_valid_input_discards_partial_totals():
    class BrokenStream(StringIO):
        def readline(self, *args):
            if self.tell():
                raise OSError("midstream failure")
            return super().readline(*args)
    with patch("builtins.open", return_value=BrokenStream("2026-10-15 12:00:00 ERROR failure\n")):
        result = analyze_log_file("broken.log")
    assert result.state == ScanState.FAILED
    assert result.total_lines == result.parsed_count == 0
    assert not result.entries


def test_real_invalid_utf8_is_failed(tmp_path):
    path = tmp_path / "invalid.log"
    path.write_bytes(b"2026-10-15 12:00:00 ERROR failure\n" + b"a" * 10000 + b"\xff")
    result = analyze_log_file(path)
    assert result.state == ScanState.FAILED
    assert result.parsed_count == 0


def test_programming_errors_are_not_swallowed():
    with pytest.raises(TypeError):
        analyze_upload(None, "invalid")
    with pytest.raises(TypeError):
        analyze_lines([None], "invalid")
