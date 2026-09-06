"""Calendars left out of the published feed.

Everything else is included by default, so a calendar created next month shows
up with no action here. IDs were read off the live account; the short IDs in the
original design doc (C3-, C0V, ...) do not exist on this server.
"""

EXCLUDED = {
    "75a524b5-381e-402c-b496-6515e1037fb0": "Contacts — birthdays, not events",
    "148cb99d-d438-4af8-9ba7-8edc4ef33a76": "Google-synced account calendar — republishing it would loop",
    "8d83f9bd-78ba-432b-ba93-088bf13872f1": "Old NextCloud Calendar — stale",
    "974cea54-d08f-4045-b9b4-572e5f0c9277": "Old NextCloud Personal — stale",
    # Both pairs below are duplicates. The survivor is whichever the server's
    # ctag says was written most recently.
    "56976221-7f17-48d2-82db-975e639a0f92": "VEXU copy — last changed 2025-01-23; 175afc41 is live",
    "17720d41-d157-422a-970c-cbb4428c47a0": "Holidays copy — last changed 2024-12-04; dc14b2ad is live",
}
