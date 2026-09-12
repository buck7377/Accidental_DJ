"""Command line entry point: sync -> enrich -> build."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from . import db, display, export
from .matching import (DEFAULT_MAX_PER_TRACK, DEFAULT_SEARCH_BUDGET,
                       DEFAULT_TOLERANCE, build_pairs, candidates_from_rows,
                       longest_chain, pair_lookup, ratio_label)
from .textnorm import clean_title, fold

DEFAULT_DB = "library.db"


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


def _dedupe(track_rows: list[dict]) -> tuple[list[dict], int]:
    """Drop repeats of the same song saved under more than one Spotify id.

    A single and an album release of one track are different ids, so the
    chain would otherwise treat them as two songs and a set could play the
    same thing twice. Compared on cleaned title plus primary artist, which
    also collapses a live or remastered cut into the original.
    """
    seen: set[tuple[str, str]] = set()
    kept, dropped = [], 0
    for row in track_rows:
        key = (fold(clean_title(row["title"])), fold(row["primary_artist"]))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, dropped


def _load_pairs(args: argparse.Namespace, dedupe: bool = False):
    """Rows, candidates and pairs for the given matching flags.

    Returns (conn, track_rows, candidates, pairs, dropped) or an error string.
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

    dropped = 0
    if dedupe:
        track_rows, dropped = _dedupe(track_rows)
        # Indexes must stay in step with the list they point into.
        for position, row in enumerate(track_rows):
            row["index"] = position

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
    return conn, track_rows, candidates, pairs, dropped


# ----------------------------------------------------------------- playlist
def cmd_playlist(args: argparse.Namespace) -> int:
    from .spotify import (SCOPE_PLAYLIST_PRIVATE, SCOPE_PLAYLIST_PUBLIC,
                          SpotifyConfigError, create_playlist, make_client)

    if args.tolerance <= 0:
        return _err("--tolerance must be greater than 0.")

    loaded = _load_pairs(args, dedupe=True)
    if isinstance(loaded, str):
        return _err(loaded)
    _conn, track_rows, _candidates, pairs, dropped = loaded
    if dropped:
        print(f"Ignored {dropped} duplicate track(s) saved under more than one "
              "Spotify id, so the set cannot play the same song twice.")

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

    color = display.use_color(False if args.no_color else None)
    by_endpoints = pair_lookup(pairs)
    ordered = [track_rows[i] for i in chain]

    print(f"Longest continuous set: {len(chain)} tracks\n")
    for position, index in enumerate(chain, start=1):
        print(display.set_line(position, track_rows[index], color))
        if position < len(chain):
            nxt = chain[position]
            pair = by_endpoints.get((min(index, nxt), max(index, nxt)))
            if pair:
                label = ratio_label(pair.ratio)
                note = f"↓ {pair.drift:.2f}% drift" + (f", {label}" if label else "")
                print("            " + display.dim(note, color))

    if args.export:
        try:
            kind = export.write(args.export, ordered, name=args.name)
        except OSError as exc:
            return _err(f"Could not write {args.export}: {exc}")
        print(f"\nWrote {os.path.abspath(args.export)} ({kind}).")
        if kind == "txt":
            print("Import: open the file, select all, copy, then paste into an empty "
                  "playlist in the Spotify desktop app.")
        elif kind == "csv":
            print("Import: upload it at soundiiz.com or tunemymusic.com, or open it "
                  "in a spreadsheet.")

    if args.dry_run:
        print("\nNothing was written to Spotify (--dry-run).")
        return 0

    if args.export and args.export_only:
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


# -------------------------------------------------------------- transitions
def _matches_search(pair, tracks, terms: list[str]) -> bool:
    """Every term must appear on one side or the other."""
    if not terms:
        return True
    haystack = " ".join(
        fold(f"{tracks[i]['title']} {tracks[i]['artist']} {tracks[i]['release_year']} "
             f"{tracks[i]['camelot']}")
        for i in (pair.a, pair.b)
    )
    return all(term in haystack for term in terms)


def cmd_transitions(args: argparse.Namespace) -> int:
    if args.tolerance <= 0:
        return _err("--tolerance must be greater than 0.")

    loaded = _load_pairs(args)
    if isinstance(loaded, str):
        return _err(loaded)
    _conn, track_rows, candidates, pairs, _dropped = loaded

    terms = [fold(t) for t in (args.search or "").split() if t.strip()]
    shown = [
        pair for pair in pairs
        if (args.max_drift is None or pair.drift <= args.max_drift)
        and (not args.same_key_only or pair.same_key)
        and _matches_search(pair, track_rows, terms)
    ]

    color = display.use_color(False if args.no_color else None)
    print(f"{len(candidates)} of {len(track_rows)} track(s) have tempo and key.")
    if not pairs:
        print("No compatible pairs at this tolerance. Try a wider --tolerance "
              "(e.g. --tolerance 6), a higher --max-per-track, or --allow-same-artist.")
        return 0
    if not shown:
        print(f"None of the {len(pairs)} transitions match those filters. Loosen "
              "--max-drift, drop --same-key-only, or clear --search.")
        return 0

    limit = len(shown) if args.limit == 0 else min(args.limit, len(shown))
    print(f"Showing {limit} of {len(shown)} transitions, tightest first.\n")
    for pair in shown[:limit]:
        print(display.transition_line(pair, track_rows, color))
    if limit < len(shown):
        print(f"\n{len(shown) - limit} more. Use --limit 0 to print them all.")
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

    trans = subparsers.add_parser(
        "transitions", parents=[common, matching],
        help="list the transitions found, tightest first")
    trans.add_argument("--search", default="",
                       help="only pairs matching these words (title, artist, year, key)")
    trans.add_argument("--max-drift", type=float, default=None,
                       help="hide pairs whose tempo drift exceeds this percent")
    trans.add_argument("--same-key-only", action="store_true",
                       help="only pairs in the identical Camelot key")
    trans.add_argument("--limit", type=int, default=40,
                       help="how many to print (default: %(default)s, 0 for all)")
    trans.add_argument("--no-color", action="store_true", help="disable colour output")
    trans.set_defaults(func=cmd_transitions)

    playlist = subparsers.add_parser(
        "playlist", parents=[common, matching],
        help="create a Spotify playlist that plays as one continuous set")
    playlist.add_argument("--name", default="Accidental DJ",
                          help="playlist name (default: %(default)s)")
    playlist.add_argument("--limit", type=int, default=None,
                          help="use at most this many tracks from the chain")
    playlist.add_argument("--public", action="store_true",
                          help="make the playlist public (private by default)")
    playlist.add_argument("--export", metavar="FILE", default=None,
                          help="write the set to a file (.csv, .txt of Spotify URIs, "
                               "or .m3u8) as well as creating it on Spotify")
    playlist.add_argument("--export-only", action="store_true",
                          help="with --export, write the file and skip Spotify entirely")
    playlist.add_argument("--dry-run", action="store_true",
                          help="print the set without writing anything to Spotify")
    playlist.add_argument("--no-color", action="store_true", help="disable colour output")
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
