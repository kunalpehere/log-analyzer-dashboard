"""One analysis path for file, upload, and iterable inputs."""

from io import StringIO
from pathlib import Path
from typing import Iterable

from .ingestion import iter_records
from .limits import DEFAULT_LIMITS, ResourceLimitError
from .models import BlankLine, DEFAULT_OPTIONS, Event, ScanOptions, ScanResult, ScanState


def analyze_lines(lines: Iterable[str], source: str, options: ScanOptions = DEFAULT_OPTIONS, limits=DEFAULT_LIMITS) -> ScanResult:
    if options.max_event_chars>limits.max_event_chars or options.max_event_lines>limits.max_event_lines:
        raise ResourceLimitError("Traceback options exceed compatibility hard limits")
    result = ScanResult(source, options=options)
    def checked_lines():
        size=0
        for number,line in enumerate(lines,1):
            if len(line)>limits.max_line_chars or number>limits.max_physical_lines:
                raise ResourceLimitError("Compatibility physical line limit exceeded; use Store for large inputs")
            size+=len(line.encode('utf-8'))
            if size>limits.max_source_bytes: raise ResourceLimitError("Source byte limit exceeded")
            yield line
    retained=0
    for item in iter_records(checked_lines(), source, options):
        if isinstance(item, BlankLine):
            result.physical_lines += 1
            continue
        retained+=len(item.raw_text)+(len(item.message) if isinstance(item,Event) else 0)
        if len(result.events)+len(result.issues)>=min(limits.max_compat_events,limits.max_records) or retained>limits.max_compat_chars:
            raise ResourceLimitError("Compatibility result limit exceeded; use database-backed Store")
        count = item.nonblank_lines
        result.physical_lines += count
        result.total_lines += count
        if isinstance(item, Event):
            result.counts[item.level] += 1
            result.events.append(item)
        else:
            result.issues.append(item)
    result.state = ScanState.SUCCESS if result.total_lines else ScanState.EMPTY
    return result


def analyze_log_file(file_name: str | Path, options: ScanOptions = DEFAULT_OPTIONS, limits=DEFAULT_LIMITS) -> ScanResult:
    source = str(file_name)
    try:
        with open(file_name, "r", encoding="utf-8-sig", newline="") as stream:
            def bounded():
                while True:
                    line=stream.readline(limits.max_line_chars+1)
                    if not line: break
                    yield line
            return analyze_lines(bounded(), source, options, limits)
    except (OSError, UnicodeError, ResourceLimitError) as error:
        return ScanResult(source, state=ScanState.FAILED, options=options, failure=f"{type(error).__name__}: {error}")


def analyze_upload(content: bytes, source: str, options: ScanOptions = DEFAULT_OPTIONS, limits=DEFAULT_LIMITS) -> ScanResult:
    from io import BytesIO, TextIOWrapper
    if not isinstance(content,bytes): raise TypeError("Upload content must be bytes")
    try:
        if len(content)>limits.max_source_bytes: raise ResourceLimitError("Source byte limit exceeded")
        with TextIOWrapper(BytesIO(content),encoding='utf-8-sig',newline='') as stream:
            return analyze_lines(stream,source,options,limits)
    except (OSError,UnicodeError,ResourceLimitError) as error:
        return ScanResult(source,state=ScanState.FAILED,options=options,failure=f"{type(error).__name__}: {error}")
