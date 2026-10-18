"""Incremental exports of revision-pinned database selections; bounded Python buffers."""
from dataclasses import asdict
import csv
from io import StringIO
import json
from pathlib import Path
import uuid
import os

from .exporting import CSV_COLUMNS, csv_safe_cell
from .grouping import SIGNATURE_VERSION
from .limits import ResourceLimitError
from .diagnostics import DEFAULT_DIAGNOSTICS, summary, iter_buckets, iter_alerts, assess_status
from .paths import validate_output_path

class StreamArray:
    def __init__(self,values): self.values=values

def json_chunks(value):
    if isinstance(value,dict):
        yield '{'
        for index,key in enumerate(sorted(value)):
            if index: yield ','
            yield json.dumps(key,ensure_ascii=False)+':'
            yield from json_chunks(value[key])
        yield '}'
    elif isinstance(value,(StreamArray,list,tuple)):
        yield '['
        for index,item in enumerate(value.values if isinstance(value,StreamArray) else value):
            if index: yield ','
            yield from json_chunks(item)
        yield ']'
    else: yield json.dumps(value,ensure_ascii=False,allow_nan=False)

def group_documents(view):
    for group in view.iter_groups():
        key=group.pop('group_key')
        group['signature_tokens']=json.loads(key)[-1]
        group['event_ids']=StreamArray(view.iter_member_ids(key))
        group['example_event_ids']=[row['event_id'] for row in view.examples(key)]
        yield group

def metadata(view, diagnostics=DEFAULT_DIAGNOSTICS):
    findings=summary(view, diagnostics)
    return {
        'diagnostics': dict(findings, buckets=StreamArray(iter_buckets(view, diagnostics)), alerts=StreamArray(iter_alerts(view, diagnostics))),
        'schema_version':3,'filters':view.filters.as_dict(),'signature_version':SIGNATURE_VERSION,
        'storage':{'schema_version':1,'selection_id':view.id,'source_revision_ids':[source.id for source in view.sources]},
        'event_order':'UTC timestamp, source, start/end line, severity, message, raw text, original timestamp, assumption flag',
        'event_id_scope':'one revision-pinned investigation selection; genuine duplicates have distinct IDs',
        'selected':{'event_count':view.event_count,'error_count':view.error_count,'warning_count':view.warning_count,
                    'continuation_lines':view.continuation_lines,'nonblank_evidence_lines':view.event_count+view.continuation_lines,
                    'counts':{level.value:count for level,count in view.counts.items()}},
        'status':asdict(assess_status(view,diagnostics,findings)),
        'ingestion_quality_unfiltered':[
            {'source':source.source,'source_revision_id':source.id,'run_id':source.run_id,'identity':source.identity,
             'sha256':source.sha256,'byte_count':source.byte_count,'state':source.state.value,'failure':None,
             'options':source.options,'physical_lines':source.stats['physical_lines'],'nonblank_lines':source.stats['total_lines'],
             'parsed_events':source.parsed_count,'continuation_lines':source.stats['continuation_lines'],
             'rejected_lines':source.rejected_count,'issues':StreamArray(view.iter_issues(source.id))}
            for source in sorted(view.sources,key=lambda item:(item.source,item.id))],
        'csv_safety':{'formula_prefix':'apostrophe','escaped_columns_field':'csv_escaped_fields',
                      'original_values':'JSON and text preserve originals; remove exactly one leading apostrophe from CSV fields listed in csv_escaped_fields'},
        'groups':StreamArray(group_documents(view))}

