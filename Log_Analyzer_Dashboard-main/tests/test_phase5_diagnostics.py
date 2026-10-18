from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

import pytest

from log_analyzer.diagnostics import DiagnosticOptions, iter_buckets, iter_alerts, summary, assess_status
from log_analyzer.filtering import FilterSpec, parse_filter_time
from log_analyzer.storage import Store
from log_analyzer.streams import SourceInput
from log_analyzer.stored_export import export_chunks, write_stored_export
from main import cli, run

ROOT = Path(__file__).resolve().parents[1]


def view_for(tmp_path, lines, filters=FilterSpec()):
    path = tmp_path/'source.log'
    path.write_text('\n'.join(lines), encoding='utf-8')
    store = Store(tmp_path/'store.db')
    return store.view(store.import_sources([SourceInput.file(path)]).source_ids, filters)


@pytest.mark.parametrize('kwargs', [
    {'bucket_seconds': 0}, {'bucket_seconds': 86401}, {'bucket_seconds': True},
    {'min_errors': 0}, {'min_errors': 1.5}, {'min_errors': 2**31},
    {'top_signatures': 0}, {'top_signatures': 101},
    {'error_rate': '-0.1'}, {'error_rate': '1.01'}, {'error_rate': 'NaN'},
    {'error_rate': 'Infinity'}, {'error_rate': 'bad'}, {'error_rate': '1e-19'},
    {'error_rate': '0e999999999'}, {'error_rate': '0'*33},
])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError): DiagnosticOptions(**kwargs)


def test_boundaries_offsets_out_of_order_and_evidence(tmp_path):
    view = view_for(tmp_path, [
        '2026-10-15T12:05:00Z CRITICAL repeated',
        '2026-10-15T12:04:59.999999Z ERROR repeated',
        '2026-10-15T17:30:00+05:30 INFO ready',
        '2026-10-15T12:00:00Z ERROR repeated',
    ])
    options = DiagnosticOptions(300, 2, '0.666666666666666666')
    buckets = list(iter_buckets(view, options))
    assert [(b['errors'], b['denominator'], b['alert']) for b in buckets] == [(2, 3, True), (1, 1, False)]
    assert buckets[0]['window_end_exclusive_utc'] == buckets[1]['window_start_utc'] == '2026-10-15T12:05:00+00:00'
    alert, = iter_alerts(view, options)
    rows = {row['event_id']:row for row in view.iter_events()}
    assert all(rows[e]['level'] in ('ERROR','CRITICAL') for e in alert['evidence_event_ids'])
    assert all(alert[k] for k in ('observation','hypothesis','suggested_next_steps'))
    assert summary(view, options)['alert_count'] == 1


@pytest.mark.parametrize('minimum,rate,expected', [(1,'0',True),(1,'0.5',True),(1,'0.500000000000000001',False),(2,'0.5',False),(1,'1',False)])
def test_exact_threshold_edges(tmp_path, minimum, rate, expected):
    view = view_for(tmp_path, ['2026-10-15T12:00:00Z ERROR failure','2026-10-15T12:00:01Z INFO ready'])
    assert list(iter_buckets(view, DiagnosticOptions(300,minimum,rate)))[0]['alert'] is expected


def test_sparse_windows_no_synthetic_zero_denominators(tmp_path):
    view = view_for(tmp_path, ['1900-01-01T00:00:00Z INFO ready','2099-01-01T00:00:00Z ERROR failure'])
    assert summary(view, DiagnosticOptions(1,1,'0'))['occupied_buckets'] == 2
    empty = view.store.view([s.id for s in view.sources], FilterSpec(severities=[]))
    assert list(iter_buckets(empty)) == list(iter_alerts(empty)) == []
    assert summary(empty)['occupied_buckets'] == summary(empty)['alert_count'] == 0
    assert assess_status(empty).label == 'No matches'


def test_no_errors_never_alert_even_at_zero_rate_threshold(tmp_path):
    view = view_for(tmp_path, ['2026-10-15T12:00:00Z INFO ready'])
    options = DiagnosticOptions(300, 1, '0')
    bucket, = iter_buckets(view, options)
    assert bucket['errors'] == 0 and bucket['error_rate'] == 0
    assert not bucket['alert'] and not bucket['evidence_event_ids']
    assert assess_status(view, options).label == 'No rule alerts'


