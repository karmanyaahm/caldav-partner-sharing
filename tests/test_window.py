"""The date filter. The failure that matters is a recurring event vanishing.

A weekly meeting's DTSTART is its FIRST occurrence, routinely long before the
window. Measured on this account: of the ten live series, all ten start before
the window and the oldest begins two years earlier -- so a date comparison
against DTSTART deletes every recurring event, silently, and the subscriber
finds out by missing a meeting. Every test here exists to stop that regressing.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from icalendar import Calendar

from calendar_sharer.merge import (
    Source,
    as_date,
    event_span,
    in_window,
    is_recurring,
    merge,
    series_ended_before,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
START = date(2026, 8, 30)   # NOW - 7d
END = date(2026, 10, 4)     # NOW + 28d


def event(**props) -> Calendar:
    lines = "".join(f"{k.replace('_', '-')}:{v}\r\n" for k, v in props.items())
    raw = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
        f"BEGIN:VEVENT\r\nUID:u1\r\nSUMMARY:s\r\n{lines}END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    return list(Calendar.from_ical(raw).walk("VEVENT"))[0]


def keep(**props) -> bool:
    return in_window(event(**props), START, END)[0]


def reason(**props) -> str:
    return in_window(event(**props), START, END)[1]


# --- the regression the whole design is built around ----------------------

def test_ancient_weekly_series_is_kept():
    """An extreme case of the real pattern: old DTSTART, still running."""
    assert keep(DTSTART="19700101T100000Z", RRULE="FREQ=WEEKLY;BYDAY=MO")
    assert reason(DTSTART="19700101T100000Z", RRULE="FREQ=WEEKLY;BYDAY=MO") == "recurring"


def test_rdate_only_series_is_kept():
    """A series can be defined by RDATE alone, with no RRULE."""
    ev = event(DTSTART="20200101T100000Z", RDATE="20261001T100000Z")
    assert is_recurring(ev)
    assert in_window(ev, START, END)[0]


def test_recurrence_id_override_is_kept_however_old():
    """An override edits one instance; dropping it un-edits that occurrence."""
    assert keep(DTSTART="20200101T100000Z", RECURRENCE_ID="20200101T100000Z")


def test_no_recurring_event_is_ever_dropped_as_future():
    """Even a series starting past the horizon keeps running later."""
    assert keep(DTSTART="20990101T100000Z", RRULE="FREQ=WEEKLY")


# --- one-time events: the only thing the window applies to ----------------

@pytest.mark.parametrize(
    "dtstart,dtend,expected",
    [
        ("20200101T100000Z", "20200101T110000Z", False),  # long past
        ("20260828T100000Z", "20260828T110000Z", False),  # 2 days before window
        ("20260830T100000Z", "20260830T110000Z", True),   # exactly on window start
        ("20260906T100000Z", "20260906T110000Z", True),   # today
        ("20261004T100000Z", "20261004T110000Z", True),   # exactly on window end
        ("20270101T100000Z", "20270101T110000Z", False),  # far future
    ],
)
def test_one_time_event_boundaries(dtstart, dtend, expected):
    assert keep(DTSTART=dtstart, DTEND=dtend) is expected


def test_long_event_spanning_into_the_window_is_kept():
    """Started before the window but still running inside it."""
    assert keep(DTSTART="20260101T100000Z", DTEND="20260915T110000Z")


def test_duration_is_used_when_dtend_absent():
    ev = event(DTSTART="20260829T100000Z", DURATION="P5D")
    start, end = event_span(ev)
    assert start == date(2026, 8, 29)
    assert end == date(2026, 9, 3)
    assert in_window(ev, START, END)[0], "DURATION should carry it into the window"


def test_all_day_event_at_both_edges():
    assert keep(**{"DTSTART;VALUE=DATE": "20260830"}) is True
    assert keep(**{"DTSTART;VALUE=DATE": "20200101"}) is False


def test_undated_event_is_kept_rather_than_guessed_at():
    assert keep(SUMMARY="no start") is True
    assert reason(SUMMARY="no start") == "undated"


def test_future_bound_disabled_keeps_everything_ahead():
    assert in_window(event(DTSTART="20990101T100000Z", DTEND="20990101T110000Z"), START, None)[0]


def test_future_boundary_has_a_day_of_slack_for_timezones():
    """Dates are reduced from datetimes in arbitrary zones; keep, don't guess."""
    assert keep(DTSTART="20261005T230000Z", DTEND="20261005T233000Z") is True
    assert keep(DTSTART="20261007T230000Z", DTEND="20261007T233000Z") is False


