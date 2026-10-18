"""Strict timestamps and canonical severity shared by both formats."""

from datetime import datetime, timezone
import re

from log_analyzer.models import NaivePolicy, ScanOptions, Severity

_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?"
)
_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL"}


def parse_timestamp(value: str, options: ScanOptions) -> tuple[datetime, bool]:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Expected timestamp YYYY-MM-DD HH:MM:SS or YYYY-MM-DDTHH:MM:SS with optional fraction and Z/offset")
    offset = re.search(r"([+-])([0-9]{2}):([0-9]{2})$", value)
    if offset and (int(offset[2]) > 23 or int(offset[3]) > 59):
        raise ValueError("Invalid timezone offset")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid calendar date or clock time") from error
    assumed = stamp.tzinfo is None
    if assumed:
        if options.naive_policy == NaivePolicy.REJECT:
            raise ValueError("Naive timestamp rejected: explicit timezone required")
        stamp = stamp.replace(tzinfo=timezone.utc)
    try:
        return stamp.astimezone(timezone.utc), assumed
    except (ValueError, OverflowError) as error:
        raise ValueError("Timestamp outside supported UTC calendar range") from error


def parse_severity(value: str) -> Severity:
    normalized = value.removesuffix(":").upper()
    try:
        return Severity(_ALIASES.get(normalized, normalized))
    except ValueError as error:
        raise ValueError(f"Unknown severity: {normalized}") from error
