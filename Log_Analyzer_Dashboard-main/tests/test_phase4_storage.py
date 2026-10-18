from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sqlite3

import pytest

from log_analyzer.analysis import analyze_lines, analyze_log_file, analyze_upload
from log_analyzer.filtering import FilterSpec, parse_filter_time
from log_analyzer.investigation import investigate
from log_analyzer.limits import ResourceLimits, ResourceLimitError
from log_analyzer.models import ScanOptions, ScanState
from log_analyzer.storage import Store, ImportFailure
from log_analyzer.streams import SourceInput

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/investigation.log'


def source(raw,name='input.log',identity=None):
    @contextmanager
    def opened(): yield BytesIO(raw)
    return SourceInput(name,identity or name,opened)


def count(store,table):
    with store.connection() as conn: return conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]


def test_batches_all_levels_repetitions_and_revision_reuse(tmp_path,monkeypatch):
    store=Store(tmp_path/'test.db',ResourceLimits(batch_size=2))
    inputs=[SourceInput.file(FIXTURE)]
    first=store.import_sources(inputs)
    assert first.peak_event_batch==2 and first.peak_issue_batch==0
    view=store.view(first.source_ids)
    assert view.event_count==9 and view.error_count==6 and view.warning_count==1
    assert view.group_count==3 and len(view.page_events())==9
    import log_analyzer.storage as module
    def forbidden(*args,**kwargs): raise AssertionError('Unchanged input must not be reparsed')
    monkeypatch.setattr(module,'iter_records',forbidden)
    again=store.import_sources(inputs,run_id=first.run_id)
    assert again.reused==1 and again.source_ids==first.source_ids
    assert count(store,'stored_events')==9 and count(store,'source_revisions')==1
    assert store.view(first.source_ids).id==view.id


def test_changed_source_retains_prior_revision_and_pinned_views(tmp_path):
    store=Store(tmp_path/'test.db')
    first=store.import_sources([source(b'2026-10-15T12:00:00Z ERROR old')])
    old=store.view(first.source_ids)
    changed=store.import_sources([source(b'2026-10-15T12:00:00Z ERROR new')],run_id=first.run_id)
    assert changed.source_ids!=first.source_ids and changed.reused==0
    assert old.page_events()[0]['message']=='old'
    assert store.view(changed.source_ids).page_events()[0]['message']=='new'
    assert store.run_sources(first.run_id)==changed.source_ids
    assert count(store,'source_revisions')==2 and count(store,'stored_events')==2


def test_same_bytes_different_identity_keeps_distinct_sources(tmp_path):
    store=Store(tmp_path/'test.db')
    raw=b'2026-10-15T12:00:00Z ERROR repeated\n'*2
    result=store.import_sources([source(raw,'a'),source(raw,'b')])
    assert store.view(result.source_ids).event_count==4
    assert store.view(result.source_ids).page_groups()[0]['count']==4


def test_parser_options_create_new_revision(tmp_path):
    store=Store(tmp_path/'test.db')
    raw=b'2026-10-15 12:00:00 INFO ready'
    first=store.import_sources([source(raw)])
    second=store.import_sources([source(raw)],ScanOptions(naive_policy='reject'),run_id=first.run_id)
    assert second.source_ids!=first.source_ids and not second.published
    assert store.view(first.source_ids).event_count==1
    assert store.view(second.source_ids).event_count==0


def test_read_failure_rolls_back_entire_request_and_preserves_previous_snapshot(tmp_path):
    store=Store(tmp_path/'test.db')
    first=store.import_sources([source(b'2026-10-15T12:00:00Z ERROR old')])
    before=count(store,'stored_events')
    with pytest.raises(ImportFailure,match='FileNotFoundError'):
        store.import_sources([source(b'2026-10-15T12:00:00Z ERROR new'),SourceInput.file(tmp_path/'missing')],run_id=first.run_id)
    assert count(store,'stored_events')==before and count(store,'source_revisions')==1
    assert store.run_sources(first.run_id)==first.source_ids
    with store.connection() as conn: assert conn.execute('SELECT message FROM log_entries').fetchone()[0]=='old'


