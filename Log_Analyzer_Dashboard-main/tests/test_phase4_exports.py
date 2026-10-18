import csv
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import sqlite3

import pytest

from log_analyzer.analysis import analyze_log_file
from log_analyzer.exporting import render_export
from log_analyzer.filtering import FilterSpec
from log_analyzer.investigation import investigate
from log_analyzer.limits import ResourceLimits, ResourceLimitError
from log_analyzer.storage import Store
from log_analyzer.streams import SourceInput
from log_analyzer.stored_export import export_chunks, write_stored_export
from main import run

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/investigation.log'


def test_streamed_formats_match_events_groups_filters_and_metadata(tmp_path):
    store=Store(tmp_path/'test.db',ResourceLimits(batch_size=2))
    imported=store.import_sources([SourceInput.file(FIXTURE)])
    filters=FilterSpec(severities={'ERROR'},text='E42')
    view=store.view(imported.source_ids,filters)
    for format,extension in [('text','txt'),('csv','csv'),('json','json')]:
        write_stored_export(view,tmp_path/f'export.{extension}',format)
    document=json.loads((tmp_path/'export.json').read_text(encoding='utf-8'))
    expected=json.loads(render_export(investigate([analyze_log_file(FIXTURE)],filters),'json'))
    for key in ('events','groups','filters','selected'): assert document[key]==expected[key]
    assert document['status']['label']=='No rule alerts'
    assert document['schema_version']==3
    source=document['ingestion_quality_unfiltered'][0]
    assert len(source['sha256'])==64 and source['byte_count']==FIXTURE.stat().st_size
    assert source['source_revision_id']==imported.source_ids[0]
    rows=list(csv.DictReader(StringIO((tmp_path/'export.csv').read_text(encoding='utf-8'))))
    assert [row['event_id'] for row in rows]==[row['event_id'] for row in document['events']]
    manifest=json.loads((tmp_path/'export.csv.metadata.json').read_text(encoding='utf-8'))
    assert manifest=={key:value for key,value in document.items() if key!='events'}
    assert 'Selected events: 4' in (tmp_path/'export.txt').read_text(encoding='utf-8')


def test_export_limits_preserve_previous_file_and_remove_staging(tmp_path):
    store=Store(tmp_path/'test.db',ResourceLimits(max_export_bytes=100))
    view=store.view(store.import_sources([SourceInput.file(FIXTURE)]).source_ids)
    output=tmp_path/'result.json'; output.write_bytes(b'previous completed output')
    with pytest.raises(ResourceLimitError,match='Export byte limit'):
        write_stored_export(view,output,'json')
    assert output.read_bytes()==b'previous completed output'
    assert not list(tmp_path.glob('.export-*.tmp'))


def test_output_failure_preserves_previous_file(tmp_path,monkeypatch):
    store=Store(tmp_path/'test.db'); view=store.view(store.import_sources([SourceInput.file(FIXTURE)]).source_ids)
    output=tmp_path/'result.csv'; output.write_bytes(b'previous')
    import log_analyzer.stored_export as module
    def denied(*args,**kwargs): raise PermissionError('rename denied')
    monkeypatch.setattr(module.os,'replace',denied)
    with pytest.raises(PermissionError): write_stored_export(view,output,'csv')
    assert output.read_bytes()==b'previous'
    assert not list(tmp_path.glob('.export-*.tmp'))


@pytest.mark.parametrize('format',['text','csv','json'])
def test_stored_export_input_and_database_protection(tmp_path,format):
    store=Store(tmp_path/'test.db'); view=store.view(store.import_sources([SourceInput.file(FIXTURE)]).source_ids)
    with pytest.raises(ValueError): write_stored_export(view,store.path,format)
    with pytest.raises(ValueError): write_stored_export(view,FIXTURE,format)


@pytest.mark.parametrize('suffix',['-journal','-wal','-shm'])
def test_database_sidecar_collisions_are_rejected_before_database_creation(tmp_path,suffix):
    database=tmp_path/'test.db'; source=Path(str(database)+suffix)
    source.write_bytes(b'2026-10-15T12:00:00Z INFO original')
    assert run([source],db_path=database,report_dir=tmp_path/'output')==1
    assert source.read_bytes().endswith(b'original')
    assert not database.exists()


def test_repeated_run_cli_reuses_revision_and_retains_filtered_snapshot(tmp_path,capsys):
    database=tmp_path/'test.db'
    assert run([FIXTURE],db_path=database,report_dir=tmp_path/'output',run_id='repeat')==0
    capsys.readouterr()
    assert run([FIXTURE],db_path=database,report_dir=tmp_path/'output',run_id='repeat',filters=FilterSpec(text='absent'))==0
    assert 'reused sources: 1' in capsys.readouterr().out
    store=Store(database)
    with store.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM stored_events').fetchone()[0]==9
        assert conn.execute('SELECT COUNT(*) FROM log_entries').fetchone()[0]==7
    doc=json.loads((tmp_path/'output/investigation.json').read_text(encoding='utf-8'))
    assert doc['selected']['event_count']==0 and doc['status']['label']=='No matches'


def test_export_chunks_are_bounded_and_stable_for_pinned_revision(tmp_path):
    store=Store(tmp_path/'test.db')
    imported=store.import_sources([SourceInput.file(FIXTURE)])
    view=store.view(imported.source_ids)
    original=''.join(export_chunks(view,'json'))
    assert max(len(chunk) for chunk in export_chunks(view,'json'))<65536
    assert ''.join(export_chunks(view,'json'))==original


def test_rejected_lines_are_streamed_in_full_not_just_preview(tmp_path):
    path=tmp_path/'bad.log'; path.write_text('bad\n'*205,encoding='utf-8')
    store=Store(tmp_path/'test.db',ResourceLimits(preview_size=2,batch_size=3))
    view=store.view(store.import_sources([SourceInput.file(path)]).source_ids)
    write_stored_export(view,tmp_path/'all.json','json')
    document=json.loads((tmp_path/'all.json').read_text(encoding='utf-8'))
    assert len(document['ingestion_quality_unfiltered'][0]['issues'])==205
    assert document['status']['label']=='Uninterpretable'
    assert not document['events']


def test_exclusive_temp_name_collision_never_deletes_preexisting_file(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import log_analyzer.stored_export as module
    store=Store(tmp_path/'test.db'); view=store.view(store.import_sources([SourceInput.file(FIXTURE)]).source_ids)
    existing=tmp_path/'.export-aaaaaaaaaaaa.tmp'; existing.write_bytes(b'keep existing')
    monkeypatch.setattr(module.uuid,'uuid4',lambda:SimpleNamespace(hex='a'*32))
    with pytest.raises(FileExistsError): write_stored_export(view,tmp_path/'output.json','json')
    assert existing.read_bytes()==b'keep existing'
