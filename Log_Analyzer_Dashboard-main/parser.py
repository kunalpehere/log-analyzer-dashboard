"""Compatibility entry point for the shared text parser."""

from log_analyzer.models import DEFAULT_OPTIONS, ParseOutcome, ParsedRecord, ScanOptions, Severity
from log_analyzer.parsers.text import TextParser


def parse_line(line: str, options: ScanOptions = DEFAULT_OPTIONS) -> ParseOutcome:
    return TextParser().parse(line, options)


__all__ = ["ParseOutcome", "ParsedRecord", "Severity", "parse_line"]
