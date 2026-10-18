"""Validated inclusive UTC filters and deterministic, duplicate-preserving sorting."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .models import Event, ScanOptions, Severity
from .limits import DEFAULT_LIMITS, ResourceLimitError
from .parsers.common import parse_timestamp


def parse_filter_time(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    return parse_timestamp(value.strip(), ScanOptions(naive_policy="reject"))[0]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Filter times must be timezone-aware datetimes")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError("Filter time is outside the UTC calendar range") from error


@dataclass(frozen=True)
class FilterSpec:
    start: datetime | None = None
    end: datetime | None = None
    severities: frozenset[Severity] | None = None
    text: str = ""
    case_sensitive: bool = False

    def __post_init__(self):
        object.__setattr__(self, "start", _utc(self.start))
        object.__setattr__(self, "end", _utc(self.end))
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("Start time must be at or before end time")
        if self.severities is not None:
            if isinstance(self.severities, (str, bytes)):
                raise ValueError("Severities must be a collection of canonical levels")
            try:
                object.__setattr__(self, "severities", frozenset(Severity(value) for value in self.severities))
            except (ValueError, TypeError) as error:
                raise ValueError("Severities must contain canonical levels") from error
        if not isinstance(self.text, str):
            raise ValueError("Message text filter must be a string")
        if type(self.case_sensitive) is not bool:
            raise ValueError("case_sensitive must be a boolean")

    def as_dict(self) -> dict:
        return {
            "start_utc": self.start.isoformat() if self.start else None,
            "end_utc": self.end.isoformat() if self.end else None,
            "time_boundaries": "inclusive",
            "severities": sorted(level.value for level in self.severities) if self.severities is not None else None,
            "message_contains": self.text, "case_sensitive": self.case_sensitive,
            "text_matching": "literal substring in decoded message",
        }


def event_sort_key(event: Event) -> tuple:
    return (event.timestamp, event.source, event.start_line, event.end_line,
            event.level.value, event.message, event.raw_text, event.original_timestamp, event.timestamp_assumed)


def filter_events(events: Iterable[Event], filters: FilterSpec = FilterSpec()) -> tuple[Event, ...]:
    needle = filters.text if filters.case_sensitive else filters.text.casefold()
    def matches(event: Event) -> bool:
        return (
            (filters.start is None or event.timestamp >= filters.start)
            and (filters.end is None or event.timestamp <= filters.end)
            and (filters.severities is None or event.level in filters.severities)
            and needle in (event.message if filters.case_sensitive else event.message.casefold())
        )
    selected=[]; chars=0
    for event in events:
        if not matches(event): continue
        chars+=len(event.raw_text)+len(event.message)
        if len(selected)>=DEFAULT_LIMITS.max_compat_events or chars>DEFAULT_LIMITS.max_compat_chars:
            raise ResourceLimitError("Materialized selection limit exceeded; use Store.view")
        selected.append(event)
    return tuple(sorted(selected,key=event_sort_key))
