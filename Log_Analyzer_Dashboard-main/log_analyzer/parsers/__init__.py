"""A parser protocol with explicit selection; no format guessing."""

from typing import Protocol

from log_analyzer.models import LogFormat, ParseOutcome, ScanOptions
from .jsonl import JsonLinesParser
from .text import TextParser


class LineParser(Protocol):
    def parse(self, line: str, options: ScanOptions) -> ParseOutcome: ...


def get_parser(format: LogFormat) -> LineParser:
    return {LogFormat.TEXT: TextParser, LogFormat.JSONL: JsonLinesParser}[format]()