def text_chunks(view, diagnostics=DEFAULT_DIAGNOSTICS):
    yield 'LOG INVESTIGATION REPORT\n'
    yield f'Selected events: {view.event_count}\nParsed records: {view.event_count}\nContinuation lines: {view.continuation_lines}\n'
    yield f'Total errors: {view.error_count}\nTotal warnings: {view.warning_count}\nNormalized timezone: UTC\n'
    yield 'Filters: '+json.dumps(view.filters.as_dict(),ensure_ascii=False,sort_keys=True)+'\n'
    for source in sorted(view.sources,key=lambda item:(item.source,item.id)):
        yield f"Source: {source.source}; revision: {source.id}; SHA-256: {source.sha256}\n"
        yield f"Format: {source.options['format']}; naive timestamp policy: {source.options['naive_policy']}\n"
        yield f"Source ingestion (unfiltered): {source.parsed_count} events; Rejected lines: {source.rejected_count}\n"
        for issue in view.iter_issues(source.id):
            yield f"line {issue['line_number']}: through line {issue['end_line']}; {issue['reason']} | {issue['raw_text']}\n"
    yield '---ERROR GROUPS---\n'
    for group in view.iter_groups():
        yield f"Group: {group['group_id']}; {group['level']}; count: {group['count']}\nSignature: {group['signature']}\n"
        yield f"First: {group['first_seen_utc']}; last: {group['last_seen_utc']}\nMembers: "
        for index,event_id in enumerate(view.iter_member_ids(group['group_key'])):
            yield (', ' if index else '')+event_id
        yield '\nExamples: '+', '.join(row['event_id'] for row in view.examples(group['group_key']))+'\n'
    yield '---EVIDENCE-BASED DIAGNOSTICS---\n'
    yield json.dumps(summary(view, diagnostics), ensure_ascii=False, sort_keys=True)+'\n'
    for bucket in iter_buckets(view, diagnostics):
        yield 'Bucket: '+json.dumps(bucket, ensure_ascii=False, sort_keys=True)+'\n'
    for alert in iter_alerts(view, diagnostics):
        yield 'Alert: '+json.dumps(alert, ensure_ascii=False, sort_keys=True)+'\n'
    yield '---SELECTED EVENT EVIDENCE---\n'
    for row in view.iter_events():
        yield f"Event: {row['event_id']}; group: {row['group_id'] or 'none'}; source: {row['source']}\n"
        yield f"line {row['start_line']}: through line {row['end_line']}; {row['timestamp_utc']} {row['level']} -> {row['message']}\n"
        yield f"Original timestamp: {row['original_timestamp']}; assumed UTC: {row['timestamp_assumed']}\n"
        yield '---BEGIN RAW---\n'; yield row['raw_text']; yield '\n---END RAW---\n'
    status=assess_status(view,diagnostics)
    yield f'---STATUS---\n{status.label}: {status.message}\n'

def csv_chunks(view):
    buffer=StringIO(newline='')
    writer=csv.DictWriter(buffer,fieldnames=CSV_COLUMNS,quoting=csv.QUOTE_ALL,lineterminator='\r\n')
    writer.writeheader(); yield buffer.getvalue(); buffer.seek(0); buffer.truncate()
    for event in view.iter_csv_events():
        row={key:event.get(key,'') for key in CSV_COLUMNS}; escaped=[]
        for key in CSV_COLUMNS[:-1]:
            row[key],changed=csv_safe_cell(row[key])
            if changed: escaped.append(key)
        row['csv_escaped_fields']=json.dumps(escaped,ensure_ascii=False)
        writer.writerow(row); yield buffer.getvalue(); buffer.seek(0); buffer.truncate()

def export_chunks(view,format,diagnostics=DEFAULT_DIAGNOSTICS):
    if format=='text': yield from text_chunks(view,diagnostics)
    elif format=='csv': yield from csv_chunks(view)
    elif format in ('json','metadata'):
        document=metadata(view,diagnostics)
        if format=='json': document['events']=StreamArray(view.iter_events())
        yield from json_chunks(document); yield '\n'
    else: raise ValueError('Unsupported export format: '+str(format))

def write_stored_export(view,path,format,*,protected_inputs=(),diagnostics=DEFAULT_DIAGNOSTICS):
    if format not in ('text','csv','json','metadata'): raise ValueError('Unsupported export format')
    inputs=[source.source for source in view.sources]+[view.store.path,*(str(view.store.path)+suffix for suffix in ('-journal','-wal','-shm')),*protected_inputs]
    destination=validate_output_path(path,inputs)
    targets=[(destination,format)]
    if format=='csv':
        targets.append((validate_output_path(str(destination)+'.metadata.json',[*inputs,destination]),'metadata'))
    staged=[]
    try:
        for target,kind in targets:
            target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_name('.export-'+uuid.uuid4().hex[:12]+'.tmp')
            with temp.open('x',encoding='utf-8',newline='') as stream:
                staged.append((temp,target))
                count=0
                for chunk in export_chunks(view,kind,diagnostics):
                    count+=len(chunk.encode('utf-8'))
                    if count>view.store.limits.max_export_bytes: raise ResourceLimitError('Export byte limit exceeded')
                    stream.write(chunk)
        for temp,target in staged: os.replace(temp,target)
    finally:
        for temp,target in staged: temp.unlink(missing_ok=True)
    return destination
