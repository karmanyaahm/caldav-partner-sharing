"""Merge many Fastmail calendars into one publishable VCALENDAR."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from icalendar import Calendar, Event, Timezone

PRODID = "-//calendar-sharer//merged feed//EN"
REFRESH = timedelta(hours=4)

# Strict allowlist. Copy in what is named rather than deleting what is known to
# be bad, so a property Fastmail starts emitting next year cannot leak silently.
ALLOWED = (
    "UID",
    "DTSTART",
    "DTEND",
    "DURATION",
    "DTSTAMP",
    "LAST-MODIFIED",
    "CREATED",
    "SEQUENCE",
    "SUMMARY",
    "LOCATION",
    "RRULE",
    "RDATE",
    "EXDATE",
    "RECURRENCE-ID",
    "STATUS",
    "TRANSP",
    "CLASS",
)

# Everything else is dropped, but these are the ones that actually carry other
# people's data, and the reason the allowlist exists at all.
_NOTABLE_DROPS = ("ATTENDEE", "ORGANIZER", "DESCRIPTION", "ATTACH", "URL", "VALARM")

_PRIVATE = {"PRIVATE", "CONFIDENTIAL"}

# Parameters ride along on a property even when the property itself is allowed:
# LOCATION;X-JMAP-ID=1 leaks a Fastmail internal id. Allowlist these too.
SAFE_PARAMS = {"TZID", "VALUE", "RANGE"}

# SUMMARY and LOCATION are free text the user controls, and in practice they
# carry join links ("Standup https://zoom.us/j/123") and addresses. Dropping the
# whole property would gut the feed, so redact just the sensitive spans.
_CONFERENCING = (
    r"zoom\.us|meet\.google\.com|teams\.(?:microsoft|live)\.com|webex\.com|"
    r"whereby\.com|chime\.aws|bluejeans\.com|gather\.town|discord\.gg|partiful\.com"
)
_REDACTIONS = (
    re.compile(r"\b(?:https?://|www\.)\S+", re.I),
    re.compile(rf"\b\S*(?:{_CONFERENCING})\S*", re.I),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]*[\w-]\b"),
)


def _strip_params(value):
    """Drop every parameter not on the allowlist, in place."""
    params = getattr(value, "params", None)
    if params is not None:
        for key in [k for k in params if k.upper() not in SAFE_PARAMS]:
            del params[key]
    return value


def redact_text(text: str) -> str:
    for pattern in _REDACTIONS:
        text = pattern.sub("", text)
    # Collapse the whitespace and dangling punctuation a removal leaves behind.
    text = re.sub(r"\s{2,}", " ", text).strip()
    return re.sub(r"^[\s:;,\-–—|]+|[\s:;,\-–—|]+$", "", text).strip()


@dataclass
class Stats:
    calendars: int = 0
    events: int = 0
    recurring: int = 0
    past_dropped: int = 0
    future_dropped: int = 0
    duplicates: int = 0
    timezones: int = 0
    redacted: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "calendars": self.calendars,
            "events": self.events,
            "recurring": self.recurring,
            "past_dropped": self.past_dropped,
            "future_dropped": self.future_dropped,
            "duplicates": self.duplicates,
            "timezones": self.timezones,
            "redacted": self.redacted,
            "dropped": self.dropped,
        }


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    ics: str


def is_recurring(event: Event) -> bool:
    """Does this describe a series rather than a single occurrence?

    RECURRENCE-ID counts: it marks an override of one instance of a series, and
    dropping it would leave that occurrence showing its unedited version.
    """
    return any(key in event for key in ("RRULE", "RDATE", "RECURRENCE-ID"))


def as_date(value) -> date | None:
    """Reduce a DTSTART/DTEND value to a plain date for comparison.

    Values arrive as dates (all-day), naive datetimes, or aware datetimes in any
    zone. Comparing plain dates sidesteps every tz trap; the resulting day of
    slack is handled explicitly at the window edges.
    """
    value = getattr(value, "dt", value)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def event_span(event: Event) -> tuple[date | None, date | None]:
    """(start, end) as dates. End falls back to DURATION, then to start."""
    start = as_date(event.get("DTSTART"))
    end = as_date(event.get("DTEND"))
    if end is None and start is not None and "DURATION" in event:
        duration = event["DURATION"].dt
        if isinstance(duration, timedelta):
            end = start + duration
    return start, end if end is not None else start


def series_ended_before(event: Event, cutoff: date) -> bool:
    """True only when a series provably has no occurrence at or after cutoff.

    Only UNTIL is consulted. COUNT is deliberately ignored: resolving it means
    walking the whole recurrence, which is exactly the machinery this design
    avoids. An over-kept dead series is inert -- the client renders nothing from
    it -- so anything uncertain is kept.
    """
    if "RDATE" in event or "RRULE" not in event:
        return False
    rule = event["RRULE"]
    until = rule.get("UNTIL") if hasattr(rule, "get") else None
    if not until:
        return False
    value = until[0] if isinstance(until, list) else until
    day = as_date(value)
    return day is not None and day < cutoff


def in_window(event: Event, window_start: date, window_end: date | None) -> tuple[bool, str]:
    """Keep this event? Returns (keep, reason).

    Recurring events are kept regardless of date. Their DTSTART is the FIRST
    occurrence, which is routinely long before the window: of the ten live
    series on this account, all ten start before it and the oldest begins two
    years earlier. Testing DTSTART would therefore delete every recurring event
    the feed has. Only one-time events are windowed.
    """
    if is_recurring(event):
        if series_ended_before(event, window_start):
            return False, "past"
        return True, "recurring"

    start, end = event_span(event)
    if start is None:
        return True, "undated"  # cannot judge it; keeping is the safe default
    if end is not None and end < window_start:
        return False, "past"
    # A day of slack ahead: these dates are reduced from datetimes in arbitrary
    # zones. Erring toward keeping is right -- a wrongly dropped event is
    # invisible to the subscriber, a wrongly kept one is merely noise.
    if window_end is not None and start > window_end + timedelta(days=1):
        return False, "future"
    return True, "in-window"


def _synthesise_uid(event: Event) -> str:
    """A UID-less event still needs a stable identity.

    Without this, every UID-less event shares one dedupe key and all but the
    first are silently discarded.
    """
    return "x-" + hashlib.sha256(event.to_ical()).hexdigest()[:32]


def _copy_allowed(src: Event, prefix: str, stats: Stats) -> Event:
    out = Event()

    private = str(src.get("CLASS", "PUBLIC")).upper() in _PRIVATE
    if private:
        stats.redacted += 1

    for name in ALLOWED:
        if name == "UID":
            uid = str(src["UID"]) if "UID" in src else _synthesise_uid(src)
            # Namespace per calendar so the two duplicate calendars cannot
            # collide. RECURRENCE-ID overrides share their master's UID, so a
            # consistent prefix keeps an override attached to its master.
            out.add("UID", f"{prefix}-{uid}")
            continue
        if name not in src:
            continue
        if private and name in ("SUMMARY", "LOCATION"):
            continue

        value = src[name]
        for item in value if isinstance(value, list) else [value]:
            if name in ("SUMMARY", "LOCATION"):
                redacted = redact_text(str(item))
                if not redacted:
                    continue
                out.add(name, redacted)
            else:
                out.add(name, _strip_params(item), encode=False)

    if private:
        out.add("SUMMARY", "Busy")

    for name in _NOTABLE_DROPS:
        if name in src or (name == "VALARM" and src.walk("VALARM")):
            stats.dropped[name] = stats.dropped.get(name, 0) + 1

    return out


def _tz_score(tz: Timezone) -> tuple[int, int]:
    """Fastmail sometimes serves a VTIMEZONE truncated with TZUNTIL.

    Prefer an uncapped definition, then the more detailed one.
    """
    return (0 if "TZUNTIL" in tz else 1, len(tz.to_ical()))


def merge(
    sources: list[Source],
    cal_name: str = "Calendar",
    now: datetime | None = None,
    past_days: int = 7,
    future_days: int | None = 28,
) -> tuple[bytes, Stats]:
    now = now or datetime.now(timezone.utc)
    window_start = now.date() - timedelta(days=past_days)
    window_end = None if future_days is None else now.date() + timedelta(days=future_days)
    stats = Stats()

    timezones: dict[str, Timezone] = {}
    events: list[Event] = []
    seen: set[tuple[str, str]] = set()

    for source in sources:
        parsed = Calendar.from_ical(source.ics)
        stats.calendars += 1

        for tz in parsed.walk("VTIMEZONE"):
            tzid = str(tz.get("TZID", "")).strip()
            if not tzid:
                continue
            if tzid not in timezones or _tz_score(tz) > _tz_score(timezones[tzid]):
                timezones[tzid] = tz

        # VTODO and VJOURNAL are deliberately not walked: subscribers want events.
        prefix = source.id[:8]
        for event in parsed.walk("VEVENT"):
            keep, reason = in_window(event, window_start, window_end)
            if not keep:
                stats.past_dropped += reason == "past"
                stats.future_dropped += reason == "future"
                continue
            stats.recurring += reason == "recurring"
            scrubbed = _copy_allowed(event, prefix, stats)
            key = (
                str(scrubbed["UID"]),
                scrubbed["RECURRENCE-ID"].to_ical().decode() if "RECURRENCE-ID" in scrubbed else "",
            )
            if key in seen:
                stats.duplicates += 1
                continue
            seen.add(key)
            events.append(scrubbed)
            stats.events += 1

    stats.timezones = len(timezones)

    out = Calendar()
    out.add("PRODID", PRODID)
    out.add("VERSION", "2.0")
    out.add("CALSCALE", "GREGORIAN")
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.add("X-WR-CALNAME", f"{cal_name} (updated {stamp})")
    span = f"from {window_start}" + (f" to {window_end}" if window_end else " onward")
    out.add("X-WR-CALDESC", f"One-time events {span}; all recurring events included.")
    out.add("REFRESH-INTERVAL", REFRESH, parameters={"VALUE": "DURATION"})
    # icalendar renders a timedelta here as "4:00:00"; the property wants a
    # duration literal.
    out.add("X-PUBLISHED-TTL", "PT4H")

    for tz in timezones.values():
        out.add_component(tz)
    for event in events:
        out.add_component(event)

    return out.to_ical(), stats


def empty_calendar(reason: str = "This calendar feed has been revoked.") -> bytes:
    """A valid calendar with no events.

    Revocation cannot be a 404: Google and Apple keep the last successful
    snapshot when a subscription fails to fetch, so the recipient would keep the
    schedule forever. Serving a valid empty feed makes their client clear it.
    """
    out = Calendar()
    out.add("PRODID", PRODID)
    out.add("VERSION", "2.0")
    out.add("CALSCALE", "GREGORIAN")
    out.add("X-WR-CALNAME", reason)
    return out.to_ical()
