"""CLI client of shared parsing, investigation, and export pipelines."""

from pathlib import Path
import sqlite3
import json
from typing import Iterable

from log_analyzer.exporting import write_export
from log_analyzer.filtering import FilterSpec
from log_analyzer.investigation import investigate
from log_analyzer.models import DEFAULT_OPTIONS, ScanOptions, ScanState, ScanResult
from log_analyzer.limits import DEFAULT_LIMITS
from log_analyzer.storage import Store, ImportFailure
from log_analyzer.streams import SourceInput
from log_analyzer.stored_export import write_stored_export
from log_analyzer.diagnostics import DEFAULT_DIAGNOSTICS, summary, iter_alerts, assess_status
from log_analyzer.runtime import output_directory, sample_directory
from database import DB_NAME
from report_writer import validate_output_path, write_report

BASE_DIR = output_directory()
DEFAULT_LOG_FILES = tuple(sample_directory() / name for name in ("app.log", "server.log", "database.log", "big_test.log"))


def run(log_files: Iterable[str | Path] | None = None, *, report_dir: str | Path | None = None,
        db_path: str | Path = DB_NAME, options: ScanOptions = DEFAULT_OPTIONS,
        filters: FilterSpec = FilterSpec(), exports: tuple[str, ...] = ("text", "csv", "json"),
        limits=DEFAULT_LIMITS, run_id=None, diagnostics=DEFAULT_DIAGNOSTICS, fail_on_alert=False) -> int:
    from itertools import islice
    sources = [Path(path).resolve() for path in islice(DEFAULT_LOG_FILES if log_files is None else log_files,limits.max_sources+1)]
    if len(sources)>limits.max_sources:
        print("Source count limit exceeded."); return 1
    if not sources:
        print("No input files selected.")
        return 1
    output_dir = Path(report_dir) if report_dir is not None else BASE_DIR / "reports"
    try:
        if isinstance(exports, str) or any(value not in ("text", "csv", "json") for value in exports):
            raise ValueError("Exports must be a collection of text, csv, or json formats")
        formats = tuple(dict.fromkeys(exports))
        database_path = validate_output_path(db_path, sources)
        database_files=[database_path,*(Path(str(database_path)+suffix) for suffix in ('-journal','-wal','-shm'))]
        for database_file in database_files: validate_output_path(database_file,sources)
        reports = [output_dir / f"{path.stem}_report.txt" for path in sources]
        combined = [(format, output_dir / ("investigation." + {"text": "txt", "csv": "csv", "json": "json"}[format])) for format in formats]
        all_outputs = [*reports, *(path for _, path in combined)]
        all_outputs.extend(Path(str(path) + ".metadata.json") for format, path in combined if format == "csv")
        destinations = [validate_output_path(path, [*sources, *database_files]) for path in all_outputs]
        if len(set(destinations)) != len(destinations):
            raise ValueError("Input names produce colliding export destinations")
        for index, destination in enumerate(destinations):
            validate_output_path(destination, destinations[:index])
        store=Store(database_path,limits)
        try:
            imported=store.import_sources([SourceInput.file(path) for path in sources],options,run_id=run_id)
        except ImportFailure as error:
            # No partial imported records are published; emit explicitly failed diagnostics only.
            results=[ScanResult(str(path),state=ScanState.FAILED,options=options,failure=f"Import rolled back: {error}") for path in sources]
            for result,destination in zip(results,reports):
                write_report(result,destination,protected_inputs=[*sources,database_path],filters=filters)
            failure_view=investigate(results,filters)
            for format,destination in combined:
                write_export(failure_view,destination,format,protected_inputs=[*sources,database_path])
            print(f"Import failed: {error}")
            print("Database refresh skipped: import rolled back; previous completed results preserved.")
            return 1
        view=store.view(imported.source_ids,filters)
        for source_id,destination in zip(imported.source_ids,reports):
            source=store.source(source_id)
            write_stored_export(store.view([source_id],filters),destination,'text',protected_inputs=sources,diagnostics=diagnostics)
            print(f"Source ingestion {source.source}: {source.state.value}, {source.parsed_count} parsed source events, {source.rejected_count} rejected lines")
        for format,destination in combined:
            write_stored_export(view,destination,format,protected_inputs=sources,diagnostics=diagnostics)
        status=assess_status(view,diagnostics)
        print(f"Run: {imported.run_id}; reused sources: {imported.reused}; selection: {view.id}")
        print(f"Status: {status.label}: {status.message}")
        print(f"Total lines: {view.event_count+view.continuation_lines} (selected accepted evidence)")
        print(f"Parsed records: {view.event_count}")
        print(f"Continuation lines: {view.continuation_lines}")
        print(f"Rejected lines: {sum(source.rejected_count for source in view.sources)} (unfiltered ingestion quality)")
        print(f"Input format: {options.format.value}; naive timestamp policy: {options.naive_policy.value}; normalized timezone: UTC")
        print(f"Total errors: {view.error_count}")
        print(f"Total warnings: {view.warning_count}")
        print(f"Error groups: {view.group_count}")
        for group in view.page_groups():
            print(f"Group {group['group_id']}: {group['count']} {group['level']} events; {group['first_seen_utc']} through {group['last_seen_utc']}")
        if view.group_count>limits.preview_size: print("Group preview limited; exports contain all groups.")
        diagnostic_summary=summary(view,diagnostics)
        print('Diagnostics: '+json.dumps(diagnostic_summary,ensure_ascii=False,sort_keys=True))
        for alert in islice(iter_alerts(view,diagnostics),limits.preview_size):
            print('Alert: '+json.dumps(alert,ensure_ascii=False,sort_keys=True))
        if diagnostic_summary['alert_count']>limits.preview_size:
            print('Alert preview limited; streamed exports contain all alerts.')
        if not imported.published:
            print("Database refresh skipped: a source is empty or uninterpretable; prior issue snapshot preserved.")
            return 1
        with store.connection() as conn:
            count=conn.execute('SELECT COUNT(*) FROM log_entries').fetchone()[0]
        print(f"Stored DB entries: {count} (unfiltered source snapshot; all severities also retained in revision storage)")
        if any(source.rejected_count for source in view.sources): return 1
        return 3 if fail_on_alert and diagnostic_summary['alert_count'] else 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Analysis failed: {type(error).__name__}: {error}")
        return 1


def cli(argv: list[str] | None = None) -> int:
    from log_analyzer.cli import cli as command
    return command(argv)


if __name__ == "__main__":
    raise SystemExit(cli())
