import csv
from io import BytesIO, StringIO
import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
import streamlit as st
from streamlit.testing.v1 import AppTest

from log_analyzer.analysis import analyze_upload
from log_analyzer.exporting import render_export
from log_analyzer.filtering import FilterSpec, parse_filter_time
from log_analyzer.investigation import investigate

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/investigation.log"


def app_with_upload(monkeypatch, content=None):
    file = BytesIO(FIXTURE.read_bytes() if content is None else content)
    file.name = "investigation.log"
    monkeypatch.setattr(st.sidebar, "file_uploader", lambda *args, **kwargs: file)
    downloads = {}
    original = st.download_button
    def capture(label, data, *args, **kwargs):
        downloads[label] = data
        return original(label, data, *args, **kwargs)
    monkeypatch.setattr(st, "download_button", capture)
    app = AppTest.from_string("import dashboard; dashboard.main()").run(timeout=20)
    assert not app.exception
    return app, downloads


def test_dashboard_filters_counts_groups_and_downloads_agree(monkeypatch):
    app, downloads = app_with_upload(monkeypatch)
    assert [metric.value for metric in app.metric] == ["9", "9", "0", "6", "1", "1"]
    app.text_input[0].set_value("2026-10-15T17:30:01+05:30")
    app.text_input[1].set_value("2026-10-15T12:00:02Z")
    app.text_input[2].set_value("e42")
    app.multiselect[0].set_value(["ERROR"]).run(timeout=20)
    assert not app.exception
    assert [metric.value for metric in app.metric] == ["3", "3", "0", "3", "0", "1"]
    doc = json.loads(downloads["Download JSON"])
    csv_rows = list(csv.DictReader(StringIO(downloads["Download CSV"].decode("utf-8"))))
    assert len(csv_rows) == len(doc["events"]) == 3
    assert doc["groups"][0]["count"] == 3
    assert len(doc["groups"]) == 1
    assert json.loads(downloads["Download CSV metadata"]) == {key: value for key, value in doc.items() if key != "events"}
    filters = FilterSpec(parse_filter_time("2026-10-15T12:00:01Z"), parse_filter_time("2026-10-15T12:00:02Z"), {"ERROR"}, "e42")
    expected = investigate([analyze_upload(FIXTURE.read_bytes(), "investigation.log")], filters)
    expected_doc=json.loads(render_export(expected, "json"))
    for key in ('selected','events','groups','filters'):
        assert doc[key] == expected_doc[key]
    assert doc['status']['label']=='No rule alerts'
    assert doc['schema_version']==3 and doc['storage']['source_revision_ids']
    tables = [item.value for item in app.dataframe]
    group_table = next(table for table in tables if "signature" in table.columns and "count" in table.columns)
    assert group_table["count"].tolist() == [3]
    selected_table = next(table for table in tables if "event_id" in table.columns)
    assert selected_table["event_id"].tolist() == [row["event_id"] for row in csv_rows]
    assert "Selected events: 3" in downloads["Download TEXT"].decode("utf-8")
    assert len(app.get("plotly_chart")) == 3


def test_dashboard_empty_selection_exports_no_fake_rows(monkeypatch):
    app, downloads = app_with_upload(monkeypatch)
    app.multiselect[0].set_value([]).run(timeout=20)
    assert not app.exception
    assert [metric.value for metric in app.metric][:5] == ["0", "0", "0", "0", "0"]
    assert any(item.value.startswith("No matches:") for item in app.warning)
    assert not app.success
    doc = json.loads(downloads["Download JSON"])
    assert doc["events"] == [] and doc["groups"] == []
    assert doc["filters"]["severities"] == []
    assert list(csv.DictReader(StringIO(downloads["Download CSV"].decode("utf-8")))) == []
    assert not app.get("plotly_chart")


@pytest.mark.parametrize("start,end", [("2026-10-15T12:00:00", ""), ("2026-10-16T12:00:00Z", "2026-10-15T12:00:00Z"), ("bad", "")])
def test_invalid_dashboard_filter_stops_before_showing_stale_results(monkeypatch, start, end):
    app, downloads = app_with_upload(monkeypatch)
    downloads.clear()
    app.text_input[0].set_value(start)
    app.text_input[1].set_value(end).run(timeout=20)
    assert not app.exception
    assert app.error[0].value.startswith("Invalid filters:")
    assert not app.metric
    assert not downloads


def test_dashboard_literal_case_sensitive_matching(monkeypatch):
    app, downloads = app_with_upload(monkeypatch)
    app.text_input[2].set_value("valueerror").run(timeout=20)
    assert app.metric[1].value == "6"
    app.checkbox[1].check().run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "0"
    assert json.loads(downloads["Download JSON"])["filters"]["case_sensitive"] is True


def test_filters_cannot_hide_source_rejections(monkeypatch):
    app, downloads = app_with_upload(monkeypatch, b"2026-10-15T12:00:00Z INFO ok\nbad")
    app.text_input[2].set_value("absent").run(timeout=20)
    assert not app.exception
    assert [metric.value for metric in app.metric][:3] == ["0", "0", "1"]
    assert any(item.value.startswith("Incomplete:") for item in app.warning)
    doc = json.loads(downloads["Download JSON"])
    assert not doc["events"]
    assert doc["ingestion_quality_unfiltered"][0]["rejected_lines"] == 1
