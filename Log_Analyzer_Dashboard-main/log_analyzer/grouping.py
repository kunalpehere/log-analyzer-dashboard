"""Conservative structured signatures; no arbitrary number/code replacement."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Sequence

from .models import Event, Severity
from .limits import DEFAULT_LIMITS, ResourceLimitError

SIGNATURE_VERSION = "labeled-ids-v1"
_LABELED_ID = re.compile(
    r"(?P<label>\b(?:request[_-]?id|correlation[_-]?id|trace[_-]?id)\s*[:=]\s*)"
    r"(?P<value>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,64})"
    r"(?![A-Za-z0-9_-])", re.IGNORECASE,
)


def signature_tokens(message: str) -> tuple[tuple[str, str], ...]:
    parts = []
    cursor = 0
    for match in _LABELED_ID.finditer(message):
        parts.append(("literal", message[cursor:match.start("value")]))
        value = match["value"]
        parts.append(("id", "uuid" if "-" in value else "hex"))
        cursor = match.end("value")
    parts.append(("literal", message[cursor:]))
    return tuple(parts)


def signature_details(level, message):
    """One bounded event's structured signature, shared with disk-backed imports."""
    tokens=signature_tokens(message)
    encoded=json.dumps([SIGNATURE_VERSION,level.value,tokens],ensure_ascii=False,separators=(",", ":"))
    identifier=hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    display="".join(value if kind=="literal" else f"<{value}-id>" for kind,value in tokens)
    return tokens,identifier,display


@dataclass(frozen=True)
class ErrorGroup:
    level: Severity
    signature: str
    signature_id: str
    tokens: tuple[tuple[str, str], ...]
    member_indices: tuple[int, ...]
    example_indices: tuple[int, ...]
    first_seen: datetime
    last_seen: datetime

    @property
    def count(self) -> int:
        return len(self.member_indices)


def group_errors(events: Sequence[Event]) -> tuple[ErrorGroup, ...]:
    if len(events)>DEFAULT_LIMITS.max_compat_events: raise ResourceLimitError("Materialized group input limit exceeded; use Store.view")
    buckets = {}
    chars=0
    for index, event in enumerate(events):
        chars+=len(event.message)
        if chars>DEFAULT_LIMITS.max_compat_chars: raise ResourceLimitError("Materialized signature budget exceeded; use Store.view")
        if event.level not in (Severity.ERROR, Severity.CRITICAL):
            continue
        key = (event.level, signature_tokens(event.message))
        buckets.setdefault(key, []).append(index)
    groups = []
    for (level, tokens), indices in buckets.items():
        # Dictionary grouping uses structured tokens, never their display or digest.
        encoded = json.dumps([SIGNATURE_VERSION, level.value, tokens], ensure_ascii=False, separators=(",", ":"))
        identifier = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        display = "".join(value if kind == "literal" else f"<{value}-id>" for kind, value in tokens)
        examples, seen = [], set()
        ordered = sorted(indices, key=lambda index: (events[index].timestamp, index))
        for index in ordered:
            if events[index].message not in seen:
                examples.append(index)
                seen.add(events[index].message)
            if len(examples) == 3:
                break
        groups.append(ErrorGroup(level, display, identifier, tokens, tuple(ordered), tuple(examples),
                                 events[ordered[0]].timestamp, events[ordered[-1]].timestamp))
    return tuple(sorted(groups, key=lambda group: (-group.count, group.first_seen, group.level.value, group.signature, group.tokens)))
