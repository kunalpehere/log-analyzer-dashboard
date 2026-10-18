"""Immutable event contracts and explicit physical/logical accounting."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(str, Enum):
    TEXT = "text"
    JSONL = "jsonl"


class NaivePolicy(str, Enum):
    ASSUME_UTC = "utc"
    REJECT = "reject"


@dataclass(frozen=True)
class ScanOptions:
    format: LogFormat = LogFormat.TEXT
    naive_policy: NaivePolicy = NaivePolicy.ASSUME_UTC
    tracebacks: bool = True
    max_event_lines: int = 200
    max_event_chars: int = 65536

    def __post_init__(self):
        object.__setattr__(self, "format", LogFormat(self.format))
        object.__setattr__(self, "naive_policy", NaivePolicy(self.naive_policy))
        if type(self.tracebacks) is not bool:
            raise ValueError("tracebacks must be a boolean")
        for name in ("max_event_lines", "max_event_chars"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_OPTIONS = ScanOptions()


@dataclass(frozen=True)
class ParsedRecord:
    timestamp: datetime
    level: Severity
    message: str
    original_timestamp: str
    timestamp_assumed: bool


@dataclass(frozen=True)
class ParseOutcome:
    record: ParsedRecord | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    level: Severity
    message: str
    source: str
    start_line: int
    end_line: int
    raw_text: str
    original_timestamp: str
    timestamp_assumed: bool

    @property
    def nonblank_lines(self) -> int:
        return self.end_line - self.start_line + 1

    def as_row(self) -> dict:
        return {
            "source": self.source, "line_number": self.start_line,
            "start_line": self.start_line, "end_line": self.end_line,
            "timestamp_utc": self.timestamp.isoformat(),
            "original_timestamp": self.original_timestamp,
            "timestamp_assumed": self.timestamp_assumed,
            "date": self.timestamp.date().isoformat(),
            "time": self.timestamp.time().isoformat(),
            "level": self.level.value, "message": self.message, "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class ParseIssue:
    source: str
    line_number: int
    reason: str
    raw_text: str
    end_line: int

    @property
    def nonblank_lines(self) -> int:
        return self.end_line - self.line_number + 1


@dataclass(frozen=True)
class BlankLine:
    line_number: int


class ScanState(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass
class ScanResult:
    source: str
    state: ScanState = ScanState.EMPTY
    options: ScanOptions = DEFAULT_OPTIONS
    physical_lines: int = 0
    total_lines: int = 0
    counts: dict[Severity, int] = field(default_factory=lambda: {level: 0 for level in Severity})
    events: list[Event] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    failure: str | None = None

    @property
    def parsed_count(self) -> int:
        return sum(self.counts.values())

    @property
    def rejected_count(self) -> int:
        return sum(issue.nonblank_lines for issue in self.issues)

    @property
    def continuation_lines(self) -> int:
        return sum(event.nonblank_lines - 1 for event in self.events)

    @property
    def error_count(self) -> int:
        return self.counts[Severity.ERROR] + self.counts[Severity.CRITICAL]

    @property
    def warning_count(self) -> int:
        return self.counts[Severity.WARNING]

    @property
    def entries(self) -> list[dict]:
        return [event.as_row() for event in self.events]

    @property
    def error_log(self) -> list[dict]:
        return [event.as_row() for event in self.events if event.level in (Severity.ERROR, Severity.CRITICAL)]

    @property
    def warning_log(self) -> list[dict]:
        return [event.as_row() for event in self.events if event.level == Severity.WARNING]