@pytest.mark.parametrize('lines,status', [([], 'Empty'), (['bad input'], 'Uninterpretable')])
def test_no_parsed_evidence_keeps_quality_status(tmp_path,lines,status):
    view = view_for(tmp_path,lines)
    assert summary(view)['alert_count'] == 0
    assert assess_status(view).label == status


def test_logical_tracebacks_and_multiple_sources_share_bucket(tmp_path):
    first = tmp_path/'first.log'; second = tmp_path/'second.log'
    first.write_text('2026-10-15T12:00:00Z ERROR Failed\nTraceback (most recent call last):\n  File "a.py", line 1\nValueError: bad\n')
    second.write_text('2026-10-15T12:00:00Z INFO Ready\n2026-10-15T12:00:01Z CRITICAL Failed\n')
    store = Store(tmp_path/'store.db')
    view = store.view(store.import_sources([SourceInput.file(first),SourceInput.file(second)]).source_ids)
    bucket, = iter_buckets(view,DiagnosticOptions(300,2,'0.6'))
    assert bucket['errors'] == 2 and bucket['denominator'] == 3 and bucket['alert']
    assert bucket['evidence_event_ids'] == ['event-000001']


def test_diagnostics_do_not_decode_full_event_lists(tmp_path, monkeypatch):
    view = view_for(tmp_path,['2026-10-15T12:00:00Z ERROR repeated']*1000)
    def forbidden(*args,**kwargs): raise AssertionError('Full event materialization is forbidden')
    monkeypatch.setattr(view,'iter_events',forbidden)
    assert summary(view)['alert_count'] == 1
    assert next(iter_alerts(view))['denominator'] == 1000


def test_filters_define_conditional_denominator_and_partial_windows(tmp_path):
    view = view_for(tmp_path, ['2026-10-15T12:00:00Z INFO ready','2026-10-15T12:00:01Z ERROR failure', 'bad input'])
    selected = view.store.view([s.id for s in view.sources], FilterSpec(start=parse_filter_time('2026-10-15T12:00:01Z'), severities={'ERROR'},text='failure'))
    bucket, = iter_buckets(selected, DiagnosticOptions(300,1,'1'))
    assert bucket['errors'] == bucket['denominator'] == 1 and bucket['alert']
    findings = summary(selected)
    assert findings['source_rejected_lines'] == 1 and 'ALL investigation filters' in findings['denominator_definition']
    assert assess_status(selected).label == 'Incomplete'


def test_ranked_recurrences_keep_codes_levels_and_duplicate_evidence(tmp_path):
    view = view_for(tmp_path, [
        '2026-10-15T12:00:00Z ERROR E42 request_id=aaaaaaaaaaaaaaaa',
        '2026-10-15T12:00:00Z ERROR E42 request_id=aaaaaaaaaaaaaaaa',
        '2026-10-15T12:00:01Z ERROR E42 request_id=bbbbbbbbbbbbbbbb',
        '2026-10-15T12:00:02Z ERROR E43 request_id=cccccccccccccccc',
        '2026-10-15T12:00:03Z CRITICAL E42 request_id=dddddddddddddddd',
    ])
    group, = summary(view,DiagnosticOptions(top_signatures=1))['ranked_recurring_signatures']
    assert group['count'] == 3 and group['level'] == 'ERROR'
    assert group['first_seen_utc'] == '2026-10-15T12:00:00+00:00'
    assert len(group['evidence_event_ids']) == 2
    assert all(e in {r['event_id'] for r in view.iter_events()} for e in group['evidence_event_ids'])


@pytest.mark.parametrize('stamp', ['0001-01-01T00:00:00Z','1969-12-31T23:59:59.999999Z','9999-12-31T23:59:59Z'])
def test_calendar_extremes_and_pre_epoch_floor(tmp_path,stamp):
    view = view_for(tmp_path,[stamp+' ERROR failure'])
    bucket, = iter_buckets(view,DiagnosticOptions(86399,1,'1'))
    assert bucket['alert'] and bucket['window_start_utc'] and bucket['window_end_exclusive_utc']


