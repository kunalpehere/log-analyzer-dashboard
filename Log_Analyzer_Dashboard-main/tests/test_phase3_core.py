from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
import json

import pytest

from log_analyzer.analysis import analyze_lines
from log_analyzer.filtering import FilterSpec, filter_events, parse_filter_time
from log_analyzer.grouping import group_errors, signature_tokens
from log_analyzer.investigation import investigate
from log_analyzer.models import Severity
from status_policy import assess_investigation


def scan(lines, source="source.log"):
    return analyze_lines(lines, source)


def event(message="failure", level="ERROR", time="12:00:00", source="source.log"):
    return scan([f"2026-10-15T{time}Z {level} {message}"], source).events[0]


@pytest.mark.parametrize("start,end,count", [
    (None, None, 3), ("2026-10-15T12:00:00Z", "2026-10-15T12:00:01Z", 2),
    ("2026-10-15T12:00:00Z", "2026-10-15T12:00:00Z", 1),
    ("2026-10-15T17:30:00+05:30", "2026-10-15T08:00:00-04:00", 1),
    (None, "2026-10-15T11:59:59Z", 0), ("2026-10-15T12:00:00.000001Z", None, 2),
])
def test_inclusive_utc_bounds(start, end, count):
    events = [event(time=f"12:00:0{index}") for index in range(3)]
    selected = filter_events(events, FilterSpec(parse_filter_time(start), parse_filter_time(end)))
    assert len(selected) == count


@pytest.mark.parametrize("stamp", ["2026-10-15T12:00:00", "bad", "2026-02-30T12:00:00Z", "2026-10-15T12:00:00+24:00"])
def test_filter_bound_requires_valid_explicit_timezone(stamp):
    with pytest.raises(ValueError):
        parse_filter_time(stamp)


def test_unbounded_and_reversed_ranges():
    assert parse_filter_time(" ") is None
    assert parse_filter_time(None) is None
    with pytest.raises(ValueError, match="Start time"):
        FilterSpec(parse_filter_time("2026-10-16T00:00:00Z"), parse_filter_time("2026-10-15T00:00:00Z"))


@pytest.mark.parametrize("kwargs", [
    {"start": datetime(2026, 10, 15)}, {"end": "2026-10-15T12:00:00Z"},
    {"severities": "ERROR"}, {"severities": ["WARN"]}, {"severities": ["error"]},
    {"severities": [None]}, {"text": None}, {"case_sensitive": 1},
])
def test_invalid_filter_contract(kwargs):
    with pytest.raises(ValueError):
        FilterSpec(**kwargs)


def test_severity_is_exact_with_none_all_and_empty_none():
    events = [event(level=level.value) for level in Severity]
    assert len(filter_events(events)) == 5
    assert filter_events(events, FilterSpec(severities=frozenset())) == ()
    result = filter_events(events, FilterSpec(severities={"ERROR"}))
    assert [item.level for item in result] == [Severity.ERROR]
    result = filter_events(events, FilterSpec(severities={"ERROR", "CRITICAL"}))
    assert {item.level for item in result} == {Severity.ERROR, Severity.CRITICAL}


@pytest.mark.parametrize("query,sensitive,count", [("STRASSE", False, 1), ("STRASSE", True, 0), (".*", False, 1), ("[x]", False, 1), ("\nsecond", False, 1), ("  ", False, 1)])
def test_literal_message_matching_and_unicode(query, sensitive, count):
    events = [event("Straße [x] .*  details\nsecond line"), event("other")]
    # Build multiline decoded messages directly; physical ingestion tests cover traceback grammar.
    events[0] = replace(events[0], message="Straße [x] .*  details\nsecond line")
    assert len(filter_events(events, FilterSpec(text=query, case_sensitive=sensitive))) == count


def test_filters_combine_with_and_and_do_not_mutate_sources():
    result = scan(["2026-10-15T12:00:00Z ERROR failed", "2026-10-15T12:00:01Z INFO failed", "bad"])
    before_events, before_counts = list(result.events), dict(result.counts)
    view = investigate([result], FilterSpec(end=parse_filter_time("2026-10-15T12:00:00Z"), severities={"ERROR"}, text="FAIL"))
    assert len(view.events) == view.error_count == 1
    assert result.events == before_events and result.counts == before_counts
    assert view.sources[0].rejected_count == 1
    assert assess_investigation(view).label == "Incomplete"


def test_stable_sort_and_genuine_duplicates():
    a = event(source="a.log")
    b = event(source="b.log")
    early = event(time="11:59:59")
    first = filter_events([b, a, a, early])
    second = filter_events([a, early, b, a])
    assert first == second == (early, a, a, b)
    assert len(first) == 4


def test_complete_input_without_matches_has_explicit_status():
    source = scan(["2026-10-15T12:00:00Z ERROR failed"])
    view = investigate([source], FilterSpec(text="absent"))
    assert not view.events and not view.groups
    assert assess_investigation(view).label == "No matches"
    assert sum(view.counts.values()) == 0


