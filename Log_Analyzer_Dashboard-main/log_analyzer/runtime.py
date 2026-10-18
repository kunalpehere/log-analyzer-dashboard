"""Source-checkout compatibility plus writable defaults for an installed wheel."""
from pathlib import Path


def source_directory():
    root = Path(__file__).resolve().parents[1]
    return root if (root/'DEVELOPMENT_PLAN.md').is_file() and (root/'logs').is_dir() else None


def output_directory():
    return source_directory() or Path.cwd()


def sample_directory():
    source = source_directory()
    return source/'logs' if source else Path(__file__).resolve().parent/'samples'

