"""Explicit text parser. Traceback assembly is handled by ingestion."""

from log_analyzer.models import ParseOutcome, ParsedRecord, ScanOptions
from log_analyzer.parsers.common import parse_severity, parse_timestamp


class TextParser:
    def parse(self, line: str, options: ScanOptions) -> ParseOutcome:
        # A timestamp occupies two tokens with a space, one with T.
        parts = line.strip().split(maxsplit=3)
        if parts and "T" in parts[0] and parts[0][:1].isdigit():
            iso_parts = line.strip().split(maxsplit=2)
            if len(iso_parts) != 3:
                return ParseOutcome(reason="Expected timestamp, level, and a nonempty message")
            stamp, level, message = iso_parts
        else:
            if len(parts) != 4:
                return ParseOutcome(reason="Expected date, time, level, and a nonempty message")
            date, time, level, message = parts
            stamp = f"{date} {time}"
        try:
            timestamp, assumed = parse_timestamp(stamp, options)
            severity = parse_severity(level)
        except ValueError as error:
            return ParseOutcome(reason=str(error))
        return ParseOutcome(record=ParsedRecord(timestamp, severity, message, stamp, assumed))