def test_rejected_evidence_does_not_disappear_when_filters_match_nothing():
    source = scan(["2026-10-15T12:00:00Z INFO ok", "bad"])
    view = investigate([source], FilterSpec(text="absent"))
    assert assess_investigation(view).label == "Incomplete"
    assert view.sources[0].issues


@pytest.mark.parametrize("label,first,second", [
    ("request_id=", "a" * 16, "b" * 32),
    ("trace-id: ", "01234567-89ab-cdef-0123-456789abcdef", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ("correlationId=", "0" * 64, "1" * 64),
])
def test_labeled_id_normalization_preserves_original_evidence(label, first, second):
    source = scan([f"2026-10-15T12:00:00Z ERROR ValueError code=E42 {label}{first}",
                   f"2026-10-15T12:00:01Z ERROR ValueError code=E42 {label}{second}"])
    view = investigate([source])
    assert len(view.groups) == 1
    group = view.groups[0]
    assert group.count == 2
    assert "ValueError code=E42" in group.signature
    assert first in view.events[0].message and second in view.events[1].message
    assert group.member_indices == (0, 1)
    assert group.first_seen == view.events[0].timestamp
    assert group.last_seen == view.events[1].timestamp


@pytest.mark.parametrize("first,second", [
    ("ValueError code=E42", "TypeError code=E42"),
    ("failed status=404", "failed status=500"),
    ("error code=0xABCDEF0123456789", "error code=0x1111111111111111"),
    ("id=aaaaaaaaaaaaaaaa", "id=bbbbbbbbbbbbbbbb"),
    ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    ("request_id=123", "request_id=456"),
    ('request_id="aaaaaaaaaaaaaaaa"', 'request_id="bbbbbbbbbbbbbbbb"'),
    ("request_id=aaaaaaaaaaaaaaaaz", "request_id=bbbbbbbbbbbbbbbbz"),
    ("request_id=aaaaaaaaaaaaaaaa-suffix", "request_id=bbbbbbbbbbbbbbbb-suffix"),
    ("request_id=" + "a" * 65, "request_id=" + "b" * 65),
    ("Failure", "failure"), ("Failed  request", "Failed request"),
])
def test_conservative_signatures_keep_meaningful_differences(first, second):
    view = investigate([scan([f"2026-10-15T12:00:00Z ERROR {first}", f"2026-10-15T12:00:01Z ERROR {second}"])])
    assert len(view.groups) == 2


def test_literal_placeholder_cannot_collide_with_normalized_id():
    view = investigate([scan(["2026-10-15T12:00:00Z ERROR failed request_id=aaaaaaaaaaaaaaaa", "2026-10-15T12:00:00Z ERROR failed request_id=<hex-id>"])])
    assert len(view.groups) == 2
    assert view.groups[0].signature == view.groups[1].signature
    assert view.groups[0].tokens != view.groups[1].tokens
    assert view.groups[0].signature_id != view.groups[1].signature_id


def test_groups_include_only_selected_errors_and_keep_severity_separate():
    result = scan([f"2026-10-15T12:00:00Z {level.value} same" for level in Severity])
    view = investigate([result])
    assert len(view.groups) == 2
    assert sum(group.count for group in view.groups) == view.error_count == 2
    assert {group.level for group in view.groups} == {Severity.ERROR, Severity.CRITICAL}
    assert len(investigate([result], FilterSpec(severities={"WARNING"})).groups) == 0


def test_groups_stable_ranking_examples_and_member_references():
    result = scan([f"2026-10-15T12:00:0{index}Z ERROR failed request_id={index:016x}" for index in range(5)] + ["2026-10-15T12:00:09Z ERROR another"])
    view = investigate([result])
    assert [group.count for group in view.groups] == [5, 1]
    assert view.groups[0].example_indices == (0, 1, 2)
    assert len({row["event_id"] for row in view.event_rows}) == 6
    for group in view.groups:
        for index in group.member_indices:
            assert view.event_rows[index]["group_id"] == group.signature_id
    reversed_result = replace(result, events=list(reversed(result.events)))
    assert investigate([reversed_result]).group_rows == view.group_rows
    assert investigate([reversed_result]).event_rows == view.event_rows


def test_identical_messages_still_count_as_repeated_events():
    result = scan(["2026-10-15T12:00:00Z ERROR same"] * 4)
    view = investigate([result])
    assert view.groups[0].count == 4
    assert len(view.groups[0].example_indices) == 1
    assert len(view.event_rows) == 4


def test_traceback_exception_types_are_not_normalized_away():
    first = event("failed request_id=aaaaaaaaaaaaaaaa")
    first = replace(first, message=first.message + "\nValueError: code E42")
    second = replace(first, message=first.message.replace("ValueError", "TypeError"))
    assert len(group_errors([first, second])) == 2
