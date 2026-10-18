"""Streamlit presentation using the same validated scans as the CLI."""

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Sequence

from log_analyzer.runtime import output_directory, sample_directory
from log_analyzer.models import ScanOptions, ScanResult, Severity
from log_analyzer.exporting import render_export, render_metadata
from log_analyzer.filtering import FilterSpec, parse_filter_time
from log_analyzer.investigation import investigate
from status_policy import assess_investigation
from log_analyzer.diagnostics import DiagnosticOptions, DEFAULT_DIAGNOSTICS, summary, iter_buckets, iter_alerts, assess_status
from log_analyzer.storage import Store, ImportFailure
from log_analyzer.streams import SourceInput
from log_analyzer.stored_export import write_stored_export
import sqlite3
import json
import uuid
import math

BASE_DIR = output_directory()
DEFAULT_LOG_FILES = tuple(sample_directory() / name for name in ("app.log", "server.log", "database.log"))


def render_results(st, results: Sequence[ScanResult], filters: FilterSpec = FilterSpec()) -> None:
    view = investigate(results, filters)
    event_rows = view.event_rows
    st.subheader("Selected event summary")
    metrics = [
        ("Selected evidence lines", sum(event.nonblank_lines for event in view.events)),
        ("Selected events", len(view.events)),
        ("Source rejected lines", sum(result.rejected_count for result in results)),
        ("Errors (ERROR + CRITICAL)", view.error_count),
        ("Warnings", view.warning_count),
        ("Sources", len(results)),
    ]
    for column, (label, value) in zip(st.columns(len(metrics)), metrics):
        column.metric(label, value)
    status = assess_investigation(view)
    st.subheader("Analysis status")
    getattr(st, status.tone)(f"{status.label}: {status.message}")
    st.caption(f"UTC timestamps; {view.continuation_lines} traceback continuation lines. Counts describe logical events. Failed reads discard partial results.")

    st.caption("Tables, charts, groups, and downloads use the same selected events. Source rejection counts and ingestion quality remain unfiltered.")
    st.subheader("Per-file selection and source ingestion quality")
    for result in results:
        source_view = investigate([result], filters)
        with st.expander(result.source):
            st.write(f"Format: {result.options.format.value}; naive timestamp policy: {result.options.naive_policy.value}; UTC display; continuation lines: {result.continuation_lines}")
            st.write(f"Scan state: {result.state.value}; physical lines: {result.physical_lines}; nonblank: {result.total_lines}; parsed: {result.parsed_count}; rejected: {result.rejected_count}")
            source_status = assess_investigation(source_view)
            getattr(st, source_status.tone)(f"{source_status.label}: {source_status.message}")
            if result.failure:
                st.error(result.failure)
            st.dataframe([{"Level": level.value, "Count": source_view.counts[level]} for level in Severity], hide_index=True)
            if result.issues:
                st.dataframe([asdict(issue) for issue in result.issues], hide_index=True)
            source_rows = [row for row in event_rows if row["source"] == result.source]
            if source_rows:
                st.write("Selected event evidence (UTC timestamps, original text, and source spans)")
                st.dataframe(source_rows, hide_index=True)

    counts = view.counts
    if sum(counts.values()):
        import plotly.express as px
        st.subheader("Visual analysis")
        distribution = [{"Level": level.value, "Count": count} for level, count in counts.items() if count]
        st.plotly_chart(px.pie(distribution, names="Level", values="Count", hole=0.5, title="Validated severity distribution"), width="stretch")
        per_file = [
            {"File": result.source, "Count": count, "Type": kind}
            for result in results
            for kind, count in (("Errors", sum(event.level in (Severity.ERROR, Severity.CRITICAL) for event in view.events if event.source == result.source)), ("Warnings", sum(event.level == Severity.WARNING for event in view.events if event.source == result.source)))
        ]
        st.plotly_chart(px.bar(per_file, x="File", y="Count", color="Type", barmode="group", title="Errors and warnings per file"), width="stretch")
        timeline = [
            {"Timestamp": datetime.fromisoformat(entry["timestamp_utc"]), "File": entry["source"], "Message": entry["message"]}
            for entry in event_rows if entry["level"] in ("ERROR", "CRITICAL")
        ]
        if timeline:
            timeline.sort(key=lambda entry: entry["Timestamp"])
            st.plotly_chart(px.scatter(timeline, x="Timestamp", y="File", color="File", hover_data=["Message"], title="Error timeline (UTC)"), width="stretch")

    st.subheader("Recurring error groups")
    st.caption("Counts describe selected ERROR/CRITICAL events. Grouping preserves exception types and error codes and does not establish a root cause.")
    if view.groups:
        st.dataframe(view.group_rows, hide_index=True)
        for group in view.groups:
            with st.expander(f"{group.count} {group.level.value} events â€” {group.signature_id[:12]}"):
                st.code(group.signature, language=None)
                st.write("Representative original messages and source evidence")
                st.dataframe([event_rows[index] for index in group.example_indices], hide_index=True)
                st.write("All selected group members (repeated source events retained)")
                st.dataframe([event_rows[index] for index in group.member_indices], hide_index=True)
    else:
        st.info("No error-level events in the current selection.")
    st.subheader("Download selected investigation")
    for format, suffix, mime in [("text", "txt", "text/plain"), ("csv", "csv", "text/csv"), ("json", "json", "application/json")]:
        st.download_button(f"Download {format.upper()}", render_export(view, format).encode("utf-8"),
                           file_name=f"investigation.{suffix}", mime=mime, key=f"export_{format}")
    st.download_button("Download CSV metadata", render_metadata(view).encode("utf-8"),
                       file_name="investigation.csv.metadata.json", mime="application/json", key="export_csv_metadata")
    st.caption("CSV uses documented reversible apostrophe escaping for formula-like cells. Keep its metadata file; JSON preserves original values.")

    for title, entries in [
        ("Error details", [entry for entry in event_rows if entry["level"] in ("ERROR", "CRITICAL")]),
        ("Warning details", [entry for entry in event_rows if entry["level"] == "WARNING"]),
    ]:
        st.subheader(title)
        if entries:
            st.dataframe(entries, hide_index=True)
        else:
            st.info("No matching records in the parsed input.")


