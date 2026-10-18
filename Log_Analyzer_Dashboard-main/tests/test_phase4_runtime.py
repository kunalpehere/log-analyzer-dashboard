from io import BytesIO
import json
from pathlib import Path

import pytest

pytest.importorskip('streamlit')
pytest.importorskip('plotly')
import streamlit as st
from streamlit.testing.v1 import AppTest

import dashboard
from log_analyzer.limits import ResourceLimits
from log_analyzer.storage import Store

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/investigation.log'


def upload(monkeypatch,raw):
    class NoCopy(BytesIO):
        def getvalue(self): raise AssertionError('Whole upload copy is forbidden')
    file=NoCopy(raw); file.name='input.log'
    monkeypatch.setattr(st.sidebar,'file_uploader',lambda *args,**kwargs:file)
    return file


def widget(items,label): return next(item for item in items if item.label==label)


def test_filter_and_page_reruns_reuse_completed_import(monkeypatch):
    upload(monkeypatch,FIXTURE.read_bytes()); calls=[]
    original=Store.import_sources
    def counted(self,*args,**kwargs):
        result=original(self,*args,**kwargs); calls.append(result)
        return result
    monkeypatch.setattr(Store,'import_sources',counted)
    app=AppTest.from_string('import dashboard; dashboard.main()').run(timeout=20)
    assert not app.exception and len(calls)==1
    app.text_input[2].set_value('e42').run(timeout=20)
    assert not app.exception and len(calls)==1
    widget(app.selectbox,'Rows per page').select(25).run(timeout=20)
    assert not app.exception and len(calls)==1
    widget(app.button,'Refresh sources').click().run(timeout=20)
    assert not app.exception and len(calls)==2 and calls[-1].reused==1


def test_large_source_is_paginated_and_exports_are_explicit(monkeypatch):
    raw=b'2026-10-15T12:00:00Z INFO repeated\n'*250
    upload(monkeypatch,raw)
    app=AppTest.from_string('import dashboard; dashboard.main()').run(timeout=20)
    assert not app.exception and app.metric[1].value=='250'
    assert len(app.dataframe[1].value)==100
    assert not app.get('download_button')
    widget(app.selectbox,'Rows per page').select(25).run(timeout=20)
    widget(app.number_input,'Event page').set_value(2).run(timeout=20)
    assert not app.exception and len(app.dataframe[1].value)==25
    assert app.dataframe[1].value.iloc[0]['start_line']==26
    assert app.metric[1].value=='250'
    widget(app.button,'Prepare full streamed exports').click().run(timeout=20)
    assert not app.exception and len(app.get('download_button'))==4


def test_browser_download_limit_does_not_truncate_full_export(monkeypatch):
    upload(monkeypatch,FIXTURE.read_bytes())
    class SmallDownloads(Store):
        def __init__(self,path): super().__init__(path,ResourceLimits(max_download_bytes=1))
    monkeypatch.setattr(dashboard,'Store',SmallDownloads)
    app=AppTest.from_string('import dashboard; dashboard.main()').run(timeout=20)
    assert not app.exception and not app.get('download_button')
    assert any('browser download limit' in item.value for item in app.info)
    folder=Path(app.session_state['_store_dir'])/'exports'
    full=next(folder.glob('*/investigation.json'))
    assert len(json.loads(full.read_text(encoding='utf-8'))['events'])==9


def test_import_limit_failure_keeps_last_completed_revision(monkeypatch):
    file=upload(monkeypatch,FIXTURE.read_bytes())
    class SmallInputs(Store):
        def __init__(self,path): super().__init__(path,ResourceLimits(max_source_bytes=1000))
    monkeypatch.setattr(dashboard,'Store',SmallInputs)
    app=AppTest.from_string('import dashboard; dashboard.main()').run(timeout=20)
    assert not app.exception and app.metric[1].value=='9'
    source_ids=app.session_state['_source_ids']
    file.seek(0); file.truncate(); file.write(b'x'*1001); file.seek(0)
    widget(app.button,'Refresh sources').click().run(timeout=20)
    assert not app.exception and not app.metric
    assert 'Source byte limit' in app.error[0].value
    store=Store(Path(app.session_state['_store_dir'])/'investigations.db')
    assert store.view(source_ids).event_count==9


def test_measured_python_allocation_and_batch_regression(tmp_path):
    from scripts.benchmark_storage import measure
    small=measure(1000,tmp_path/'small')
    large=measure(10000,tmp_path/'large')
    assert small['unique_groups']==1000 and large['unique_groups']==10000
    assert small['peak_event_batch']<=250 and large['peak_event_batch']<=250
    assert large['event_page_rows']==large['group_page_rows']==100
    assert large['peak_python_bytes']<=small['peak_python_bytes']+2*1024*1024
    assert large['peak_python_bytes']<4*1024*1024
