"""Shared preflight protection for every export destination."""

from pathlib import Path
from typing import Iterable


def validate_output_path(output_path: str | Path, input_paths: Iterable[str | Path]) -> Path:
    destination = Path(output_path).resolve()
    for input_path in input_paths:
        source = Path(input_path).resolve()
        if destination == source or (destination.exists() and source.exists() and destination.samefile(source)):
            raise ValueError(f"Output would overwrite input: {source}")
    return destination
