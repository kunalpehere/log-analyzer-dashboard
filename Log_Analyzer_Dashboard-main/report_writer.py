"""Compatibility text report entry point using the shared investigation/export pipeline."""

from pathlib import Path
from typing import Iterable

from log_analyzer.exporting import write_export
from log_analyzer.filtering import FilterSpec
from log_analyzer.investigation import investigate
from log_analyzer.models import ScanResult
from log_analyzer.paths import validate_output_path


def write_report(result: ScanResult, output_path: str | Path, *, protected_inputs: Iterable[str | Path] = (), filters: FilterSpec = FilterSpec()) -> Path:
    return write_export(investigate([result], filters), output_path, "text", protected_inputs=protected_inputs)
