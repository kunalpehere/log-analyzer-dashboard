from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="Install requirements-dev.txt for dashboard smoke tests")
pytest.importorskip("plotly", reason="Install requirements-dev.txt for dashboard smoke tests")
from streamlit.testing.v1 import AppTest

import dashboard
from analyzer import analyze_log_file

ROOT = Path(__file__).resolve().parents[1]


def test_default_dashboard_smoke_and_single_scan(monkeypatch):
    calls = []
    original=dashboard.Store.import_sources
    def counted(self, inputs, *args, **kwargs):
        inputs=tuple(inputs); calls.append(len(inputs))
        return original(self, inputs, *args, **kwargs)
    monkeypatch.setattr(dashboard.Store, "import_sources", counted)
    app = AppTest.from_string("import dashboard; dashboard.main()").run(timeout=20)
    assert not app.exception
    assert calls == [3]
    assert [metric.value for metric in app.metric] == ["48", "48", "0", "17", "11", "3"]
    assert any(item.value.startswith(("Rule alerts:", "No rule alerts:")) for item in [*app.warning, *app.info])
    assert len(app.get("plotly_chart")) == 3
    assert "source" in app.dataframe[-1].value.columns


@pytest.mark.parametrize("content,label", [
    (b"", "Empty:"), (b"   \n", "Empty:"), (b"bad input", "Uninterpretable:"),
    (b"\xff", "Failed:"),
    (b"2026-10-15 12:00:00 INFO ready\nbad", "Incomplete:"),
])
def test_upload_result_dashboard_states(content, label):
    script = f"""
import streamlit as st
from analyzer import analyze_upload
from dashboard import render_results
render_results(st, [analyze_upload({content!r}, 'upload.log')])
"""
    app = AppTest.from_string(script).run(timeout=20)
    assert not app.exception
    messages = [item.value for item in app.warning] + [item.value for item in app.error]
    assert any(message.startswith(label) for message in messages)
    assert not app.success


def test_uploaded_mixed_input_uses_real_severity_counts():
    fixture = ROOT / "tests/fixtures/mixed.log"
    script = f"""
import streamlit as st
from pathlib import Path
from analyzer import analyze_upload
from dashboard import render_results
render_results(st, [analyze_upload(Path({str(fixture)!r}).read_bytes(), 'upload.log')])
"""
    app = AppTest.from_string(script).run(timeout=20)
    assert not app.exception
    assert [metric.value for metric in app.metric] == ["5", "5", "4", "2", "1", "1"]
    levels = app.dataframe[0].value
    assert levels.set_index("Level")["Count"].to_dict() == {"DEBUG": 1, "INFO": 1, "WARNING": 1, "ERROR": 1, "CRITICAL": 1}
    assert len(app.dataframe[1].value) == 4


def test_missing_default_source_is_visible(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "DEFAULT_LOG_FILES", [tmp_path / "missing.log"])
    app = AppTest.from_string("import dashboard; dashboard.main()").run(timeout=20)
    assert not app.exception
    assert app.error[0].value.startswith("Failed:")
    assert any("FileNotFoundError" in item.value for item in app.error)
    assert not app.success