def test_mutation_between_hashing_and_parsing_is_rejected(tmp_path):
    store=Store(tmp_path/'test.db'); calls=[]
    @contextmanager
    def opened():
        calls.append(1)
        yield BytesIO(b'2026-10-15T12:00:00Z ERROR '+(b'old' if len(calls)==1 else b'new'))
    with pytest.raises(ImportFailure,match='changed between'):
        store.import_sources([SourceInput('changing','changing',opened)])
    assert count(store,'analysis_runs')==count(store,'stored_events')==0


def test_injected_batch_failure_rolls_back_staged_rows(tmp_path,monkeypatch):
    store=Store(tmp_path/'test.db',ResourceLimits(batch_size=2))
    original=store._flush
    def fail(conn,events,issues):
        original(conn,events,issues)
        raise sqlite3.OperationalError('injected failure')
    monkeypatch.setattr(store,'_flush',fail)
    with pytest.raises(ImportFailure,match='injected failure'):
        store.import_sources([SourceInput.file(FIXTURE)])
    assert count(store,'stored_events')==count(store,'source_revisions')==count(store,'analysis_runs')==0


@pytest.mark.parametrize('kwargs,raw,reason',[
    ({'max_source_bytes':10},b'2026-10-15T12:00:00Z INFO ready','byte limit'),
    ({'max_line_chars':10},b'2026-10-15T12:00:00Z INFO ready','line character'),
    ({'max_records':1},b'2026-10-15T12:00:00Z INFO ready\n2026-10-15T12:00:00Z INFO ready','record limit'),
    ({'max_physical_lines':1},b'\n\n','Physical line'),
])
def test_resource_failures_rollback(tmp_path,kwargs,raw,reason):
    store=Store(tmp_path/'test.db',ResourceLimits(**kwargs))
    with pytest.raises(ImportFailure,match=reason): store.import_sources([source(raw)])
    assert count(store,'stored_events')==count(store,'source_revisions')==0


def test_exact_byte_line_and_record_limits(tmp_path):
    raw='2026-10-15T12:00:00Z INFO café\r\n'.encode('utf-8')
    limits=ResourceLimits(max_source_bytes=len(raw),max_line_chars=len(raw.decode('utf-8')),max_records=1,max_physical_lines=1)
    store=Store(tmp_path/'test.db',limits)
    imported=store.import_sources([source(raw)])
    assert store.view(imported.source_ids).page_events()[0]['raw_text']==raw.decode('utf-8')
    assert store.source(imported.source_ids[0]).byte_count==len(raw)


def test_invalid_utf8_and_long_traceback_do_not_publish_partial_input(tmp_path):
    store=Store(tmp_path/'test.db')
    with pytest.raises(ImportFailure,match='UnicodeDecodeError'):
        store.import_sources([source(b'2026-10-15T12:00:00Z ERROR valid\n\xff')])
    assert count(store,'stored_events')==0
    raw=b'2026-10-15T12:00:00Z ERROR failed\nTraceback (most recent call last):\n  frame\nValueError: bad\n'
    imported=store.import_sources([source(raw)],ScanOptions(max_event_lines=2))
    view=store.view(imported.source_ids)
    assert view.event_count==0 and view.sources[0].rejected_count==4


def test_issues_are_persisted_and_paginated_not_retained_as_full_python_list(tmp_path):
    store=Store(tmp_path/'test.db',ResourceLimits(batch_size=3,preview_size=2))
    imported=store.import_sources([source(b'bad\n'*17)])
    assert imported.peak_issue_batch==3
    view=store.view(imported.source_ids)
    assert view.sources[0].rejected_count==17
    first=list(view.iter_issues(imported.source_ids[0],size=2))
    second=list(view.iter_issues(imported.source_ids[0],size=2,offset=2))
    assert [row['line_number'] for row in first+second]==[1,2,3,4]
    assert len(list(view.iter_issues(imported.source_ids[0])))==17


@pytest.mark.parametrize('size,offset',[(0,0),(201,0),(True,0),(2,-1),(2,False)])
def test_page_bounds(tmp_path,size,offset):
    store=Store(tmp_path/'test.db')
    view=store.view(store.import_sources([SourceInput.file(FIXTURE)]).source_ids)
    with pytest.raises(ValueError): view.page_events(size,offset)
    with pytest.raises(ValueError): view.page_groups(size,offset)


