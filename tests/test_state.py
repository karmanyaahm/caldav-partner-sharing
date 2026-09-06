import pytest

from calendar_sharer.state import ShrinkFloor, check


def test_no_previous_run_allows_anything():
    check({}, calendars=1, events=1)


def test_normal_run_passes():
    check({"calendars": 22, "events": 1169}, calendars=22, events=1180)


def test_lost_calendars_blocks_publish():
    with pytest.raises(ShrinkFloor, match="calendars"):
        check({"calendars": 22, "events": 1169}, calendars=2, events=1100)


def test_lost_events_blocks_publish():
    with pytest.raises(ShrinkFloor, match="events"):
        check({"calendars": 22, "events": 1169}, calendars=22, events=100)


def test_small_shrink_is_tolerated():
    check({"calendars": 22, "events": 1169}, calendars=19, events=900)
