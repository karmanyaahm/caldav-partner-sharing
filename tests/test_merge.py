from datetime import datetime, timezone

import pytest
from icalendar import Calendar

from calendar_sharer.merge import Source, empty_calendar, merge, redact_text


def cal(body: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\nNAME:Thing\r\n"
        f"{body}END:VCALENDAR\r\n"
    )


def vevent(uid: str | None = "u1", extra: str = "") -> str:
    uid_line = f"UID:{uid}\r\n" if uid else ""
    return (
        "BEGIN:VEVENT\r\n"
        f"{uid_line}"
        "DTSTART:20260101T100000Z\r\nDTEND:20260101T110000Z\r\nSUMMARY:hi\r\n"
        f"{extra}END:VEVENT\r\n"
    )


def events_of(ics: bytes):
    return list(Calendar.from_ical(ics).walk("VEVENT"))


def test_merges_under_one_wrapper():
    ics, stats = merge([
        Source("aaaaaaaa-1", "A", cal(vevent("one") + vevent("two"))),
        Source("bbbbbbbb-2", "B", cal(vevent("three"))),
    ])
    assert stats.events == 3
    assert ics.count(b"BEGIN:VCALENDAR") == 1
    assert ics.count(b"END:VCALENDAR") == 1
    assert len(events_of(ics)) == 3
    assert b"NAME:Thing" not in ics


def test_uids_namespaced_so_duplicate_calendars_cannot_collide():
    ics, stats = merge([
        Source("aaaaaaaa-1", "A", cal(vevent("same"))),
        Source("bbbbbbbb-2", "B", cal(vevent("same"))),
    ])
    assert stats.events == 2 and stats.duplicates == 0
    assert {str(e["UID"]) for e in events_of(ics)} == {"aaaaaaaa-same", "bbbbbbbb-same"}


def test_recurrence_override_stays_attached_to_its_master():
    ics, _ = merge([
        Source("aaaaaaaa-1", "A", cal(
            vevent("rec") + vevent("rec", "RECURRENCE-ID:20260102T100000Z\r\n")
        ))
    ])
    uids = [str(e["UID"]) for e in events_of(ics)]
    assert uids == ["aaaaaaaa-rec", "aaaaaaaa-rec"]


def test_exact_duplicate_dropped():
    _, stats = merge([Source("aaaaaaaa-1", "A", cal(vevent("dup") + vevent("dup")))])
    assert stats.events == 1 and stats.duplicates == 1


def test_uidless_events_do_not_collapse():
    """Regression: a null dedupe key silently discarded all but the first."""
    ics, stats = merge([
        Source("aaaaaaaa-1", "A", cal(
            vevent(None, "SUMMARY:first\r\n") + vevent(None, "SUMMARY:second\r\n")
        ))
    ])
    assert stats.events == 2, "UID-less events collapsed into one"
    assert len({str(e["UID"]) for e in events_of(ics)}) == 2


def test_vtodo_and_vjournal_dropped():
    ics, stats = merge([Source("aaaaaaaa-1", "A", cal(
        vevent("e")
        + "BEGIN:VTODO\r\nUID:t\r\nEND:VTODO\r\n"
        + "BEGIN:VJOURNAL\r\nUID:j\r\nEND:VJOURNAL\r\n"
    ))])
    assert stats.events == 1
    assert b"VTODO" not in ics and b"VJOURNAL" not in ics


@pytest.mark.parametrize("order", [("truncated", "full"), ("full", "truncated")])
def test_vtimezone_dedupe_prefers_uncapped_definition(order):
    blocks = {
        "truncated": (
            "BEGIN:VTIMEZONE\r\nTZID:America/Chicago\r\nTZUNTIL:20241209T180000Z\r\n"
            "BEGIN:STANDARD\r\nDTSTART:19700101T000000\r\nTZOFFSETFROM:-0500\r\n"
            "TZOFFSETTO:-0600\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
        ),
        "full": (
            "BEGIN:VTIMEZONE\r\nTZID:America/Chicago\r\nX-LIC-LOCATION:America/Chicago\r\n"
            "BEGIN:STANDARD\r\nDTSTART:19700101T000000\r\nTZOFFSETFROM:-0500\r\n"
            "TZOFFSETTO:-0600\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
        ),
    }
    ics, stats = merge([
        Source(f"{i}{i}{i}{i}{i}{i}{i}{i}", "x", cal(blocks[name]))
        for i, name in enumerate(order)
    ])
    assert stats.timezones == 1
    assert ics.count(b"BEGIN:VTIMEZONE") == 1
    assert b"TZUNTIL" not in ics


def test_generation_time_is_stamped():
    ics, _ = merge(
        [Source("aaaaaaaa-1", "A", cal(vevent()))],
        cal_name="Feed",
        now=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
    )
    assert b"X-WR-CALNAME:Feed (updated 2026-09-06T12:00:00Z)" in ics


def test_refresh_interval_is_a_duration_literal():
    ics, _ = merge([Source("aaaaaaaa-1", "A", cal(vevent()))])
    assert b"REFRESH-INTERVAL;VALUE=DURATION:PT4H" in ics
    assert b"X-PUBLISHED-TTL:PT4H" in ics


def test_empty_calendar_parses_and_has_no_events():
    parsed = Calendar.from_ical(empty_calendar())
    assert list(parsed.walk("VEVENT")) == []
