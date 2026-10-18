"""SQLite snapshot storage with UTC timestamps and original event evidence."""

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Iterable

from log_analyzer.models import ScanResult
from log_analyzer.limits import DEFAULT_LIMITS, ResourceLimitError

from log_analyzer.runtime import output_directory

DB_NAME = output_directory() / "logs.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT, date TEXT, time TEXT, level TEXT, message TEXT
)
"""
# Additive compatibility upgrade only. Legacy rows keep NULL evidence fields.
_EVIDENCE_COLUMNS = {
    "timestamp_utc": "TEXT", "original_timestamp": "TEXT", "timestamp_assumed": "INTEGER",
    "start_line": "INTEGER", "end_line": "INTEGER", "raw_text": "TEXT",
}
_INSERT = """INSERT INTO log_entries
(file, date, time, level, message, timestamp_utc, original_timestamp, timestamp_assumed, start_line, end_line, raw_text)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(log_entries)")}
    for name, kind in _EVIDENCE_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE log_entries ADD COLUMN {name} {kind}")


def create_table(db_path: str | Path = DB_NAME) -> None:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("BEGIN")
        _ensure_schema(conn)


def _insert(conn: sqlite3.Connection, file_name: str, entries: Iterable[dict]) -> None:
    conn.executemany(_INSERT, (
        (str(file_name), entry["date"], entry["time"], entry["level"], entry["message"],
         *(entry.get(name) for name in _EVIDENCE_COLUMNS))
        for entry in entries
    ))


def insert_entries(file_name: str, error_log: list[dict], warning_log: list[dict], db_path: str | Path = DB_NAME) -> None:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("BEGIN")
        _ensure_schema(conn)
        _insert(conn, file_name, error_log)
        _insert(conn, file_name, warning_log)


def _bounded_rows(cursor):
    rows=[]; chars=0
    for row in cursor:
        chars+=sum(len(value) for value in row if isinstance(value,str))
        if len(rows)>=DEFAULT_LIMITS.max_compat_events or chars>DEFAULT_LIMITS.max_compat_chars:
            raise ResourceLimitError("Legacy read limit exceeded; use paginated Store queries")
        rows.append(row)
    return rows


def fetch_all_entries(db_path: str | Path = DB_NAME) -> list[tuple]:
    """Retain the existing five-field read contract for older callers."""
    with closing(sqlite3.connect(db_path)) as conn:
        return _bounded_rows(conn.execute("SELECT file, date, time, level, message FROM log_entries ORDER BY date, time, id"))


def fetch_event_evidence(db_path: str | Path = DB_NAME) -> list[dict]:
    """Read stored evidence, without changing or relabeling legacy rows."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in _bounded_rows(conn.execute("SELECT *, file AS source FROM log_entries ORDER BY date, time, id"))]


def clear_table(db_path: str | Path = DB_NAME) -> None:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM log_entries")


def replace_entries(results: Iterable[ScanResult], db_path: str | Path = DB_NAME) -> None:
    """Replace the snapshot and upgrade its evidence columns in one transaction."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("BEGIN")
        _ensure_schema(conn)
        conn.execute("DELETE FROM log_entries")
        for result in results:
            _insert(conn, result.source, result.error_log)
            _insert(conn, result.source, result.warning_log)