# --- dead series ----------------------------------------------------------

def test_series_that_ended_before_the_window_is_dropped():
    assert series_ended_before(event(DTSTART="20200101T100000Z", RRULE="FREQ=WEEKLY;UNTIL=20200601T000000Z"), START)
    assert not keep(DTSTART="20200101T100000Z", RRULE="FREQ=WEEKLY;UNTIL=20200601T000000Z")


def test_series_with_until_inside_the_window_is_kept():
    assert keep(DTSTART="20200101T100000Z", RRULE="FREQ=WEEKLY;UNTIL=20261001T000000Z")


def test_count_based_series_is_never_dropped():
    """Resolving COUNT means walking the recurrence; keeping is the safe default."""
    assert not series_ended_before(event(DTSTART="20200101T100000Z", RRULE="FREQ=WEEKLY;COUNT=3"), START)
    assert keep(DTSTART="20200101T100000Z", RRULE="FREQ=WEEKLY;COUNT=3")


def test_rdate_overrides_a_past_until():
    """An RDATE can add occurrences after UNTIL, so the series is not dead."""
    assert not series_ended_before(
        event(DTSTART="20200101T100000Z", RRULE="FREQ=WEEKLY;UNTIL=20200601T000000Z", RDATE="20261001T100000Z"),
        START,
    )


# --- helpers --------------------------------------------------------------

def test_as_date_normalises_every_shape():
    assert as_date(datetime(2026, 9, 6, 23, 0, tzinfo=timezone.utc)) == date(2026, 9, 6)
    assert as_date(datetime(2026, 9, 6, 23, 0)) == date(2026, 9, 6)
    assert as_date(date(2026, 9, 6)) == date(2026, 9, 6)
    assert as_date(None) is None


# --- end to end, and the accounting must balance --------------------------

def test_merge_keeps_a_1970_series_end_to_end():
    ics, stats = merge(
        [Source("aaaaaaaa-1", "A", (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:weekly\r\nDTSTART:19700101T100000Z\r\n"
            "RRULE:FREQ=WEEKLY;BYDAY=MO\r\nSUMMARY:Weekly Team Meeting\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:old\r\nDTSTART:20200101T100000Z\r\n"
            "DTEND:20200101T110000Z\r\nSUMMARY:Ancient one-off\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ))],
        now=NOW,
    )
    assert stats.events == 1 and stats.recurring == 1 and stats.past_dropped == 1
    text = ics.decode()
    assert "Weekly Team Meeting" in text
    assert "Ancient one-off" not in text
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in text, "the rule itself must survive"


def test_fixture_accounting_balances(sources):
    """Nothing may be lost in transit: kept + dropped == everything there was."""
    ics, stats = merge(sources, now=NOW)
    total = sum(len(list(Calendar.from_ical(s.ics).walk("VEVENT"))) for s in sources)
    assert stats.events + stats.past_dropped + stats.future_dropped + stats.duplicates == total


def test_every_recurring_fixture_event_survives(sources):
    """By construction: count recurring events in the sources, expect them out."""
    ics, stats = merge(sources, now=NOW)
    recurring_in = sum(
        1
        for s in sources
        for e in Calendar.from_ical(s.ics).walk("VEVENT")
        if is_recurring(e) and not series_ended_before(e, START)
    )
    assert stats.recurring == recurring_in
    out = [e for e in Calendar.from_ical(ics).walk("VEVENT") if "RRULE" in e or "RDATE" in e]
    assert len(out) >= 1, "no recurrence rules survived into the feed at all"
