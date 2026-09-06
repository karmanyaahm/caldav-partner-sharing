"""calendar-sharer — merge Fastmail calendars into one revocable feed."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import caldav, config, publish, state, tokens
from .excluded import EXCLUDED
from .merge import Source, empty_calendar, merge

log = logging.getLogger("calendar-sharer")

# Cloudflare 403s the default urllib agent, which would break the listing check.
UA = "calendar-sharer/1.0 (+doctor)"


def _build(cfg) -> tuple[bytes, dict, list[str]]:
    session = caldav.session_for(cfg.fm_user, cfg.fm_apppw)
    discovered = caldav.discover(session, cfg.fm_user)
    included = [c for c in discovered if c.id not in EXCLUDED]
    log.info("discovered %d calendars, %d after exclusions", len(discovered), len(included))

    sources, failed = [], []
    for cal in included:
        try:
            sources.append(Source(id=cal.id, name=cal.name, ics=caldav.fetch(session, cal)))
        except caldav.AuthError:
            raise
        except Exception as err:  # one bad calendar must not cost the other 21
            log.error("skipping %s (%s): %s", cal.name, cal.id, err)
            failed.append(f"{cal.name}: {err}")

    if not sources:
        raise RuntimeError(f"no calendars fetched; {'; '.join(failed)}")

    ics, stats = merge(sources, cal_name=cfg.feed_name)
    if failed:
        log.warning("%d calendar(s) skipped; `doctor` will keep reporting them", len(failed))
    summary = dict(stats.as_dict(), discovered=len(discovered), failed=failed, bytes=len(ics))
    return ics, summary, failed


def cmd_generate(args) -> int:
    cfg = config.load(need_r2=not args.dry_run)

    lock = config.lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("another run holds %s; exiting", lock)
            return 1

        ics, summary, failed = _build(cfg)

        if not args.force:
            state.check(state.load(), summary["calendars"], summary["events"])

        if args.dry_run:
            out = Path(args.output or "feed.preview.ics")
            out.write_bytes(ics)
            summary["written"] = str(out)
            print(json.dumps(summary, indent=2))
            return 0

        active = tokens.active()
        if not active:
            log.warning("no active tokens; nothing to publish. Run: calendar-sharer token add <label>")
            return 0

        s3 = publish.client(cfg)
        uploaded, errors = [], []
        for token in active:
            try:
                publish.put(s3, cfg.r2_bucket, token, ics)
                uploaded.append(token)
            except Exception as err:
                errors.append(f"{token[:8]}...: {err}")
                log.error("upload failed for %s...: %s", token[:8], err)

        summary["uploaded"] = len(uploaded)
        summary["upload_errors"] = errors
        if uploaded:
            state.save({k: summary[k] for k in ("calendars", "events", "timezones", "bytes", "failed")})
        print(json.dumps(summary, indent=2))
        # A partial upload must not look like success to systemd. A calendar
        # that failed to fetch is recorded and surfaced by `doctor` instead of
        # failing the unit, so one chronically broken calendar does not paint
        # the timer red every hour. The shrink floor guards the dangerous case.
        return 1 if errors else 0


def cmd_token_add(args) -> int:
    cfg = config.load()
    token, entry = tokens.add(args.label)
    print(f"{entry['label']}: {cfg.url_for(token)}")
    print("\nThe feed appears at that URL after the next run. To publish it now:")
    print("  calendar-sharer generate")
    return 0


def cmd_token_list(args) -> int:
    cfg = config.load()
    table = tokens.load()
    if not table:
        print("no tokens yet — calendar-sharer token add <label>")
        return 0
    for token, meta in sorted(table.items(), key=lambda kv: kv[1].get("created", "")):
        status = f"REVOKED {meta['revoked']}" if meta.get("revoked") else "active"
        print(f"{meta.get('label', '?'):24} {status:32} {cfg.url_for(token)}")
    return 0


def cmd_token_revoke(args) -> int:
    """Overwrite with an empty calendar rather than deleting.

    A 404 does not revoke anything: Google and Apple keep the last successful
    snapshot when a subscription fails to fetch, so the recipient would keep the
    schedule — including RRULE instances years out — indefinitely. Serving a
    valid empty feed makes their client clear it.
    """
    cfg = config.load()
    if args.token not in tokens.load():
        log.error("unknown token")
        return 1
    s3 = publish.client(cfg)
    publish.put(s3, cfg.r2_bucket, args.token, empty_calendar())
    tokens.mark_revoked(args.token)
    print("Revoked. The URL now serves an empty calendar, which clears the subscriber's copy.")
    print("Once they have polled it (allow 48h), remove the object entirely:")
    print(f"  calendar-sharer token purge {args.token}")
    return 0


def cmd_token_purge(args) -> int:
    cfg = config.load()
    meta = tokens.load().get(args.token)
    if meta and not meta.get("revoked"):
        log.error("token is still active; run `token revoke` first so the subscriber's copy clears")
        return 1
    publish.delete(publish.client(cfg), cfg.r2_bucket, args.token)
    tokens.forget(args.token)
    print("Object deleted.")
    return 0


def cmd_doctor(args) -> int:
    cfg = config.load()
    last = state.load()
    table = tokens.load()

    print(f"config      {config.config_dir()}")
    print(f"tokens      {len(tokens.active())} active, {len(table)} total")

    if last.get("at"):
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])
        hours = age.total_seconds() / 3600
        flag = "  <-- STALE" if hours > 6 else ""
        print(f"last run    {last['at']} ({hours:.1f}h ago){flag}")
        print(f"            {last.get('calendars')} calendars, {last.get('events')} events")
        for failure in last.get("failed") or []:
            print(f"            SKIPPED {failure}")
    else:
        print("last run    never")

    session = caldav.session_for(cfg.fm_user, cfg.fm_apppw)
    discovered = caldav.discover(session, cfg.fm_user)
    print(f"\nincluded calendars ({sum(1 for c in discovered if c.id not in EXCLUDED)}):")
    for cal in sorted(discovered, key=lambda c: c.name.lower()):
        mark = "  excluded" if cal.id in EXCLUDED else "          "
        print(f" {mark} {cal.name}")

    # An object with no matching token is a live URL serving your calendar that
    # you have no record of — a lost tokens.json, or a purge that half-failed.
    print("\nbucket contents:")
    try:
        s3 = publish.client(cfg)
        keys = set(publish.list_keys(s3, cfg.r2_bucket))
        known = {publish.key_for(t): t for t in table}
        orphans = sorted(keys - set(known))
        missing = sorted(publish.key_for(t) for t in tokens.active() if publish.key_for(t) not in keys)
        print(f"  {len(keys)} object(s) in {cfg.r2_bucket}/{publish.PREFIX}")
        for key in orphans:
            print(f"  ORPHAN {key} — live URL with no token on record")
        for key in missing:
            print(f"  MISSING {key} — active token never published; run `generate`")
        if not orphans and not missing:
            print("  every object matches an active token")
    except Exception as err:
        print(f"  could not list: {err}")

    # The token is the object key, so anonymous listing would expose every one.
    #
    # Send a real User-Agent: Cloudflare's bot rules 403 the default
    # "Python-urllib/x.y", and a bot-protection 403 is indistinguishable from a
    # "listing denied" 403 — which would make this check silently pass even if
    # the bucket were wide open.
    print("\nbucket listing must not be public:")
    for probe in (cfg.public_base_url.rstrip("/") + "/", cfg.public_base_url.rstrip("/") + "/f/"):
        request = urllib.request.Request(probe, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=10) as res:
                body = res.read(2048)
                bad = b"<ListBucketResult" in body or b"<Key>" in body
                verdict = "!!! LISTING EXPOSED !!!" if bad else "no listing"
                print(f"  {probe} -> {res.status} {verdict}")
        except urllib.error.HTTPError as err:
            if err.code == 403 and "cloudflare" in str(err.headers.get("server", "")).lower():
                print(f"  {probe} -> 403, but from bot protection; check manually")
            else:
                print(f"  {probe} -> {err.code} (good)")
        except Exception as err:
            print(f"  {probe} -> {err}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="calendar-sharer", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="build the feed and upload it to every active token")
    gen.add_argument("--dry-run", action="store_true", help="write locally, upload nothing")
    gen.add_argument("--output", help="path for --dry-run (default feed.preview.ics)")
    gen.add_argument("--force", action="store_true", help="publish even if the feed shrank sharply")
    gen.set_defaults(func=cmd_generate)

    tok = sub.add_parser("token", help="manage recipient tokens").add_subparsers(dest="action", required=True)
    add = tok.add_parser("add", help="mint a token for one recipient")
    add.add_argument("label")
    add.set_defaults(func=cmd_token_add)
    tok.add_parser("list", help="list tokens and their URLs").set_defaults(func=cmd_token_list)
    rev = tok.add_parser("revoke", help="serve an empty calendar so the subscriber's copy clears")
    rev.add_argument("token")
    rev.set_defaults(func=cmd_token_revoke)
    pur = tok.add_parser("purge", help="delete the object, 48h after revoking")
    pur.add_argument("token")
    pur.set_defaults(func=cmd_token_purge)

    sub.add_parser("doctor", help="report health and check the bucket is not listable").set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except (config.ConfigError, state.ShrinkFloor, caldav.AuthError) as err:
        log.error("%s", err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