def test_exports_share_rules_alerts_rankings_and_references(tmp_path):
    view = view_for(tmp_path,['2026-10-15T12:00:00Z ERROR failure']*6)
    options = DiagnosticOptions(60,6,'1',1)
    doc = json.loads(''.join(export_chunks(view,'json',options)))
    assert doc['schema_version'] == 3
    assert doc['diagnostics']['options'] == asdict(options)
    assert doc['status']['label'] == 'Rule alerts'
    assert doc['diagnostics']['alert_count'] == len(doc['diagnostics']['alerts']) == 1
    assert doc['diagnostics']['buckets'][0]['denominator'] == 6
    assert doc['diagnostics']['alerts'][0]['evidence_event_ids'][0] in {e['event_id'] for e in doc['events']}
    write_stored_export(view,tmp_path/'out.csv','csv',diagnostics=options)
    metadata = json.loads((tmp_path/'out.csv.metadata.json').read_text(encoding='utf-8'))
    assert metadata == {k:v for k,v in doc.items() if k!='events'}
    text = ''.join(export_chunks(view,'text',options))
    assert 'Alert: ' in text and 'hypothesis' in text and 'denominator' in text


@pytest.mark.parametrize('extra,expected',[([],0),(['--fail-on-alert'],3),(['--fail-on-alert','--min-errors','99'],0)])
def test_cli_alert_exit_and_machine_artifact_from_other_directory(tmp_path,extra,expected):
    source = tmp_path/'cli.log'; source.write_text('2026-10-15T12:00:00Z ERROR failure\n'*5)
    command = [sys.executable,str(ROOT/'main.py'),str(source),'--database',str(tmp_path/'cli.db'),'--report-dir',str(tmp_path/'out'),'--export','json',*extra]
    result = subprocess.run(command,cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode == expected, result.stderr+result.stdout
    assert 'Diagnostics: ' in result.stdout
    doc = json.loads((tmp_path/'out/investigation.json').read_text(encoding='utf-8'))
    assert doc['diagnostics']['options']['min_errors'] == (99 if '99' in extra else 5)


def test_cli_quality_failure_precedes_alert_exit(tmp_path):
    source = tmp_path/'cli.log'; source.write_text('2026-10-15T12:00:00Z ERROR failure\n'*5+'bad\n')
    assert run([source],db_path=tmp_path/'cli.db',report_dir=tmp_path/'out',fail_on_alert=True) == 1


@pytest.mark.parametrize('args',[['--bucket-seconds','0'],['--error-rate','NaN'],['--min-errors','0'],['--top-signatures','101']])
def test_cli_invalid_rules_before_writes(tmp_path,args):
    with pytest.raises(SystemExit) as caught:
        cli([*args,'--database',str(tmp_path/'invalid.db'),'--report-dir',str(tmp_path/'out')])
    assert caught.value.code == 2
    assert not (tmp_path/'invalid.db').exists() and not (tmp_path/'out').exists()


def test_dashboard_rules_export_agreement_and_cache_invalidation(monkeypatch):
    st = pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    file = BytesIO(b'2026-10-15T12:00:00Z ERROR failure\n'*5); file.name='diagnostics.log'
    monkeypatch.setattr(st.sidebar,'file_uploader',lambda *a,**k:file)
    downloads = {}; original = st.download_button; imports = []
    def capture(label,data,*args,**kwargs):
        downloads[label] = data
        return original(label,data,*args,**kwargs)
    original_import = Store.import_sources
    def counted(self,*args,**kwargs):
        imports.append(1)
        return original_import(self,*args,**kwargs)
    monkeypatch.setattr(st,'download_button',capture)
    monkeypatch.setattr(Store,'import_sources',counted)
    app = AppTest.from_string('import dashboard; dashboard.main()').run(timeout=20)
    assert not app.exception
    doc = json.loads(downloads['Download JSON'])
    assert doc['diagnostics']['alert_count'] == 1 and doc['status']['label'] == 'Rule alerts'
    next(w for w in app.number_input if w.label=='Minimum bucket errors').set_value(6).run(timeout=20)
    assert not app.exception and len(imports)==1
    updated = json.loads(downloads['Download JSON'])
    assert updated['diagnostics']['alert_count'] == 0 and updated['status']['label'] == 'No rule alerts'
    assert updated['storage'] == doc['storage']
    assert any('No configured diagnostic thresholds' in item.value for item in app.info)
    next(w for w in app.text_input if w.label=='Error rate threshold (fraction 0..1)').set_value('NaN').run(timeout=20)
    assert not app.exception and any('Invalid diagnostic rules' in e.value for e in app.error)
