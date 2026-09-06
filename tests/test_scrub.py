"""The scrub is the security boundary: everything here is a leak test."""

import re
from datetime import datetime, timezone

import pytest
from icalendar import Calendar

from calendar_sharer.merge import Source, redact_text
from calendar_sharer.merge import merge as _merge

# These modules build events dated 2026-01-01. The feed is now a rolling
# window, so merges here run against a clock that contains them.
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def merge(sources, **kwargs):
    kwargs.setdefault("now", NOW)
    return _merge(sources, **kwargs)



FORBIDDEN = ("ATTENDEE", "ORGANIZER", "DESCRIPTION", "ATTACH", "URL", "BEGIN:VALARM")

RICH_EVENT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
    "BEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260101T100000Z\r\n"
    "SUMMARY:Standup\r\n"
    "LOCATION;X-JMAP-ID=17:Room 4\r\n"
    "DESCRIPTION:Join https://berkeley.zoom.us/j/9723125652 pw 12345\r\n"
    "ATTENDEE;CN=Someone;RSVP=TRUE:mailto:someone@example.com\r\n"
    "ORGANIZER:mailto:boss@example.com\r\n"
    "ATTACH:https://example.com/boarding.pkpass\r\n"
    "URL:https://example.com/event\r\n"
    "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij\r\n"
    "X-JMAP-PRIVACY:private\r\n"
    "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT10M\r\nEND:VALARM\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_allowlist_drops_every_sensitive_property():
    ics, stats = merge([Source("aaaaaaaa-1", "A", RICH_EVENT)])
    text = ics.decode()
    for prop in FORBIDDEN:
        assert prop not in text, f"{prop} survived the scrub"
    assert "X-GOOGLE-CONFERENCE" not in text
    assert "X-JMAP-PRIVACY" not in text
    assert "someone@example.com" not in text
    assert "boss@example.com" not in text
    assert "zoom.us" not in text
    # what/when/where is kept
    assert "SUMMARY:Standup" in text
    assert "Room 4" in text


def test_parameters_are_allowlisted_too():
    """LOCATION;X-JMAP-ID=17 leaks a Fastmail internal id on an allowed property."""
    ics, _ = merge([Source("aaaaaaaa-1", "A", RICH_EVENT)])
    assert b"X-JMAP-ID" not in ics


def test_tzid_parameter_survives_because_events_need_it():
    src = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
        "BEGIN:VTIMEZONE\r\nTZID:America/Chicago\r\nBEGIN:STANDARD\r\n"
        "DTSTART:19700101T000000\r\nTZOFFSETFROM:-0500\r\nTZOFFSETTO:-0600\r\n"
        "END:STANDARD\r\nEND:VTIMEZONE\r\n"
        "BEGIN:VEVENT\r\nUID:u1\r\nDTSTART;TZID=America/Chicago:20260101T100000\r\n"
        "SUMMARY:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    ics, _ = merge([Source("aaaaaaaa-1", "A", src)])
    assert b"TZID=America/Chicago" in ics


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Standup https://zoom.us/j/860252", "Standup"),
        ("meet.google.com/abc-defg-hij", ""),
        ("Chat with a@b.com re: x", "Chat with re: x"),
        ("206 @ Legacy", "206 @ Legacy"),          # not an address
        ("Pi Day Party | Partiful", "Pi Day Party | Partiful"),  # the word, not a link
        ("Lunch", "Lunch"),
    ],
)
def test_redact_text(raw, expected):
    assert redact_text(raw) == expected


def test_private_events_become_busy():
    src = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
        "BEGIN:VEVENT\r\nUID:p1\r\nDTSTART:20260101T100000Z\r\n"
        "SUMMARY:Therapy\r\nLOCATION:123 Main St\r\nCLASS:PRIVATE\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    ics, stats = merge([Source("aaaaaaaa-1", "A", src)])
    text = ics.decode()
    assert stats.redacted == 1
    assert "Therapy" not in text
    assert "123 Main St" not in text
    assert "SUMMARY:Busy" in text


# --- against the real account, when fixtures are present -------------------

def test_real_feed_has_expected_shape(merged):
    ics, stats = merged
    assert stats.calendars == 22
    assert stats.timezones == 10
    # Windowed: what is kept, plus what was filtered, must equal what came in.
    assert stats.events + stats.past_dropped + stats.future_dropped == 1169
    assert stats.recurring > 0, "no recurring events survived the window"


def test_real_feed_leaks_nothing(merged):
    ics, _ = merged
    logical = ics.decode().replace("\r\n ", "").replace("\r\n", "\n")

    for prop in FORBIDDEN:
        assert not re.search(rf"^{re.escape(prop)}", logical, re.M), f"{prop} survived"
    assert ";X-" not in logical, "an X- parameter survived"

    for line in logical.split("\n"):
        if line.startswith("UID"):
            continue  # opaque machine ids like <token>@google.com
        assert not re.search(r"https?://|www\.[a-z]", line, re.I), f"URL leaked: {line[:60]}"
        assert not re.search(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", line), f"email leaked: {line[:60]}"


def test_real_feed_reparses_cleanly(merged):
    ics, stats = merged
    parsed = Calendar.from_ical(ics)
    events = list(parsed.walk("VEVENT"))
    assert len(events) == stats.events
    assert all(e.get("UID") and e.get("DTSTART") for e in events)


def test_every_referenced_timezone_is_defined(merged):
    ics, _ = merged
    parsed = Calendar.from_ical(ics)
    defined = {str(t["TZID"]) for t in parsed.walk("VTIMEZONE")}
    for event in parsed.walk("VEVENT"):
        for value in event.values():
            for item in value if isinstance(value, list) else [value]:
                tzid = getattr(item, "params", {}).get("TZID")
                if tzid:
                    assert str(tzid) in defined, f"undefined timezone {tzid}"
