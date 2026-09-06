"""Last-successful-run stats, used by the shrink floor."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import state_path


def load() -> dict:
    path = state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(stats: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(stats, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    path.write_text(json.dumps(payload, indent=2) + "\n")


class ShrinkFloor(RuntimeError):
    """The new feed is suspiciously smaller than the last good one."""


def check(previous: dict, calendars: int, events: int) -> None:
    """Refuse to publish a feed that collapsed.

    An expired app password or a captive portal can yield a technically valid
    two-calendar feed. Publishing it would wipe ~1100 events from every
    subscriber, and clients do not always recover cleanly.
    """
    if not previous:
        return
    prev_cals = previous.get("calendars", 0)
    prev_events = previous.get("events", 0)
    if prev_cals and calendars < 0.8 * prev_cals:
        raise ShrinkFloor(f"calendars {calendars} < 80% of last run's {prev_cals}; use --force to override")
    if prev_events and events < 0.5 * prev_events:
        raise ShrinkFloor(f"events {events} < 50% of last run's {prev_events}; use --force to override")
