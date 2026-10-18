from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def upload(monkeypatch, content, name):
    file = BytesIO(content)
    file.name = name
    monkeypatch.setattr(st.sidebar, "file_uploader", lambda *args, **kwargs: file)


def test_dashboard_json_selection_uses_shared_options_and_utc_evidence(monkeypatch):
    upload(monkeypatch, (ROOT / "tests/fixtures/events.jsonl").read_bytes(), "events.jsonl")
    app = AppTest.from_string("import dashboard; dashboard.main()").run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "0"  # Explicit text selection does not guess JSON.
    next(item for item in app.selectbox if item.label == "Input format").select("jsonl").run(timeout=20)
    assert not app.exception
    assert [metric.value for metric in app.metric] == ["3", "3", "0", "1", "1", "1"]
    evidence = app.dataframe[1].value
    assert evidence.iloc[0]["timestamp_utc"] == "2026-10-15T06:30:00.123456+00:00"
    assert evidence.iloc[0]["original_timestamp"] == "2026-10-15T12:00:00.123456+05:30"
    assert evidence.iloc[0]["raw_text"].startswith('{"timestamp":')
    next(item for item in app.selectbox if item.label == "Timestamps without a timezone").select("reject").run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "3"  # Explicit offsets remain accepted.
    assert len(app.get("plotly_chart")) == 3


def test_dashboard_naive_policy_rejects_instead_of_inferring_local_time(monkeypatch):
    upload(monkeypatch, b"2026-10-15 12:00:00 INFO ready", "naive.log")
    app = AppTest.from_string("import dashboard; dashboard.main()").run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "1"
    evidence = app.dataframe[1].value
    assert evidence.iloc[0]["timestamp_utc"] == "2026-10-15T12:00:00+00:00"
    assert bool(evidence.iloc[0]["timestamp_assumed"])
    next(item for item in app.selectbox if item.label == "Timestamps without a timezone").select("reject").run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "0"
    assert app.metric[2].value == "1"
    assert "Naive timestamp rejected" in app.dataframe[1].value.iloc[0]["reason"]


def test_dashboard_traceback_counts_limits_and_disable_control(monkeypatch):
    raw = b'2026-10-15 12:00:00 ERROR failed\nTraceback (most recent call last):\n  File "app.py", line 1, in run\n    work()\nValueError: bad\n'
    upload(monkeypatch, raw, "trace.log")
    app = AppTest.from_string("import dashboard; dashboard.main()").run(timeout=20)
    assert not app.exception
    assert [metric.value for metric in app.metric][:4] == ["5", "1", "0", "1"]
    assert any("4 traceback continuation lines" in item.value for item in app.caption)
    evidence = app.dataframe[1].value.iloc[0]
    assert (evidence["start_line"], evidence["end_line"]) == (1, 5)
    assert evidence["raw_text"].encode("utf-8") == raw
    next(item for item in app.number_input if item.label == "Maximum lines per event").set_value(2).run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "0"
    assert app.metric[2].value == "5"
    app.checkbox[0].uncheck().run(timeout=20)
    assert not app.exception
    assert app.metric[1].value == "1"
    assert app.metric[2].value == "4"
