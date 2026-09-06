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

## Quick start

Everything runs through the flake — nothing is installed into `PATH`.
Run these from the repo directory (or swap `.` for its full path).

```sh
# one-time: build, configure, and enable the hourly timer
nix run .#install

# give someone the feed
nix run . -- token add alice
#   -> alice: https://cal.example.com/f/xK3n....ics
#   share that URL; it appears after the next publish

# publish right now instead of waiting for the timer
nix run . -- generate

# see who has what
nix run . -- token list
#   alice    active    https://cal.example.com/f/xK3n....ics
#   bob      REVOKED   https://cal.example.com/f/9pQr....ics

# cut one person off (two steps -- see Revoking, it matters)
nix run . -- token revoke xK3n....
# ...48h later...
nix run . -- token purge xK3n....

# is it healthy? did anything leak?
nix run . -- doctor

# what would be published, without publishing it
nix run . -- generate --dry-run --output /tmp/feed.ics
```

Watch it run:

```sh
systemctl --user list-timers calendar-sharer.timer
journalctl --user -u calendar-sharer -f
systemctl --user start calendar-sharer.service   # trigger a run now
```

Each token is a separate URL to the same feed. Revoking one leaves every other
recipient untouched — which is the thing Fastmail's own publish URL cannot do.


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

Needs [Nix](https://nixos.org/download) with flakes, and systemd.

```sh
nix run .#install
```

**Nothing is installed into `PATH`.** The only artifacts are two symlinks:

```
~/.config/systemd/user/calendar-sharer.service -> /nix/store/...-calendar-sharer-units/
~/.config/systemd/user/calendar-sharer.timer   -> /nix/store/...-calendar-sharer-units/
```

The unit's `ExecStart` is an absolute store path, substituted at build time.
That matters beyond tidiness: the user manager's `PATH` at boot is the
compiled-in default, so a bare command name there would be a coin flip.

A third symlink at `/nix/var/nix/gcroots/calendar-sharer` roots the units
against garbage collection. It is required — a symlink in `~/.config` is *not*
a GC root, so without it `nix-collect-garbage` would delete the interpreter and
code the timer depends on. Rooting the units is enough: the store path appears
in the unit text, so Nix records it as a reference and keeps the whole closure.

The installer seeds from a `.env` in the working directory if one exists, then
prompts for anything missing, and smoke-tests under `systemd-run` — not your
interactive shell, which has a different environment and would pass where the
timer fails.

Re-run `nix run .#install` to upgrade; it re-points the symlinks and the GC root
at the new build.

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
3. Bucket → Settings → **Public access → Custom Domains → Connect Domain**.
   This creates the DNS record for you, so until you do it the hostname simply
   will not resolve and nothing can fetch the feed. Set `PUBLIC_BASE_URL` to
   `https://` + that hostname. Leave the `r2.dev` subdomain off; Cloudflare
   documents it as rate-limited and development-only.

## Use

Since nothing is on `PATH`, invoke it through the flake. The timer runs itself;
these are for managing recipients.

```sh
nix run . -- token add alice      # mint a URL for one recipient
nix run . -- token list           # who has what
nix run . -- generate             # build + publish now
nix run . -- generate --dry-run   # build locally, upload nothing
nix run . -- doctor               # health; finds orphaned objects; confirms
                                  # the bucket is not publicly listable
```

From outside the checkout, use the path: `nix run /path/to/calendar-sharer -- doctor`.
If you do want a short command, add a shell alias rather than installing into
your profile — that keeps the store path pinned by the GC root above.

### Revoking

**Deleting the object is not enough.** Google Calendar and Apple Calendar keep
the last successful snapshot when a subscription fails to fetch, so a 404 leaves
the recipient holding your schedule — including recurring events years out —
indefinitely. Revocation is therefore two steps:

```sh
nix run . -- token revoke <token>   # serve a valid EMPTY calendar; their copy clears
# ...wait ~48h for their client to poll...
nix run . -- token purge <token>    # now actually delete the object
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