def render_stored(st,view,diagnostics=DEFAULT_DIAGNOSTICS):
    limits=view.store.limits
    st.subheader("Selected event summary")
    metrics=[("Selected evidence lines",view.event_count+view.continuation_lines),("Selected events",view.event_count),
             ("Source rejected lines",sum(source.rejected_count for source in view.sources)),
             ("Errors (ERROR + CRITICAL)",view.error_count),("Warnings",view.warning_count),("Sources",len(view.sources))]
    for column,(label,value) in zip(st.columns(6),metrics): column.metric(label,value)
    status=assess_status(view,diagnostics); getattr(st,status.tone)(f"{status.label}: {status.message}")
    st.caption(f"UTC timestamps; {view.continuation_lines} traceback continuation lines. Tables are paginated; totals cover the full selection.")
    page_sizes=[size for size in (25,50,100,200) if size<=limits.max_page_size] or [limits.max_page_size]
    page_size=st.selectbox("Rows per page",page_sizes,index=page_sizes.index(100) if 100 in page_sizes else len(page_sizes)-1,key='view_page_size')
    for source in sorted(view.sources,key=lambda item:item.source):
        with st.expander(source.source):
            stats=source.stats; sub=view.store.view([source.id],view.filters)
            st.write(f"Format: {source.options['format']}; naive timestamp policy: {source.options['naive_policy']}; revision: {source.id}")
            st.write(f"Unfiltered ingestion: {stats['physical_lines']} physical lines; {source.parsed_count} events; {source.rejected_count} rejected lines")
            state=assess_status(sub,diagnostics); getattr(st,state.tone)(f"{state.label}: {state.message}")
            st.dataframe([{'Level':level.value,'Count':sub.counts[level]} for level in Severity],hide_index=True)
            if source.rejected_count:
                page=st.number_input('Issue page',min_value=1,max_value=max(1,math.ceil(stats['issue_count']/page_size)),value=1,key='issue_'+source.id)
                st.dataframe(list(view.iter_issues(source.id,size=page_size,offset=(page-1)*page_size)),hide_index=True)
            if sub.event_count:
                page=st.number_input('Event page',min_value=1,max_value=max(1,math.ceil(sub.event_count/page_size)),value=1,key='events_'+source.id+'_'+view.id)
                st.dataframe(view.page_events(page_size,(page-1)*page_size,source_id=source.id),hide_index=True)
    if view.event_count:
        import plotly.express as px
        distribution=[{'Level':level.value,'Count':count} for level,count in view.counts.items() if count]
        st.plotly_chart(px.pie(distribution,names='Level',values='Count',hole=.5,title='Validated severity distribution'),width="stretch")
        bars=[]
        for source in view.sources:
            sub=view.store.view([source.id],view.filters)
            bars.extend([{'File':source.source,'Count':sub.error_count,'Type':'Errors'},{'File':source.source,'Count':sub.warning_count,'Type':'Warnings'}])
        st.plotly_chart(px.bar(bars,x='File',y='Count',color='Type',barmode='group'),width="stretch")
        timeline=[row for row in view.page_events(page_size) if row['level'] in ('ERROR','CRITICAL')]
        if timeline: st.plotly_chart(px.scatter(timeline,x='timestamp_utc',y='source',color='source',hover_data=['message'],title='UTC error timeline (bounded selection preview)'),width="stretch")
        st.caption(f"Timeline previews up to {page_size} selected rows; count charts cover all selected events.")
    st.subheader('Recurring error groups')
    st.caption('SQL aggregates cover all selected errors; grouping describes observations, not a proven root cause.')
    if view.group_count:
        page=st.number_input('Group page',min_value=1,max_value=max(1,math.ceil(view.group_count/page_size)),value=1,key='group_page_'+view.id)
        groups=view.page_groups(page_size,(page-1)*page_size)
        st.dataframe([{key:value for key,value in group.items() if key!='group_key'} for group in groups],hide_index=True)
        index=st.selectbox('Inspect group',range(len(groups)),format_func=lambda index:f"{groups[index]['count']} {groups[index]['level']} — {groups[index]['group_id'][:12]}",key='chosen_'+view.id+str(page))
        group=groups[index]
        with st.expander('Original group evidence'):
            st.code(group['signature'],language=None)
            st.dataframe(view.examples(group['group_key']),hide_index=True)
            member_page=st.number_input('Group member page',min_value=1,max_value=max(1,math.ceil(group['count']/page_size)),value=1,key='members_'+view.id+group['group_id'])
            st.dataframe(view.page_events(page_size,(member_page-1)*page_size,group_key=group['group_key']),hide_index=True)
    else: st.info('No error-level events in the current selection.')
    from itertools import islice
    st.subheader('Evidence-based diagnostics')
    findings=summary(view,diagnostics)
    st.write(f"Occupied UTC buckets: {findings['occupied_buckets']}; rule alerts: {findings['alert_count']}")
    st.caption(findings['denominator_definition'])
    st.caption(findings['caveats'])
    st.write({'diagnostic_options':findings['options'],'bucket_origin_utc':findings['bucket_origin_utc'],
              'boundaries':findings['boundaries'],'comparison':findings['comparison']})
    if findings['occupied_buckets']:
        diagnostic_page=st.number_input('Diagnostic bucket page',min_value=1,max_value=max(1,math.ceil(findings['occupied_buckets']/page_size)),value=1,key='buckets_'+view.id+diagnostics.cache_key)
        st.dataframe(list(islice(iter_buckets(view,diagnostics),(diagnostic_page-1)*page_size,diagnostic_page*page_size)),hide_index=True)
    if findings['alert_count']:
        alert_page=st.number_input('Diagnostic alert page',min_value=1,max_value=max(1,math.ceil(findings['alert_count']/page_size)),value=1,key='alerts_'+view.id+diagnostics.cache_key)
        st.write('Observed threshold alerts, labeled hypotheses, and suggested next steps')
        st.dataframe(list(islice(iter_alerts(view,diagnostics),(alert_page-1)*page_size,alert_page*page_size)),hide_index=True)
    else:
        st.info('No configured diagnostic thresholds were met in the selected evidence. This does not establish system health.')
    if findings['ranked_recurring_signatures']:
        st.write('Ranked recurring signatures: counts, first/last UTC observations, and representative event IDs')
        st.dataframe(findings['ranked_recurring_signatures'],hide_index=True)
    st.caption('Event IDs refer to this combined selection; inspect the source and group evidence above or the complete exports. Hypotheses require independent verification.')
    st.subheader('Download selected investigation')
    export_key=view.id+diagnostics.cache_key
    folder=str(view.cache_number) if diagnostics==DEFAULT_DIAGNOSTICS else str(view.cache_number)+'-'+diagnostics.cache_key
    export_dir=view.store.path.parent/'exports'/folder
    ready=st.session_state.get('exports_'+export_key,False)
    if not ready and (view.event_count<=limits.preview_size or st.button('Prepare full streamed exports',key='prepare_'+view.id)):
        try:
            for format,suffix in [('text','txt'),('csv','csv'),('json','json')]: write_stored_export(view,export_dir/('investigation.'+suffix),format,diagnostics=diagnostics)
            st.session_state['exports_'+export_key]=True; ready=True
        except (OSError,ValueError,sqlite3.Error) as error: st.error(f'Export failed: {error}')
    if ready:
        for label,name,mime in [('Download TEXT','investigation.txt','text/plain'),('Download CSV','investigation.csv','text/csv'),('Download JSON','investigation.json','application/json'),('Download CSV metadata','investigation.csv.metadata.json','application/json')]:
            path=export_dir/name
            if path.stat().st_size>limits.max_download_bytes:
                st.info(f'{name} exceeds the browser download limit; the full export is saved at {path}'); continue
            with path.open('rb') as stream: data=stream.read(limits.max_download_bytes+1)
            if len(data)>limits.max_download_bytes: st.error('Download limit exceeded'); continue
            st.download_button(label,data,file_name=name,mime=mime,key='stored_'+name)
    rows=view.page_events(page_size)
    for title,levels in [('Error details',('ERROR','CRITICAL')),('Warning details',('WARNING',))]:
        st.subheader(title)
        matching=[row for row in rows if row['level'] in levels]
        if matching: st.dataframe(matching,hide_index=True)
        else: st.info('No matching records in this preview page; use paginated source/group evidence for the full selection.')


