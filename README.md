# calendar-sharer

Merges your Fastmail calendars into one `.ics` and publishes it at a separate,
unguessable URL per recipient — so you can **revoke one person** without
disturbing anyone else.

Fastmail's own publish URL cannot do that. Hand it to five people and you can
never cut off just one. That is the entire reason this exists.

```
laptop (systemd timer, hourly)
   PROPFIND -> 28 calendars -> exclude 6 -> GET each
   merge + scrub -> one VCALENDAR
   PUT one object per recipient token
        |
   https://cal.example.com/f/<token>.ics
```

## What gets published

Only **what / when / where**. Each event is rebuilt from a strict property
allowlist, so anything not named is dropped:

| Kept | Dropped |
|---|---|
| `SUMMARY`, `LOCATION` | `ATTENDEE`, `ORGANIZER` |
| `DTSTART`, `DTEND`, `DURATION` | `DESCRIPTION`, `ATTACH`, `URL` |
| `RRULE`, `RDATE`, `EXDATE`, `RECURRENCE-ID` | `VALARM` (would ring *their* phone) |
| `STATUS`, `TRANSP`, `CLASS`, `UID` | every `X-*` property **and parameter** |

`SUMMARY` and `LOCATION` are free text and in practice carry join links, so URLs,
conferencing hostnames and email addresses are redacted from them too. Events
marked `CLASS:PRIVATE` become `Busy` with no location.

On the current account that removes 571 descriptions, 149 events' worth of
attendee addresses, 152 organizers and 36 alarms. Verified by test: no URL and
no email address survives anywhere outside opaque `UID` values.

`VTODO` and `VJOURNAL` are not published at all.

## Install

Needs [Nix](https://nixos.org/download) and systemd.

```sh
./install.sh
```

It builds with Nix, installs into `~/.nix-profile`, writes
`~/.config/calendar-sharer/env` (mode 0600), enables an hourly user timer, and
smoke-tests under `systemd-run` — not your interactive shell, which has a
different environment and would pass where the timer fails.

The binary deliberately lives in the Nix store rather than beside this checkout:
a repo on a removable or LUKS volume is not mounted at boot, and a
`Persistent=true` timer fires its catch-up run before such a volume exists.

### Cloudflare setup (once)

1. Create an R2 bucket.
2. R2 → **Manage R2 API Tokens** → Create. Only this flow issues S3 credentials;
   the general account API tokens page does not.
   - Permission: **Object Read & Write** (needs put, delete and list)
   - Scope: **specific buckets** → just this one, not "All buckets"
   - TTL: **forever**. A timer cannot answer an interactive login prompt, which
     is the whole reason this uses a static key.
   - It issues an **Access Key ID** and **Secret Access Key** → `R2_ACCESS_KEY_ID`
     and `R2_SECRET_ACCESS_KEY`. `R2_ACCOUNT_ID` is on the R2 overview page.
3. Connect a custom domain to the bucket. Leave the `r2.dev` subdomain off;
   Cloudflare documents it as rate-limited and development-only.

R2 public buckets never expose listing. Since the token *is* the filename, that
matters: on GCS the obvious role (`roles/storage.objectViewer`) includes
`storage.objects.list` and would publish every token at one anonymous URL.

## Use

```sh
calendar-sharer token add alice      # mint a URL for one recipient
calendar-sharer token list           # who has what
calendar-sharer generate             # build + publish now
calendar-sharer generate --dry-run   # build locally, upload nothing
calendar-sharer doctor               # health; finds orphaned objects; confirms
                                     # the bucket is not publicly listable
```

### Revoking

**Deleting the object is not enough.** Google Calendar and Apple Calendar keep
the last successful snapshot when a subscription fails to fetch, so a 404 leaves
the recipient holding your schedule — including recurring events years out —
indefinitely. Revocation is therefore two steps:

```sh
calendar-sharer token revoke <token>   # serve a valid EMPTY calendar; their copy clears
# ...wait ~48h for their client to poll...
calendar-sharer token purge <token>    # now actually delete the object
```

## Which calendars

Everything is included by default, so a calendar you create next month appears
on its own. The exclusions live in `src/calendar_sharer/excluded.py`.

Note that Fastmail also surfaces calendars *other people share with you*, and
those are included too. `calendar-sharer doctor` prints the full included list.

## Safety rails

- **Shrink floor** — refuses to publish if the feed lost >20% of its calendars
  or >50% of its events versus the last good run. An expired app password or a
  captive portal otherwise yields a valid-looking 2-calendar feed that wipes
  every subscriber's copy. Override with `--force`.
- **Auth failures abort** rather than publishing a partial feed.
- **`flock`** so a manual run cannot race the timer.
- **Retry with backoff** in-script, because after resume-from-suspend there is
  often no DNS yet and a user-manager unit has no `network-online.target`.
- A partial upload exits non-zero so systemd records a failure. A calendar that
  merely failed to *fetch* does not fail the unit — it is recorded and reported
  by `doctor`, so one chronically broken calendar does not turn the timer red
  every hour while the shrink floor still guards the dangerous case.

## Development

```sh
nix develop            # shell with icalendar, boto3, requests, pytest
pytest tests -q        # 30 tests
nix flake check        # 26 tests in a sandbox; the 4 fixture-based
                       # integration tests skip, since fixtures/ is gitignored
```

The four integration tests run against your real calendars, pulled to a
gitignored `fixtures/`. They are what pin the expected shape (22 calendars,
1169 events, 10 timezones) and assert that nothing leaks from real data:

```sh
python scripts/fetch_fixtures.py
```

## Known limits

- **A laptop that is off does not publish.** `Persistent=true` catches up on
  wake; a suspended machine cannot be woken by a user timer.
- **Staleness is only weakly signalled.** The generation timestamp goes into
  `X-WR-CALNAME`, but Google and Apple freeze a subscription's display name at
  subscribe time, so it is largely invisible. `doctor` reports feed age locally.
- **Anyone holding a token can forward it.** Inherent to URL subscription;
  revocation is the mitigation.
