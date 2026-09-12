"""Command line entry point: sync -> enrich -> build."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from . import db
from .matching import (DEFAULT_MAX_PER_TRACK, DEFAULT_SEARCH_BUDGET,
                       DEFAULT_TOLERANCE, build_pairs,
                       candidates_from_rows, longest_chain, pair_lookup,
                       ratio_label)
from .report import payload, render

DEFAULT_DB = "library.db"
DEFAULT_OUT = "transitions.html"


def _err(message: str) -> int:
    print(f"\nerror: {message}", file=sys.stderr)
    return 1


def _partial_note(conn) -> None:
    """Flag rows that resolved with only half the data, so build's smaller
    count is never a surprise. GetSongBPM sometimes has a tempo but an empty
    key_of (or vice versa)."""
    partial = db.count(
        conn,
        "SELECT COUNT(*) FROM audio WHERE status = 'ok' "
        "AND (camelot IS NULL OR tempo IS NULL)",
    )
    if partial:
        print(f"{partial} resolved track(s) have only partial data (GetSongBPM is "
              "missing the key or tempo). They show up under All tracks but "
              "cannot be paired.")


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
        _partial_note(conn)
        if misses:
            print("Use --retry-misses to try the misses again.")
        return 0

    total = len(pending)
    print(f"Enriching {total} track(s) from GetSongBPM at ~{args.delay}s per request "
          f"(about {_fmt_duration(total * args.delay)}). Ctrl-C is safe -- progress "
          "is committed after every track.", flush=True)

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
            # flush: stdout is block-buffered when redirected to a file or
            # piped, and a progress line nobody sees until the end is useless.
            print(f"[{index:>4}/{total}] {mark}  {track['title'][:38]} — "
                  f"{track['primary_artist'][:24]}   eta {_fmt_duration(eta)}",
                  flush=True)
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
    _partial_note(conn)
    if counts["no_match"] or counts["error"]:
        print("Misses are cached so re-runs skip them. Retry with --retry-misses.")
    remaining = len(db.pending_tracks(conn))
    if remaining:
        print(f"{remaining} track(s) still unenriched. Run enrich again to continue.")
    else:
        print(f"Next: {_prog()} build --db {args.db}")
    return 0


def _load_pairs(args: argparse.Namespace):
    """Rows, candidates and pairs for the given matching flags.

    Returns (conn, track_rows, candidates, pairs) or an error string.
    """
    conn = db.connect(args.db)
    rows = db.all_tracks_with_audio(conn)
    if not rows:
        return f"No tracks in {args.db}. Run:  {_prog()} sync --db {args.db}"

    track_rows = []
    for index, row in enumerate(rows):
        data = dict(row)
        data["index"] = index
        data["bpm"] = data.pop("tempo", None)
        track_rows.append(data)

    candidates = candidates_from_rows(track_rows)
    if not candidates:
        return (f"None of the {len(track_rows)} track(s) have both a tempo and a "
                f"readable key yet.\nRun:  {_prog()} enrich --db {args.db}")

    pairs = build_pairs(
        candidates,
        tolerance=args.tolerance,
        max_per_track=args.max_per_track,
        allow_same_artist=args.allow_same_artist,
        allow_half_double=not args.no_half_double,
    )
    return conn, track_rows, candidates, pairs


# ----------------------------------------------------------------- playlist
def cmd_playlist(args: argparse.Namespace) -> int:
    from .spotify import (SCOPE_PLAYLIST_PRIVATE, SCOPE_PLAYLIST_PUBLIC,
                          SpotifyConfigError, create_playlist, make_client)

    if args.tolerance <= 0:
        return _err("--tolerance must be greater than 0.")

    loaded = _load_pairs(args)
    if isinstance(loaded, str):
        return _err(loaded)
    _conn, track_rows, _candidates, pairs = loaded

    if not pairs:
        return _err("No compatible pairs at this tolerance, so there is nothing to "
                    "chain. Try a wider --tolerance (e.g. --tolerance 6) or a higher "
                    "--max-per-track.")

    chain = longest_chain(pairs, max_steps=args.search_budget)
    if args.limit and len(chain) > args.limit:
        chain = chain[:args.limit]
    if len(chain) < 2:
        return _err("Could not find a chain of two or more tracks. Try a wider "
                    "--tolerance or a higher --max-per-track.")

    by_endpoints = pair_lookup(pairs)
    print(f"Longest continuous set: {len(chain)} tracks\n")
    for position, index in enumerate(chain, start=1):
        track = track_rows[index]
        print(f"{position:>3}. {float(track['bpm']):6.1f} {str(track['camelot']):>3}  "
              f"{track['title'][:42]} — {track['primary_artist'][:24]}")
        if position < len(chain):
            nxt = chain[position]
            pair = by_endpoints.get((min(index, nxt), max(index, nxt)))
            if pair:
                label = ratio_label(pair.ratio)
                print(f"      {'':>6} {'':>3}  \u2193 {pair.drift:.2f}% drift"
                      + (f", {label}" if label else ""))

    if args.dry_run:
        print("\n--dry-run: nothing was written to Spotify.")
        return 0

    print(f"\nThis will create a {'PUBLIC' if args.public else 'private'} playlist "
          f"called {args.name!r} with {len(chain)} tracks on your Spotify account.")
    if not args.yes:
        try:
            answer = input("Create it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Cancelled. Nothing was written.")
            return 0

    scope = SCOPE_PLAYLIST_PUBLIC if args.public else SCOPE_PLAYLIST_PRIVATE
    try:
        client = make_client(args.auth_cache, scope=scope)
    except SpotifyConfigError as exc:
        return _err(str(exc))

    try:
        url = create_playlist(
            client, args.name, [track_rows[i]["id"] for i in chain],
            public=args.public,
            description=("Continuous key- and tempo-matched set built by Accidental DJ. "
                         "Tempo and key data from GetSongBPM."),
        )
    except Exception as exc:
        message = str(exc)
        if "403" in message or "scope" in message.lower():
            message += ("\nThe cached token predates the playlist scope. Delete it and "
                        f"re-authorize:  rm {args.auth_cache}")
        return _err(f"Could not create the playlist: {message}")

    print(f"\nCreated: {url}")
    return 0


# -------------------------------------------------------------------- build
def cmd_build(args: argparse.Namespace) -> int:
    if args.tolerance <= 0:
        return _err("--tolerance must be greater than 0.")

    loaded = _load_pairs(args)
    if isinstance(loaded, str):
        return _err(loaded)
    _conn, track_rows, candidates, pairs = loaded

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

    matching = argparse.ArgumentParser(add_help=False)
    matching.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                          help="maximum tempo drift percent (default: %(default)s)")
    matching.add_argument("--max-per-track", type=int, default=DEFAULT_MAX_PER_TRACK,
                          help="cap on how many pairs one track can appear in "
                               "(default: %(default)s, 0 for no cap)")
    matching.add_argument("--allow-same-artist", action="store_true",
                          help="include pairs where both tracks share a primary artist")
    matching.add_argument("--no-half-double", action="store_true",
                          help="exclude half-time and double-time matches")

    build = subparsers.add_parser("build", parents=[common, matching],
                                  help="write the self-contained HTML page")
    build.add_argument("--out", default=DEFAULT_OUT,
                       help=f"output file (default: {DEFAULT_OUT})")
    build.set_defaults(func=cmd_build)

    playlist = subparsers.add_parser(
        "playlist", parents=[common, matching],
        help="create a Spotify playlist that plays as one continuous set")
    playlist.add_argument("--name", default="Accidental DJ",
                          help="playlist name (default: %(default)s)")
    playlist.add_argument("--limit", type=int, default=None,
                          help="use at most this many tracks from the chain")
    playlist.add_argument("--public", action="store_true",
                          help="make the playlist public (private by default)")
    playlist.add_argument("--dry-run", action="store_true",
                          help="print the set without writing anything to Spotify")
    playlist.add_argument("--yes", action="store_true",
                          help="skip the confirmation prompt")
    playlist.add_argument("--search-budget", type=int, default=DEFAULT_SEARCH_BUDGET,
                          help="how hard to search for a long chain "
                               "(default: %(default)s steps)")
    playlist.add_argument("--auth-cache", default=".spotify-cache",
                          help="where the OAuth token is cached (default: .spotify-cache)")
    playlist.set_defaults(func=cmd_playlist)
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