def main() -> None:
    import streamlit as st
    st.set_page_config(page_title="Log Analyzer Dashboard", page_icon=":bar_chart:", layout="wide")
    st.title("LOG ANALYZER DASHBOARD")
    st.write("Inspect validated log records, errors, warnings, and rejected input.")
    st.sidebar.title("Controls")
    format_name = st.sidebar.selectbox("Input format", ["text", "jsonl"])
    naive_policy = st.sidebar.selectbox("Timestamps without a timezone", ["utc", "reject"], format_func=lambda value: {"utc": "Assume UTC", "reject": "Reject"}[value])
    tracebacks = st.sidebar.checkbox("Assemble supported Python tracebacks (text)", value=True)
    max_lines = st.sidebar.number_input("Maximum lines per event", min_value=1, value=200, step=1)
    max_chars = st.sidebar.number_input("Maximum characters per event", min_value=1, value=65536, step=1)
    options = ScanOptions(format_name, naive_policy, tracebacks, int(max_lines), int(max_chars))
    st.sidebar.caption("All timestamps are normalized to UTC. Format selection applies to every input; no autodetection.")
    st.sidebar.subheader("Investigation filters")
    start_text = st.sidebar.text_input("Start time (inclusive, Z or offset)")
    end_text = st.sidebar.text_input("End time (inclusive, Z or offset)")
    levels = st.sidebar.multiselect("Selected severities", [level.value for level in Severity], default=[level.value for level in Severity])
    contains = st.sidebar.text_input("Message contains (literal text)")
    case_sensitive = st.sidebar.checkbox("Case-sensitive message matching", value=False)
    try:
        filters = FilterSpec(parse_filter_time(start_text), parse_filter_time(end_text), frozenset(levels), contains, case_sensitive)
    except ValueError as error:
        st.error(f"Invalid filters: {error}")
        st.stop()
    st.sidebar.subheader('Diagnostic rules')
    bucket_seconds=st.sidebar.number_input('UTC bucket seconds',min_value=1,max_value=86400,value=300,step=1)
    min_errors=st.sidebar.number_input('Minimum bucket errors',min_value=1,max_value=2147483647,value=5,step=1)
    error_rate=st.sidebar.text_input('Error rate threshold (fraction 0..1)',value='0.5')
    top_signatures=st.sidebar.number_input('Top recurring signatures',min_value=1,max_value=100,value=10,step=1)
    try:
        diagnostics=DiagnosticOptions(int(bucket_seconds),int(min_errors),error_rate,int(top_signatures))
    except ValueError as error:
        st.error(f'Invalid diagnostic rules: {error}')
        st.stop()
    uploaded_file = st.sidebar.file_uploader("Choose a log or JSON Lines file", type=["log", "txt", "jsonl", "ndjson"])
    st.sidebar.write("Default files: app.log, server.log, database.log")
    refresh=st.sidebar.button('Refresh sources')
    if '_store_dir' not in st.session_state:
        st.session_state['_store_dir']=str(BASE_DIR/'.runtime-tmp'/('dashboard-'+uuid.uuid4().hex))
    try:
        store=Store(Path(st.session_state['_store_dir'])/'investigations.db')
        if uploaded_file is not None:
            inputs=[SourceInput.upload(uploaded_file,uploaded_file.name)]
            token=(getattr(uploaded_file,'file_id',id(uploaded_file)),getattr(uploaded_file,'size',None))
        else:
            inputs=[SourceInput.file(path) for path in DEFAULT_LOG_FILES]
            token=tuple((str(path),path.stat().st_size,path.stat().st_mtime_ns) for path in DEFAULT_LOG_FILES)
        identities=tuple(source.identity for source in inputs)
        key=(identities,token,json.dumps(asdict(options),sort_keys=True))
        previous=st.session_state.get('_import_key')
        if refresh or previous!=key:
            run_id=st.session_state.get('_run_id') if previous and previous[0]==identities else None
            imported=store.import_sources(inputs,options,run_id=run_id)
            st.session_state['_run_id']=imported.run_id
            st.session_state['_source_ids']=imported.source_ids
            st.session_state['_import_key']=key
        view=store.view(st.session_state['_source_ids'],filters)
        render_stored(st,view,diagnostics)
    except (ImportFailure,OSError,ValueError,sqlite3.Error) as error:
        st.error(f'Failed: {type(error).__name__}: {error}. Previous completed investigations are preserved. Retry with Refresh sources.')


if __name__ == "__main__":
    main()
