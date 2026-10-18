"""Bounded assembly of a conservative single Python traceback format."""

from dataclasses import replace
import re
from typing import Iterable, Iterator

from log_analyzer.models import BlankLine, Event, LogFormat, ParseIssue, ScanOptions, Severity
from log_analyzer.parsers import get_parser

_MARKER = "Traceback (most recent call last):"
# Custom exceptions are supported when the terminal line has a colon.
_TERMINAL = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::.*)?")


def iter_records(lines: Iterable[str], source: str, options: ScanOptions) -> Iterator[Event | ParseIssue | BlankLine]:
    parser = get_parser(options.format)
    pending: Event | None = None
    active = False

    def unfinished(event: Event, tracing: bool):
        if tracing:
            return ParseIssue(source, event.start_line, "Incomplete traceback: missing terminal exception", event.raw_text, event.end_line)
        return event

    for number, raw in enumerate(lines, start=1):
        text = raw.rstrip("\r\n")
        if not text.strip():
            if pending is not None:
                yield unfinished(pending, active)
                pending, active = None, False
            yield BlankLine(number)
            continue
        outcome = parser.parse(raw, options)
        if outcome.record is not None:
            if pending is not None:
                yield unfinished(pending, active)
            record = outcome.record
            pending = Event(record.timestamp, record.level, record.message, source, number, number, raw, record.original_timestamp, record.timestamp_assumed)
            active = False
            if len(raw) > options.max_event_chars:
                yield ParseIssue(source, number, "Event character limit exceeded", raw, number)
                pending = None
            elif options.format == LogFormat.JSONL or not options.tracebacks:
                yield pending
                pending = None
            else:
                active = pending.level in (Severity.ERROR, Severity.CRITICAL) and pending.message == _MARKER
            continue

        marker = text == _MARKER
        terminal = bool(_TERMINAL.fullmatch(text)) and (":" in text or text.endswith(("Error", "Exception", "Exit", "Interrupt")))
        continuation = (marker and not active) or (active and (text[:1].isspace() or terminal))
        if pending is not None and pending.level in (Severity.ERROR, Severity.CRITICAL) and continuation:
            if pending.nonblank_lines + 1 > options.max_event_lines or len(pending.raw_text) + len(raw) > options.max_event_chars:
                yield ParseIssue(source, pending.start_line, "Traceback event limit exceeded; event rejected", pending.raw_text, pending.end_line)
                yield ParseIssue(source, number, "Traceback event limit exceeded; overflow line rejected", raw, number)
                pending, active = None, False
                continue
            pending = replace(pending, end_line=number, raw_text=pending.raw_text + raw, message=pending.message + "\n" + text)
            active = True
            if terminal:
                yield pending
                pending, active = None, False
            continue

        if pending is not None:
            yield unfinished(pending, active)
            pending, active = None, False
        if options.format == LogFormat.TEXT and (marker or text[:1].isspace() or terminal):
            reason = "Orphan or unsupported traceback continuation"
        else:
            reason = outcome.reason or "Invalid record"
        yield ParseIssue(source, number, reason, raw, number)

    if pending is not None:
        yield unfinished(pending, active)
