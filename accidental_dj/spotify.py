"""Spotify Liked Songs reader.

Only GET /v1/me/tracks is used, with the user-library-read scope. The
audio-features and audio-analysis endpoints are deprecated (2024-11-27) and
403 for apps created after that date, so nothing here touches them.
"""

from __future__ import annotations

import os
from typing import Iterator

from .textnorm import primary_artist

SCOPE = "user-library-read"
PAGE_SIZE = 50
ENV_VARS = ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI")


class SpotifyConfigError(RuntimeError):
    pass


def make_client(cache_path: str = ".spotify-cache"):
    """Authenticated spotipy client, or a clear message about what is missing."""
    missing = [name for name in ENV_VARS if not os.environ.get(name)]
    if missing:
        raise SpotifyConfigError(
            "Missing environment variable(s): " + ", ".join(missing) + "\n"
            "Create an app at https://developer.spotify.com/dashboard, add a\n"
            "redirect URI (http://127.0.0.1:8888/callback works), then export:\n"
            "  export SPOTIPY_CLIENT_ID=...\n"
            "  export SPOTIPY_CLIENT_SECRET=...\n"
            "  export SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback"
        )
    try:
        from spotipy import Spotify
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError as exc:  # pragma: no cover
        raise SpotifyConfigError(
            "spotipy is not installed. Run: pip install -r requirements.txt"
        ) from exc

    kwargs = {"scope": SCOPE}
    try:
        # Preferred since spotipy 2.26; passing cache_path directly is deprecated.
        from spotipy.cache_handler import CacheFileHandler
        kwargs["cache_handler"] = CacheFileHandler(cache_path=cache_path)
    except ImportError:
        kwargs["cache_path"] = cache_path

    auth = SpotifyOAuth(**kwargs)
    return Spotify(auth_manager=auth, requests_timeout=30, retries=5)


def iter_liked_tracks(client) -> Iterator[dict]:
    """Every saved track, page by page, flattened to the columns we store."""
    page = client.current_user_saved_tracks(limit=PAGE_SIZE)
    while page:
        for item in page.get("items", []):
            row = flatten(item)
            if row:
                yield row
        page = client.next(page) if page.get("next") else None


def flatten(item: dict) -> dict | None:
    """One /v1/me/tracks item -> a tracks-table row. Skips local files."""
    track = (item or {}).get("track") or {}
    track_id = track.get("id")
    if not track_id or track.get("is_local"):
        return None

    artists = [a.get("name", "") for a in track.get("artists", []) if a.get("name")]
    album = track.get("album") or {}
    release_date = album.get("release_date") or ""
    year = None
    if len(release_date) >= 4 and release_date[:4].isdigit():
        year = int(release_date[:4])

    artist_names = ", ".join(artists)
    return {
        "id": track_id,
        "title": track.get("name") or "",
        "artist": artist_names,
        # artists[0] straight from the API, so band names with commas,
        # ampersands or slashes survive intact.
        "primary_artist": artists[0] if artists else primary_artist(artist_names),
        "album": album.get("name"),
        "release_year": year,
        "duration_ms": track.get("duration_ms"),
        "popularity": track.get("popularity"),
        "isrc": ((track.get("external_ids") or {}).get("isrc")),
        "added_at": item.get("added_at"),
    }
