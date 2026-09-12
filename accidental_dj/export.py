"""Write a set to a file you can import elsewhere.

Format follows the extension:

``.csv``   title/artist/album/year/bpm/key/ISRC/URL -- what Soundiiz and
           TuneMyMusic accept, and readable in a spreadsheet.
``.txt``   one Spotify URI per line. Select all, copy, then paste straight
           into a playlist in the Spotify desktop app.
``.m3u8``  a playlist file pointing at Spotify URLs, for players that take it.
"""

from __future__ import annotations

import csv
import os
from typing import Sequence

FORMATS = ("csv", "txt", "m3u8")


def format_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("m3u", "m3u8"):
        return "m3u8"
    if ext == "txt":
        return "txt"
    return "csv"


def write(path: str, tracks: Sequence[dict], name: str = "Accidental DJ") -> str:
    """Write the ordered tracks to ``path``. Returns the format used."""
    kind = format_for(path)
    if kind == "csv":
        _write_csv(path, tracks)
    elif kind == "txt":
        _write_txt(path, tracks)
    else:
        _write_m3u(path, tracks, name)
    return kind


def _url(track: dict) -> str:
    return f"https://open.spotify.com/track/{track['id']}"


def _write_csv(path: str, tracks: Sequence[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Position", "Title", "Artist", "Album", "Year",
                         "BPM", "Key", "ISRC", "Spotify URL"])
        for position, track in enumerate(tracks, start=1):
            writer.writerow([
                position, track.get("title", ""), track.get("artist", ""),
                track.get("album") or "", track.get("release_year") or "",
                track.get("bpm") or "", track.get("camelot") or "",
                track.get("isrc") or "", _url(track),
            ])


def _write_txt(path: str, tracks: Sequence[dict]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for track in tracks:
            handle.write(f"spotify:track:{track['id']}\n")


def _write_m3u(path: str, tracks: Sequence[dict], name: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#EXTM3U\n")
        handle.write(f"#PLAYLIST:{name}\n")
        for track in tracks:
            seconds = int((track.get("duration_ms") or 0) / 1000) or -1
            handle.write(f"#EXTINF:{seconds},{track.get('artist','')} - "
                         f"{track.get('title','')}\n")
            handle.write(_url(track) + "\n")
