"""GetSongBPM API client.

Spotify's audio-features and audio-analysis endpoints were deprecated on
2024-11-27 and return 403 to any app created after that date, so tempo, key
and time signature come from GetSongBPM instead.

Their terms require a visible credit link back to getsongbpm.com wherever the
data is displayed -- the generated page carries it in the footer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional

import requests

from .camelot import to_camelot
from .textnorm import clean_title, fold, primary_artist

API_ROOT = "https://api.getsong.co"
BACKLINK = "https://getsongbpm.com"
# Their published ceiling is 3,000 requests/hour (~1.2s each), so 1.3s stays
# comfortably inside it.
DEFAULT_DELAY = 1.3
TITLE_THRESHOLD = 0.72
ARTIST_THRESHOLD = 0.60
# Bands are often credited more fully on one side than the other
# ("Buck Owens" vs "Buck Owens and His Buckaroos"). Treat the longer name as
# the same artist only when the extra part starts with a joining word, so
# "Drake" never absorbs "Drake Bell".
_ARTIST_TAIL = re.compile(r"^(?:and|with|his|her|their|the|featuring|feat|ft)\b")
_REMIX_MARKER = re.compile(r"\b(?:remix|rmx|bootleg|flip|mashup)\b", re.IGNORECASE)


class GetSongBPMError(RuntimeError):
    """Raised for problems that will not fix themselves on the next track."""


@dataclass
class Lookup:
    status: str                      # ok | no_match | error
    tempo: Optional[float] = None
    key_raw: Optional[str] = None
    camelot: Optional[str] = None
    time_sig: Optional[str] = None
    match_title: Optional[str] = None
    match_artist: Optional[str] = None
    query: Optional[str] = None
    detail: Optional[str] = None


class RateLimiter:
    """Keeps at least ``delay`` seconds between consecutive requests."""

    def __init__(self, delay: float = DEFAULT_DELAY) -> None:
        self.delay = delay
        # None rather than 0.0: time.monotonic()'s epoch is arbitrary, so a
        # first reading of exactly 0.0 must not read as "no request yet".
        self._last: Optional[float] = None

    def wait(self) -> None:
        if self._last is not None:
            gap = time.monotonic() - self._last
            if gap < self.delay:
                time.sleep(self.delay - gap)
        self._last = time.monotonic()


def _score(a: str, b: str) -> float:
    return SequenceMatcher(None, fold(a), fold(b)).ratio()


def _artist_score(mine: str, theirs: str) -> float:
    """Similarity, but tolerant of one side carrying a fuller band credit."""
    base = _score(mine, theirs)
    a, b = fold(mine), fold(theirs)
    if a and b and a != b:
        short, long = (a, b) if len(a) < len(b) else (b, a)
        if long.startswith(short + " ") and _ARTIST_TAIL.match(long[len(short) + 1:]):
            return max(base, 0.95)
    return base


class GetSongBPMClient:
    def __init__(self, api_key: str, delay: float = DEFAULT_DELAY, timeout: float = 20.0) -> None:
        if not api_key:
            raise GetSongBPMError(
                "No GetSongBPM API key. Get a free key at "
                "https://getsongbpm.com/api and export GETSONGBPM_API_KEY."
            )
        self.api_key = api_key
        self.timeout = timeout
        self.limiter = RateLimiter(delay)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "accidental-dj/1.0 (+https://getsongbpm.com)"

    # -- HTTP -------------------------------------------------------------
    def _get(self, path: str, params: dict[str, str]) -> Any:
        params = {"api_key": self.api_key, **params}
        last_error: Optional[str] = None

        for attempt in range(3):
            self.limiter.wait()
            try:
                response = self.session.get(
                    f"{API_ROOT}{path}", params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                continue

            if response.status_code in (401, 403):
                raise GetSongBPMError(
                    f"GetSongBPM rejected the API key (HTTP {response.status_code}). "
                    "Check GETSONGBPM_API_KEY, and that the domain registered with "
                    "your key is the one you requested it for."
                )
            if response.status_code == 429:
                backoff = 5.0 * (attempt + 1)
                last_error = "rate limited (HTTP 429)"
                time.sleep(backoff)
                continue
            if response.status_code >= 500:
                last_error = f"server error (HTTP {response.status_code})"
                time.sleep(2.0 * (attempt + 1))
                continue
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}"}

            try:
                return response.json()
            except ValueError:
                last_error = "response was not JSON"
                continue

        return {"error": last_error or "request failed"}

    # -- Lookups ----------------------------------------------------------
    def lookup(self, title: str, artist: str) -> Lookup:
        """Find tempo/key/time signature for one track."""
        search_title = clean_title(title)
        search_artist = primary_artist(artist)
        query = f"song:{search_title} artist:{search_artist}"

        if not search_title or not search_artist:
            return Lookup("no_match", query=query, detail="empty title or artist after cleaning")

        payload = self._get("/search/", {"type": "both", "lookup": query})
        results, problem = _unwrap(payload, "search")
        if problem:
            status = "no_match" if problem == "no result" else "error"
            return Lookup(status, query=query, detail=problem)
        if not results:
            return Lookup("no_match", query=query, detail="no result")

        best, score = self._best_match(results, search_title, search_artist)
        if best is None:
            top = results[0]
            return Lookup(
                "no_match", query=query,
                detail=f"no confident match (best score {score:.2f}, "
                       f"top result {top.get('title')!r} by "
                       f"{(top.get('artist') or {}).get('name')!r})",
            )

        tempo, key_raw, time_sig = _extract(best)
        if tempo is None or key_raw is None:
            # Search hits sometimes omit the analysis; the song endpoint has it.
            song_id = best.get("id") or best.get("song_id")
            if song_id:
                detail_payload = self._get("/song/", {"id": str(song_id)})
                song, problem = _unwrap(detail_payload, "song")
                if isinstance(song, list):
                    song = song[0] if song else None
                if song and not problem:
                    d_tempo, d_key, d_sig = _extract(song)
                    tempo = tempo if tempo is not None else d_tempo
                    key_raw = key_raw if key_raw is not None else d_key
                    time_sig = time_sig or d_sig

        matched_artist = (best.get("artist") or {}).get("name")
        if tempo is None and key_raw is None:
            return Lookup(
                "no_match", query=query, match_title=best.get("title"),
                match_artist=matched_artist,
                detail="matched a song but it has no tempo or key on record",
            )

        return Lookup(
            "ok", tempo=tempo, key_raw=key_raw, camelot=to_camelot(key_raw),
            time_sig=time_sig, match_title=best.get("title"),
            match_artist=matched_artist, query=query,
        )

    def _best_match(self, results: list[dict], title: str, artist: str):
        """Best candidate that clears both thresholds, or None.

        Their titles get the same noise stripping as ours before comparison --
        otherwise "Rover 2.0" never matches "Rover 2.0 (feat. 21 Savage)",
        which is the same recording. A remix is not, so a remix on one side
        only disqualifies the candidate outright: its tempo is not this song's.
        """
        wanted_remix = bool(_REMIX_MARKER.search(title))
        best, best_score = None, 0.0
        runner_up = 0.0

        for item in results:
            if not isinstance(item, dict):
                continue
            raw_title = item.get("title") or ""
            item_artist = (item.get("artist") or {}).get("name") or ""
            if bool(_REMIX_MARKER.search(raw_title)) != wanted_remix:
                continue

            item_title = clean_title(raw_title)
            title_score = max(_score(title, item_title), _score(title, raw_title))
            artist_score = _artist_score(artist, item_artist)
            combined = title_score * 0.6 + artist_score * 0.4
            runner_up = max(runner_up, combined)

            if title_score >= TITLE_THRESHOLD and artist_score >= ARTIST_THRESHOLD \
                    and combined > best_score:
                best, best_score = item, combined

        return (best, best_score) if best else (None, runner_up)


def _unwrap(payload: Any, key: str):
    """Pull ``payload[key]`` out, normalising GetSongBPM's error shapes."""
    if payload is None:
        return None, "empty response"
    if isinstance(payload, dict):
        if "error" in payload and payload["error"]:
            return None, str(payload["error"])
        value = payload.get(key)
        if isinstance(value, dict) and value.get("error"):
            return None, str(value["error"])
        if isinstance(value, dict):
            return value, None
        if isinstance(value, list):
            return value, None
        if value is None:
            return None, "unexpected response shape"
    return None, "unexpected response shape"


def _extract(item: dict):
    """Tempo, key and time signature out of a search hit or song record.

    GetSongBPM returns these as strings: ``"tempo": "220"``,
    ``"key_of": "Em"``, ``"time_sig": "4/4"``. The alternate spellings are
    tolerated in case a response shape differs.
    """
    tempo = item.get("tempo")
    try:
        tempo = float(tempo) if tempo not in (None, "", "0") else None
    except (TypeError, ValueError):
        tempo = None
    key_raw = item.get("key_of") or item.get("key") or None
    if isinstance(key_raw, str) and not key_raw.strip():
        key_raw = None
    time_sig = item.get("time_sig") or item.get("time_signature") or None
    return tempo, key_raw, time_sig
