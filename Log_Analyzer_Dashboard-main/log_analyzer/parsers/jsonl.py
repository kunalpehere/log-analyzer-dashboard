"""One object per physical line; required string fields are explicit."""

import json

from log_analyzer.models import ParseOutcome, ParsedRecord, ScanOptions
from log_analyzer.parsers.common import parse_severity, parse_timestamp


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class JsonLinesParser:
    def parse(self, line: str, options: ScanOptions) -> ParseOutcome:
        try:
            data = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (ValueError, RecursionError) as error:
            return ParseOutcome(reason=f"Invalid JSON: {error}")
        if not isinstance(data, dict):
            return ParseOutcome(reason="Expected a JSON object")
        for key in ("timestamp", "level", "message"):
            if key not in data or not isinstance(data[key], str):
                return ParseOutcome(reason=f"Required string field: {key}")
        if not data["message"].strip():
            return ParseOutcome(reason="Expected a nonempty message")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in data["message"]):
            return ParseOutcome(reason="Message contains an unpaired Unicode surrogate")
        if data["level"] != data["level"].strip():
            return ParseOutcome(reason="Severity must not contain surrounding whitespace")
        try:
            timestamp, assumed = parse_timestamp(data["timestamp"], options)
            severity = parse_severity(data["level"])
        except ValueError as error:
            return ParseOutcome(reason=str(error))
        return ParseOutcome(record=ParsedRecord(timestamp, severity, data["message"], data["timestamp"], assumed))
