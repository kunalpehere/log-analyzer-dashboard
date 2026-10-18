from pathlib import Path

import pytest

from log_analyzer import runtime
from log_analyzer.launcher import main as launch
from scripts.benchmark_storage import measure


def test_source_defaults_stay_application_relative_after_chdir(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    assert runtime.source_directory()==root
    assert runtime.output_directory()==root
    assert runtime.sample_directory()==root/'logs'


def test_installed_defaults_never_write_to_site_packages(tmp_path,monkeypatch):
    package=tmp_path/'installation/log_analyzer'; package.mkdir(parents=True)
    working=tmp_path/'working'; working.mkdir()
    monkeypatch.setattr(runtime,'__file__',str(package/'runtime.py'))
    monkeypatch.chdir(working)
    assert runtime.source_directory() is None
    assert runtime.output_directory()==working
    assert runtime.sample_directory()==package/'samples'


@pytest.mark.parametrize('port',['0','65536','bad'])
def test_dashboard_launcher_invalid_port_does_not_start_process(port,monkeypatch):
    import log_analyzer.launcher as module
    def forbidden(*args,**kwargs): raise AssertionError('Invalid input started a process')
    monkeypatch.setattr(module.subprocess,'call',forbidden)
    with pytest.raises(SystemExit) as error: launch(['--port',port])
    assert error.value.code==2


def test_benchmark_reproduces_evidence_but_preserves_existing_work(tmp_path):
    first=measure(20,tmp_path/'first'); second=measure(20,tmp_path/'second')
    assert first['source_sha256']==second['source_sha256']
    assert first['accepted_events']==second['accepted_events']==20
    assert first['unique_groups']==second['unique_groups']==20
    source=tmp_path/'first/input-20.log'; original=source.read_bytes()
    with pytest.raises(FileExistsError): measure(20,tmp_path/'first')
    assert source.read_bytes()==original


def test_benchmark_preserves_preexisting_source_without_database(tmp_path):
    source=tmp_path/'input-20.log'; source.write_bytes(b'preexisting evidence')
    with pytest.raises(FileExistsError): measure(20,tmp_path)
    assert source.read_bytes()==b'preexisting evidence'
    assert not (tmp_path/'run-20.db').exists()


@pytest.mark.parametrize('count',[0,-1,250001,True])
def test_benchmark_rejects_invalid_sizes_before_creation(tmp_path,count):
    folder=tmp_path/'never-created'
    with pytest.raises(ValueError): measure(count,folder)
    assert not folder.exists()
