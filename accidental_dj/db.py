"""SQLite cache for the library and its tempo/key lookups."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    artist         TEXT NOT NULL,
    primary_artist TEXT NOT NULL,
    album          TEXT,
    release_year   INTEGER,
    duration_ms    INTEGER,
    popularity     INTEGER,
    isrc           TEXT,
    added_at       TEXT,
    synced_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio (
    track_id     TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    status       TEXT NOT NULL,          -- ok | no_match | error
    tempo        REAL,
    key_raw      TEXT,
    camelot      TEXT,
    time_sig     TEXT,
    match_title  TEXT,
    match_artist TEXT,
    query        TEXT,
    detail       TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    checked_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS audio_status ON audio(status);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(primary_artist);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_track(conn: sqlite3.Connection, track: dict) -> None:
    conn.execute(
        """
        INSERT INTO tracks (id, title, artist, primary_artist, album, release_year,
                            duration_ms, popularity, isrc, added_at, synced_at)
        VALUES (:id, :title, :artist, :primary_artist, :album, :release_year,
                :duration_ms, :popularity, :isrc, :added_at, :synced_at)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, artist=excluded.artist,
            primary_artist=excluded.primary_artist, album=excluded.album,
            release_year=excluded.release_year, duration_ms=excluded.duration_ms,
            popularity=excluded.popularity, isrc=excluded.isrc,
            added_at=excluded.added_at, synced_at=excluded.synced_at
        """,
        track,
    )


def record_audio(conn: sqlite3.Connection, track_id: str, status: str, *,
                 tempo: Optional[float] = None, key_raw: Optional[str] = None,
                 camelot: Optional[str] = None, time_sig: Optional[str] = None,
                 match_title: Optional[str] = None, match_artist: Optional[str] = None,
                 query: Optional[str] = None, detail: Optional[str] = None) -> None:
    """Write one lookup result. Failures are cached too, so re-runs skip them."""
    conn.execute(
        """
        INSERT INTO audio (track_id, status, tempo, key_raw, camelot, time_sig,
                           match_title, match_artist, query, detail, attempts, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            status=excluded.status, tempo=excluded.tempo, key_raw=excluded.key_raw,
            camelot=excluded.camelot, time_sig=excluded.time_sig,
            match_title=excluded.match_title, match_artist=excluded.match_artist,
            query=excluded.query, detail=excluded.detail,
            attempts=audio.attempts + 1, checked_at=excluded.checked_at
        """,
        (track_id, status, tempo, key_raw, camelot, time_sig,
         match_title, match_artist, query, detail, now()),
    )


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def count(conn: sqlite3.Connection, sql: str, args: Iterable = ()) -> int:
    return int(conn.execute(sql, tuple(args)).fetchone()[0])


def pending_tracks(conn: sqlite3.Connection, retry_misses: bool = False,
                   limit: Optional[int] = None) -> list[sqlite3.Row]:
    """Tracks with no cached lookup yet, or all unresolved ones with --retry-misses."""
    where = "a.track_id IS NULL" if not retry_misses else "a.track_id IS NULL OR a.status <> 'ok'"
    sql = f"""
        SELECT t.* FROM tracks t
        LEFT JOIN audio a ON a.track_id = t.id
        WHERE {where}
        ORDER BY t.added_at DESC, t.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def all_tracks_with_audio(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.*, a.status, a.tempo, a.key_raw, a.camelot, a.time_sig
        FROM tracks t LEFT JOIN audio a ON a.track_id = t.id
        ORDER BY t.added_at DESC, t.id
        """
    ).fetchall()
