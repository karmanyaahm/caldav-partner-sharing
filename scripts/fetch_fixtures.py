#!/usr/bin/env python3
"""Pull every calendar into fixtures/ so the merge can be tested offline.

fixtures/ is gitignored: it is real personal calendar data.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from calendar_sharer import caldav, config  # noqa: E402


def main() -> int:
    cfg = config.load()
    session = caldav.session_for(cfg.fm_user, cfg.fm_apppw)
    calendars = caldav.discover(session, cfg.fm_user)

    out = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
    out.mkdir(exist_ok=True)

    index = []
    for cal in calendars:
        (out / f"{cal.id}.ics").write_text(caldav.fetch(session, cal))
        index.append({"id": cal.id, "name": cal.name, "href": cal.href})

    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"wrote {len(index)} calendars to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
