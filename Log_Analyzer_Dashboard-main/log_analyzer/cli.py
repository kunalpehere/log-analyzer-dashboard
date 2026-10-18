"""Argument parsing for the local investigation workflow; no import-time actions."""
from pathlib import Path
from .models import ScanOptions, Severity
from .filtering import FilterSpec, parse_filter_time
from .limits import ResourceLimits, DEFAULT_LIMITS
from .diagnostics import DiagnosticOptions
from database import DB_NAME

def cli(argv: list[str] | None = None) -> int:
    from main import run
    import argparse
    parser = argparse.ArgumentParser(description="Investigate text/JSON Lines logs using UTC filters and evidence-linked groups")
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--format", choices=["text", "jsonl"], default="text")
    parser.add_argument("--naive-policy", choices=["utc", "reject"], default="utc")
    parser.add_argument("--no-tracebacks", action="store_true")
    parser.add_argument("--max-event-lines", type=int, default=200)
    parser.add_argument("--max-event-chars", type=int, default=65536)
    parser.add_argument("--start", help="Inclusive lower timestamp with explicit Z/offset")
    parser.add_argument("--end", help="Inclusive upper timestamp with explicit Z/offset")
    parser.add_argument("--severity", choices=[level.value for level in Severity], action="append")
    parser.add_argument("--contains", default="", help="Literal substring in decoded message")
    parser.add_argument("--case-sensitive", action="store_true")
    parser.add_argument("--export", choices=["text", "csv", "json"], action="append", help="Repeat to choose formats; default exports all three")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--database", type=Path, default=DB_NAME)
    parser.add_argument("--run-id", help="Reuse a run; unchanged identities/options/bytes reuse completed revisions")
    parser.add_argument("--max-source-bytes",type=int,default=DEFAULT_LIMITS.max_source_bytes)
    parser.add_argument("--max-line-chars",type=int,default=DEFAULT_LIMITS.max_line_chars)
    parser.add_argument("--max-records",type=int,default=DEFAULT_LIMITS.max_records)
    parser.add_argument("--batch-size",type=int,default=DEFAULT_LIMITS.batch_size)
    parser.add_argument('--bucket-seconds',type=int,default=300,help='UTC epoch-aligned bucket width, 1..86400 seconds')
    parser.add_argument('--min-errors',type=int,default=5,help='Minimum ERROR+CRITICAL events per alert bucket')
    parser.add_argument('--error-rate',default='0.5',help='Exact decimal fraction 0..1; denominator includes all selected accepted events')
    parser.add_argument('--top-signatures',type=int,default=10,help='Ranked recurring signature limit, 1..100')
    parser.add_argument('--fail-on-alert',action='store_true',help='Exit 3 for rule alerts; ingestion/operational failures retain exit 1')
    args = parser.parse_args(argv)
    try:
        diagnostics=DiagnosticOptions(args.bucket_seconds,args.min_errors,args.error_rate,args.top_signatures)
        limits=ResourceLimits(max_source_bytes=args.max_source_bytes,max_line_chars=args.max_line_chars,max_records=args.max_records,batch_size=args.batch_size)
        options = ScanOptions(args.format, args.naive_policy, not args.no_tracebacks, args.max_event_lines, args.max_event_chars)
        filters = FilterSpec(parse_filter_time(args.start), parse_filter_time(args.end),
                             frozenset(args.severity) if args.severity is not None else None,
                             args.contains, args.case_sensitive)
    except ValueError as error:
        parser.error(str(error))
    return run(args.files or None, report_dir=args.report_dir, db_path=args.database, options=options,
               filters=filters, exports=tuple(args.export) if args.export else ("text", "csv", "json"),limits=limits,run_id=args.run_id,diagnostics=diagnostics,fail_on_alert=args.fail_on_alert)
