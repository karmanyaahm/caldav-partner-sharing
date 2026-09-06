"""CalDAV against Fastmail.

A plain GET on a calendar collection returns the whole collection as one
VCALENDAR, so there is no need for calendar-query REPORTs.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote, unquote, urljoin

import requests

log = logging.getLogger(__name__)

BASE = "https://caldav.fastmail.com"
DAV = "{DAV:}"
CALDAV = "{urn:ietf:params:xml:ns:caldav}"

TIMEOUT = 30
# On resume from suspend the network and DNS are routinely not ready yet, and a
# user-manager unit has no network-online.target to wait on. Retrying here is
# the only real fix.
BACKOFF = (30, 120, 300)

PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
    <C:supported-calendar-component-set/>
  </D:prop>
</D:propfind>"""


class AuthError(RuntimeError):
    """401/403 from Fastmail — abort rather than publish a partial feed."""


@dataclass(frozen=True)
class Calendar:
    id: str
    href: str
    name: str


def calendar_home(user: str) -> str:
    return f"{BASE}/dav/calendars/user/{quote(user)}/"


def _request(session: requests.Session, method: str, url: str, **kw) -> requests.Response:
    last: Exception | None = None
    for attempt, delay in enumerate((0, *BACKOFF)):
        if delay:
            log.warning("retrying %s %s in %ss (attempt %d)", method, url, delay, attempt + 1)
            time.sleep(delay)
        try:
            res = session.request(method, url, timeout=TIMEOUT, **kw)
        except requests.RequestException as err:
            last = err
            continue
        if res.status_code in (401, 403):
            raise AuthError(f"{method} {url} -> {res.status_code}; check the Fastmail app password")
        if res.status_code >= 500:
            last = RuntimeError(f"{method} {url} -> {res.status_code}")
            continue
        return res
    raise RuntimeError(f"{method} {url} failed after {len(BACKOFF) + 1} attempts: {last}")


def _ok_props(response: ET.Element):
    """Yield only the prop elements whose propstat reported 2xx."""
    for propstat in response.findall(DAV + "propstat"):
        status = (propstat.findtext(DAV + "status") or "").split()
        if len(status) > 1 and status[1].startswith("2"):
            prop = propstat.find(DAV + "prop")
            if prop is not None:
                yield prop


def parse_calendar_list(xml: bytes) -> list[Calendar]:
    out: list[Calendar] = []
    for response in ET.fromstring(xml).findall(DAV + "response"):
        href = response.findtext(DAV + "href")
        if not href:
            continue

        name, is_calendar, components = None, False, []
        for prop in _ok_props(response):
            display = prop.find(DAV + "displayname")
            if display is not None and display.text:
                name = display.text.strip()
            resourcetype = prop.find(DAV + "resourcetype")
            if resourcetype is not None and resourcetype.find(CALDAV + "calendar") is not None:
                is_calendar = True
            comp_set = prop.find(CALDAV + "supported-calendar-component-set")
            if comp_set is not None:
                components = [c.get("name") for c in comp_set]

        if not is_calendar:
            continue
        # An empty set means the server did not say; assume events are welcome.
        if components and "VEVENT" not in components:
            continue

        cal_id = unquote(href.rstrip("/").rsplit("/", 1)[-1])
        out.append(Calendar(id=cal_id, href=href, name=name or cal_id))
    return out


def discover(session: requests.Session, user: str) -> list[Calendar]:
    """Enumerate every calendar collection in the user's calendar home.

    Never hardcode the list — a calendar created next month should just appear.
    """
    url = calendar_home(user)
    res = _request(
        session,
        "PROPFIND",
        url,
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        data=PROPFIND_BODY.encode(),
    )
    if res.status_code != 207:
        raise RuntimeError(f"PROPFIND {url} -> {res.status_code}")
    return parse_calendar_list(res.content)


def fetch(session: requests.Session, calendar: Calendar) -> str:
    res = _request(session, "GET", urljoin(BASE, calendar.href), headers={"Accept": "text/calendar"})
    if not res.ok:
        raise RuntimeError(f"GET {calendar.id} -> {res.status_code}")
    body = res.text
    if not body.startswith("BEGIN:VCALENDAR"):
        raise RuntimeError(f"GET {calendar.id} -> not a VCALENDAR ({body[:40]!r})")
    return body


def session_for(user: str, app_password: str) -> requests.Session:
    session = requests.Session()
    session.auth = (user, app_password)
    return session