def test_full_pagination_matches_in_memory_selection_and_groups(tmp_path):
    store=Store(tmp_path/'test.db',ResourceLimits(batch_size=2))
    imported=store.import_sources([SourceInput.file(FIXTURE)])
    filters=FilterSpec(start=parse_filter_time('2026-10-15T12:00:01Z'),end=parse_filter_time('2026-10-15T12:00:02Z'),severities={'ERROR'},text='e42')
    view=store.view(imported.source_ids,filters)
    expected=investigate([analyze_log_file(FIXTURE)],filters)
    rows=view.page_events(2)+view.page_events(2,2)
    assert rows==expected.event_rows
    group=view.page_groups()[0]
    assert group['count']==3 and group['group_id']==expected.groups[0].signature_id
    assert len(view.examples(group['group_key']))==2
    assert list(view.iter_member_ids(group['group_key']))==[row['event_id'] for row in rows]


def test_signature_placeholder_collision_is_not_merged_in_sql(tmp_path):
    store=Store(tmp_path/'test.db')
    raw=b'2026-10-15T12:00:00Z ERROR request_id=aaaaaaaaaaaaaaaa\n2026-10-15T12:00:00Z ERROR request_id=<hex-id>'
    view=store.view(store.import_sources([source(raw)]).source_ids)
    assert view.group_count==2
    assert sum(group['count'] for group in view.page_groups())==2


def test_upload_stream_never_uses_getvalue_or_unbounded_reads(tmp_path):
    class Checked(BytesIO):
        def getvalue(self): raise AssertionError('Whole upload copy')
        def read(self,size=-1):
            assert 0<=size<=65536
            return super().read(size)
    stream=Checked(FIXTURE.read_bytes())
    store=Store(tmp_path/'test.db')
    imported=store.import_sources([SourceInput.upload(stream,'upload.log')])
    assert store.view(imported.source_ids).event_count==9
    assert not stream.closed


def test_locked_import_fails_without_losing_completed_results(tmp_path):
    store=Store(tmp_path/'test.db',timeout=.01)
    first=store.import_sources([SourceInput.file(FIXTURE)])
    with sqlite3.connect(store.path) as locked:
        locked.execute('BEGIN IMMEDIATE')
        with pytest.raises(ImportFailure,match='locked'):
            store.import_sources([source(b'2026-10-15T12:00:00Z ERROR changed')],run_id=first.run_id)
        locked.rollback()
    assert store.view(first.source_ids).event_count==9
    assert count(store,'source_revisions')==1


def test_selection_build_failure_rolls_back_cache(tmp_path):
    store=Store(tmp_path/'test.db'); imported=store.import_sources([SourceInput.file(FIXTURE)])
    with store.connection() as conn:
        conn.execute("CREATE TRIGGER fail_selection BEFORE INSERT ON selected_events BEGIN SELECT RAISE(ABORT,'injected selection error'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError): store.view(imported.source_ids)
    assert count(store,'selections')==0 and count(store,'selected_events')==0
    with store.connection() as conn: conn.execute('DROP TRIGGER fail_selection'); conn.commit()
    assert store.view(imported.source_ids).event_count==9


def test_legacy_migration_and_future_version_rejection(tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE log_entries(id INTEGER PRIMARY KEY,file TEXT,date TEXT,time TEXT,level TEXT,message TEXT)')
        conn.execute("INSERT INTO log_entries VALUES (1,'old','2020-01-01','12:00:00','ERROR','original')")
    store=Store(path); Store(path)
    with store.connection() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0]==1
        row=conn.execute('SELECT message,timestamp_utc FROM log_entries').fetchone()
        assert tuple(row)==('original',None)
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]==1
        conn.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError,match='future schema'): Store(path)


def test_migration_failure_rolls_back_ddl_and_preserves_legacy(tmp_path,monkeypatch):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE log_entries(id INTEGER PRIMARY KEY,file TEXT,date TEXT,time TEXT,level TEXT,message TEXT)')
        conn.execute("INSERT INTO log_entries VALUES (1,'old','2020-01-01','12:00:00','ERROR','original')")
    import log_analyzer.storage as module
    monkeypatch.setattr(module,'_SCHEMA',module._SCHEMA+'; INVALID SQL;')
    with pytest.raises(sqlite3.Error): Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0]==0
        assert 'timestamp_utc' not in {row[1] for row in conn.execute('PRAGMA table_info(log_entries)')}
        assert conn.execute('SELECT message FROM log_entries').fetchone()[0]=='original'


