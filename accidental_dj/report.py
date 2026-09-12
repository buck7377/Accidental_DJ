"""Render the self-contained HTML page."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Sequence

from .matching import Pair, ratio_label

TEMPLATE = os.path.join(os.path.dirname(__file__), "template.html")


def payload(track_rows: Sequence[dict], pairs: Sequence[Pair], *, tolerance: float,
            max_per_track: int, allow_same_artist: bool,
            include_half_double: bool, enriched_count: int) -> dict:
    return {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "params": {
            "tolerance": tolerance,
            "max_per_track": max_per_track,
            "allow_same_artist": allow_same_artist,
            "include_half_double": include_half_double,
            "track_count": len(track_rows),
            "enriched_count": enriched_count,
        },
        "tracks": [
            {
                "i": row["index"],
                "t": row["title"],
                "a": row["artist"],
                "al": row["album"],
                "y": row["release_year"],
                "bpm": round(float(row["bpm"]), 2) if row.get("bpm") else None,
                "cam": row.get("camelot"),
                "sig": row.get("time_sig"),
                "d": row.get("duration_ms"),
                "p": row.get("popularity"),
            }
            for row in track_rows
        ],
        "pairs": [
            {
                "a": pair.a,
                "b": pair.b,
                "drift": round(pair.drift, 2),
                "label": ratio_label(pair.ratio),
                "same": pair.same_key,
            }
            for pair in pairs
        ],
    }


def render(data: dict, out_path: str) -> str:
    with open(TEMPLATE, "r", encoding="utf-8") as handle:
        template = handle.read()

    # Escaping "<" keeps a track called "</script>" from breaking out of the
    # JSON island; it stays valid JSON either way.
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = template.replace("{{DATA}}", embedded)

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return out_path
