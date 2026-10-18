"""Explicit resource budgets for imports, compatibility helpers, and presentation."""
from dataclasses import dataclass, asdict

class ResourceLimitError(ValueError):
    pass

@dataclass(frozen=True)
class ResourceLimits:
    max_source_bytes: int = 50 * 1024 * 1024
    max_line_chars: int = 65536
    max_records: int = 250000
    max_physical_lines: int = 500000
    max_sources: int = 32
    batch_size: int = 250
    preview_size: int = 100
    max_page_size: int = 200
    max_event_chars: int = 262144
    max_event_lines: int = 1000
    max_download_bytes: int = 5 * 1024 * 1024
    max_export_bytes: int = 200 * 1024 * 1024
    max_compat_events: int = 20000
    max_compat_chars: int = 8 * 1024 * 1024

    def __post_init__(self):
        for name,value in asdict(self).items():
            if type(value) is not int or value < 1 or value > 2**31-1:
                raise ValueError(f"{name} must be a positive integer no larger than 2**31-1")
        if self.batch_size > 1000 or self.max_page_size > 1000 or self.preview_size > 1000:
            raise ValueError("Batch, page, and preview sizes must be at most 1000")
        if self.preview_size > self.max_page_size:
            raise ValueError("Preview size cannot exceed maximum page size")

    def import_policy(self):
        return {name:getattr(self,name) for name in ('max_source_bytes','max_line_chars','max_records','max_physical_lines','max_event_chars','max_event_lines')}

DEFAULT_LIMITS=ResourceLimits()
