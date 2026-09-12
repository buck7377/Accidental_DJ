"""Command line entry point: sync -> enrich -> build."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from . import db
from .matching import (DEFAULT_MAX_PER_TRACK, DEFAULT_TOLERANCE, build_pairs,
                       candidates_from_rows)
from .report import payload, render

DEFAULT_DB = "library.db"
DEFAULT_OUT = "transitions.html"


def _err(message: str) -> int:
    print(f"\nerror: {message}", file=sys.stderr)
    return 1


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


# --------------------------------------------------------------------- sync
def cmd_sync(args: argparse.Namespace) -> int:
    from .spotify import SpotifyConfigError, iter_liked_tracks, make_client

    try:
        client = make_client(args.auth_cache)
    except SpotifyConfigError as exc:
        return _err(str(exc))

    conn = db.connect(args.db)
    before = db.count(conn, "SELECT COUNT(*) FROM tracks")
    seen: set[str] = set()
    total = 0

    print("Reading Liked Songs from Spotify...")
    try:
        for row in iter_liked_tracks(client):
            row["synced_at"] = db.now()
            db.upsert_track(conn, row)
            seen.add(row["id"])
            total += 1
            if total % 50 == 0:
                conn.commit()
                print(f"  {total} tracks...", end="\r", flush=True)
    except KeyboardInterrupt:
        conn.commit()
        print(f"\nStopped. {total} tracks saved so far. Run sync again to finish.")
        return 130
    except Exception as exc:  # spotipy raises a variety of network/auth errors
        conn.commit()
        message = str(exc)
        if "401" in message or "token" in message.lower():
            message += ("\nDelete the auth cache and re-authorise: "
                        f"rm {args.auth_cache}")
        elif "403" in message:
            message += ("\nCheck that your Spotify app allows this account and that "
                        "the user-library-read scope was granted.")
        return _err(f"Spotify sync failed after {total} tracks: {message}")

    conn.commit()
    if total == 0:
        print("\nNo liked songs came back. If your library is not empty, remove "
              f"{args.auth_cache} and re-run to re-authorise with the "
              "user-library-read scope.")
        return 1

    removed = 0
    if args.prune:
        placeholders = ",".join("?" * len(seen)) if seen else "''"
        cursor = conn.execute(
            f"DELETE FROM tracks WHERE id NOT IN ({placeholders})", tuple(seen)
        )
        removed = cursor.rowcount or 0
        conn.commit()

    db.set_meta(conn, "last_sync", db.now())
    conn.commit()
    after = db.count(conn, "SELECT COUNT(*) FROM tracks")
    pending = len(db.pending_tracks(conn))

    print(f"\nSynced {total} liked songs ({after - before:+d} in the database, "
          f"{after} total).")
    if removed:
        print(f"Pruned {removed} track(s) no longer in Liked Songs.")
    if pending:
        print(f"Next: {pending} track(s) need tempo and key. Run:  "
              f"{_prog()} enrich --db {args.db}")
    else:
        print(f"Next: {_prog()} build --db {args.db}")
    return 0


# ------------------------------------------------------------------- enrich
def cmd_enrich(args: argparse.Namespace) -> int:
    try:
        from .getsongbpm import GetSongBPMClient, GetSongBPMError
    except ImportError:
        return _err("requests is not installed. Run: pip install -r requirements.txt")

    api_key = os.environ.get("GETSONGBPM_API_KEY", "").strip()
    try:
        client = GetSongBPMClient(api_key, delay=args.delay)
    except GetSongBPMError as exc:
        return _err(str(exc))

    conn = db.connect(args.db)
    if db.count(conn, "SELECT COUNT(*) FROM tracks") == 0:
        return _err(f"No tracks in {args.db}. Run:  {_prog()} sync --db {args.db}")

    pending = db.pending_tracks(conn, retry_misses=args.retry_misses, limit=args.limit)
    if not pending:
        done = db.count(conn, "SELECT COUNT(*) FROM audio WHERE status = 'ok'")
        misses = db.count(conn, "SELECT COUNT(*) FROM audio WHERE status <> 'ok'")
        print(f"Nothing to enrich: {done} track(s) resolved, {misses} cached miss(es).")
        if misses:
            print("Use --retry-misses to try the misses again.")
        return 0

    total = len(pending)
    print(f"Enriching {total} track(s) from GetSongBPM at ~{args.delay}s per request "
          f"(about {_fmt_duration(total * args.delay)}). Ctrl-C is safe -- progress "
          "is committed after every track.")

    counts = {"ok": 0, "no_match": 0, "error": 0}
    started = time.monotonic()
    processed = 0

    try:
        for index, track in enumerate(pending, start=1):
            result = client.lookup(track["title"], track["primary_artist"])
            db.record_audio(
                conn, track["id"], result.status, tempo=result.tempo,
                key_raw=result.key_raw, camelot=result.camelot, time_sig=result.time_sig,
                match_title=result.match_title, match_artist=result.match_artist,
                query=result.query, detail=result.detail,
            )
            conn.commit()  # every track, so Ctrl-C never loses work
            counts[result.status] = counts.get(result.status, 0) + 1
            processed += 1

            if result.status == "ok":
                tempo = f"{result.tempo:6.1f}" if result.tempo else "     ?"
                mark = f"{'ok':<8} {tempo} BPM {(result.camelot or '?'):>3}"
            else:
                mark = f"{result.status:<8} {(result.detail or '')[:30]:<30}"
            elapsed = time.monotonic() - started
            eta = (elapsed / processed) * (total - processed)
            print(f"[{index:>4}/{total}] {mark}  {track['title'][:38]} — "
                  f"{track['primary_artist'][:24]}   eta {_fmt_duration(eta)}")
    except KeyboardInterrupt:
        conn.commit()
        print(f"\nStopped after {processed} of {total} track(s). "
              f"Everything so far is saved -- run enrich again to resume.")
        return 130
    except GetSongBPMError as exc:
        conn.commit()
        return _err(f"{exc}\n{processed} track(s) were saved before this.")

    print(f"\nDone: {counts['ok']} resolved, {counts['no_match']} not found, "
          f"{counts['error']} failed.")
    if counts["no_match"] or counts["error"]:
        print("Misses are cached so re-runs skip them. Retry with --retry-misses.")
    remaining = len(db.pending_tracks(conn))
    if remaining:
        print(f"{remaining} track(s) still unenriched. Run enrich again to continue.")
    else:
        print(f"Next: {_prog()} build --db {args.db}")
    return 0


# -------------------------------------------------------------------- build
def cmd_build(args: argparse.Namespace) -> int:
    if args.tolerance <= 0:
        return _err("--tolerance must be greater than 0.")

    conn = db.connect(args.db)
    rows = db.all_tracks_with_audio(conn)
    if not rows:
        return _err(f"No tracks in {args.db}. Run:  {_prog()} sync --db {args.db}")

    track_rows = []
    for index, row in enumerate(rows):
        data = dict(row)
        data["index"] = index
        data["bpm"] = data.pop("tempo", None)
        track_rows.append(data)

    candidates = candidates_from_rows(track_rows)
    if not candidates:
        return _err(
            f"None of the {len(track_rows)} track(s) have both a tempo and a readable "
            f"key yet.\nRun:  {_prog()} enrich --db {args.db}"
        )

    pairs = build_pairs(
        candidates,
        tolerance=args.tolerance,
        max_per_track=args.max_per_track,
        allow_same_artist=args.allow_same_artist,
        allow_half_double=not args.no_half_double,
    )

    data = payload(
        track_rows, pairs,
        tolerance=args.tolerance,
        max_per_track=args.max_per_track,
        allow_same_artist=args.allow_same_artist,
        include_half_double=not args.no_half_double,
        enriched_count=len(candidates),
    )
    out_path = render(data, args.out)

    print(f"{len(candidates)} of {len(track_rows)} track(s) have tempo and key.")
    if not pairs:
        print("No compatible pairs at this tolerance. Try a wider --tolerance "
              "(e.g. --tolerance 6), a higher --max-per-track, or --allow-same-artist.")
    else:
        half_double = sum(1 for pair in pairs if pair.ratio != 1.0)
        same_key = sum(1 for pair in pairs if pair.same_key)
        print(f"Found {len(pairs)} transitions "
              f"({same_key} same-key, {half_double} half/double time).")
    print(f"Wrote {os.path.abspath(out_path)} — open it in a browser.")
    return 0


# ---------------------------------------------------------------------- cli
def _prog() -> str:
    return "python -m accidental_dj"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_prog(),
        description="Find accidental DJ transitions in your Spotify Liked Songs.",
        epilog="Tempo and key data come from GetSongBPM (https://getsongbpm.com).",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=DEFAULT_DB,
                        help=f"SQLite cache path (default: {DEFAULT_DB})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", parents=[common],
                                 help="pull Liked Songs from Spotify into SQLite")
    sync.add_argument("--auth-cache", default=".spotify-cache",
                      help="where the OAuth token is cached (default: .spotify-cache)")
    sync.add_argument("--prune", action="store_true",
                      help="delete cached tracks that are no longer liked")
    sync.set_defaults(func=cmd_sync)

    enrich = subparsers.add_parser("enrich", parents=[common],
                                   help="look up tempo and key via GetSongBPM")
    enrich.add_argument("--retry-misses", action="store_true",
                        help="also retry tracks previously cached as not found or failed")
    enrich.add_argument("--limit", type=int, default=None,
                        help="stop after this many lookups (useful for a first test)")
    enrich.add_argument("--delay", type=float, default=1.3,
                        help="seconds between API requests (default: 1.3)")
    enrich.set_defaults(func=cmd_enrich)

    build = subparsers.add_parser("build", parents=[common],
                                  help="write the self-contained HTML page")
    build.add_argument("--out", default=DEFAULT_OUT,
                       help=f"output file (default: {DEFAULT_OUT})")
    build.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                       help="maximum tempo drift percent (default: %(default)s); "
                            "the page's slider filters within this")
    build.add_argument("--max-per-track", type=int, default=DEFAULT_MAX_PER_TRACK,
                       help="cap on how many pairs one track can appear in "
                            "(default: %(default)s, 0 for no cap)")
    build.add_argument("--allow-same-artist", action="store_true",
                       help="include pairs where both tracks share a primary artist")
    build.add_argument("--no-half-double", action="store_true",
                       help="exclude half-time and double-time matches")
    build.set_defaults(func=cmd_build)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
