import pytest

from parser import Severity, parse_line


@pytest.mark.parametrize("level,expected", [
    ("INFO", Severity.INFO), ("debug", Severity.DEBUG), ("warning:", Severity.WARNING),
    ("WaRn", Severity.WARNING), ("ERROR:", Severity.ERROR),
    ("critical", Severity.CRITICAL), ("fatal:", Severity.CRITICAL),
])
def test_canonical_severities(level, expected):
    result = parse_line(f"2024-02-29 23:59:59 {level} message  with spacing")
    assert result.reason is None
    assert result.record.level == expected
    assert result.record.message == "message  with spacing"
    assert result.record.timestamp.isoformat() == "2024-02-29T23:59:59+00:00"


@pytest.mark.parametrize("line,reason", [
    ("", "nonempty message"), ("bad line", "nonempty message"),
    ("2026-10-15 10:00:00 ERROR", "nonempty message"),
    ("2026-10-15 10:00:00 ERROR    ", "nonempty message"),
    ("garbage text ERROR fabricated", "timestamp"),
    ("2026-2-01 10:00:00 ERROR bad", "timestamp"),
    ("2026-10-15 1:00:00 ERROR bad", "timestamp"),
    ("2026-02-29 12:00:00 ERROR bad", "Invalid calendar"),
    ("2026-02-30 12:00:00 ERROR bad", "Invalid calendar"),
    ("2026-10-15 24:00:00 ERROR bad", "Invalid calendar"),
    ("2026-10-15 12:60:00 ERROR bad", "Invalid calendar"),
    ("2026-10-15 12:00:60 ERROR bad", "Invalid calendar"),
    ("2026-10-15 12:00:00 NOTICE bad", "Unknown severity"),
    ("2026-10-15 12:00:00 :ERROR: bad", "Unknown severity"),
])
def test_rejection_reasons(line, reason):
    result = parse_line(line)
    assert result.record is None
    assert reason in result.reason