def test_compatibility_limits_fail_explicitly_instead_of_truncating():
    limits=ResourceLimits(max_compat_events=1)
    raw=b'2026-10-15T12:00:00Z INFO first\n2026-10-15T12:00:01Z INFO second'
    result=analyze_upload(raw,'input',limits=limits)
    assert result.state==ScanState.FAILED and result.parsed_count==0
    assert 'Compatibility result limit' in result.failure


@pytest.mark.parametrize('kwargs',[{'batch_size':0},{'batch_size':1001},{'max_page_size':1001},{'max_source_bytes':True}])
def test_invalid_resource_limits(kwargs):
    with pytest.raises(ValueError): ResourceLimits(**kwargs)


def test_empty_revision_preserves_exact_published_revision_set(tmp_path):
    store=Store(tmp_path/'test.db')
    first=store.import_sources([source(b'2026-10-15T12:00:00Z ERROR old')])
    assert store.published_sources()==first.source_ids
    empty=store.import_sources([source(b'')],run_id=first.run_id)
    assert not empty.published and store.run_sources(first.run_id)==empty.source_ids
    assert store.published_sources()==first.source_ids
    assert store.view(store.published_sources()).event_count==1
    assert len(store.list_runs(1))==1


def test_changed_ingestion_policy_rechecks_and_rolls_back_when_stricter(tmp_path):
    path=tmp_path/'test.db'
    store=Store(path)
    first=store.import_sources([SourceInput.file(FIXTURE)])
    strict=Store(path,ResourceLimits(max_records=5))
    with pytest.raises(ImportFailure,match='record limit'):
        strict.import_sources([SourceInput.file(FIXTURE)],run_id=first.run_id)
    assert strict.run_sources(first.run_id)==first.source_ids
    assert strict.view(first.source_ids).event_count==9


def test_initial_legacy_snapshot_is_archived_before_later_publication(tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE log_entries(id INTEGER PRIMARY KEY,file TEXT,date TEXT,time TEXT,level TEXT,message TEXT)')
        conn.execute("INSERT INTO log_entries VALUES (1,'old','2020-01-01','12:00:00','ERROR','legacy evidence')")
    store=Store(path)
    store.import_sources([source(b'2026-10-15T12:00:00Z ERROR new')])
    assert store.page_legacy_archive()[0]['message_preview']=='legacy evidence'
    with store.connection() as conn:
        assert conn.execute('SELECT timestamp_utc FROM legacy_log_entries_archive').fetchone()[0] is None
        assert conn.execute('SELECT message FROM log_entries').fetchone()[0]=='new'
    Store(path)
    assert len(store.page_legacy_archive())==1


def test_source_pages_keep_combined_selection_ids_and_history_is_bounded(tmp_path):
    store=Store(tmp_path/'test.db')
    result=store.import_sources([
        source(b'2026-10-15T12:00:00Z INFO a\n2026-10-15T12:00:02Z INFO a2','a'),
        source(b'2026-10-15T12:00:01Z INFO b\n2026-10-15T12:00:03Z INFO b2','b')])
    view=store.view(result.source_ids)
    assert [row['event_id'] for row in view.page_events(source_id=result.source_ids[0])]==['event-000001','event-000003']
    assert [row['event_id'] for row in view.page_events(source_id=result.source_ids[1])]==['event-000002','event-000004']
    assert len(store.source_history(result.run_id,size=1))==1
    with pytest.raises(ValueError): view.page_events(source_id='outside')


def test_reusing_run_under_stricter_source_limit_is_rejected_without_loss(tmp_path):
    path=tmp_path/'test.db'
    store=Store(path)
    raw=b'2026-10-15T12:00:00Z INFO ready'
    imported=store.import_sources([source(raw,'a'),source(raw,'b')])
    strict=Store(path,ResourceLimits(max_sources=1))
    with pytest.raises(ImportFailure,match='source count limit'):
        strict.import_sources([source(raw,'a')],run_id=imported.run_id)
    with pytest.raises(ResourceLimitError): strict.run_sources(imported.run_id)
    assert store.run_sources(imported.run_id)==imported.source_ids
