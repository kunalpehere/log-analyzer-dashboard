"""One selected event set drives metrics, groups, tables, and exports."""

from dataclasses import dataclass
from typing import Sequence

from .filtering import FilterSpec, filter_events
from .grouping import ErrorGroup, group_errors
from .models import Event, ScanResult, Severity


@dataclass(frozen=True)
class Investigation:
    sources: tuple[ScanResult, ...]
    filters: FilterSpec
    events: tuple[Event, ...]
    groups: tuple[ErrorGroup, ...]

    @property
    def counts(self) -> dict[Severity, int]:
        counts = {level: 0 for level in Severity}
        for event in self.events:
            counts[event.level] += 1
        return counts

    @property
    def error_count(self) -> int:
        return self.counts[Severity.ERROR] + self.counts[Severity.CRITICAL]

    @property
    def warning_count(self) -> int:
        return self.counts[Severity.WARNING]

    @property
    def continuation_lines(self) -> int:
        return sum(event.nonblank_lines - 1 for event in self.events)

    @property
    def event_rows(self) -> list[dict]:
        memberships = {index: group.signature_id for group in self.groups for index in group.member_indices}
        return [dict(event.as_row(), event_id=f"event-{index + 1:06d}", group_id=memberships.get(index))
                for index, event in enumerate(self.events)]

    @property
    def group_rows(self) -> list[dict]:
        return [{"group_id": group.signature_id, "level": group.level.value, "signature": group.signature,
                 "count": group.count, "first_seen_utc": group.first_seen.isoformat(),
                 "last_seen_utc": group.last_seen.isoformat()} for group in self.groups]


def investigate(results: Sequence[ScanResult], filters: FilterSpec = FilterSpec()) -> Investigation:
    events = filter_events((event for result in results for event in result.events), filters)
    return Investigation(tuple(results), filters, events, group_errors(events))
