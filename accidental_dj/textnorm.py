"""Turn Spotify track metadata into something GetSongBPM can find.

Spotify titles carry a lot of release noise ("- 2011 Remaster", "(Live at
Budokan)", "feat. Someone") that a BPM database has never heard of. We strip
that noise before searching, and we search with the primary artist only.
"""

from __future__ import annotations

import re

# Words that mark a parenthetical / dash suffix as release noise rather than
# part of the actual song title. A remix is a different recording, so "remix"
# is deliberately NOT in here.
_NOISE_WORDS = (
    r"remaster(?:ed)?", r"re-?master(?:ed)?", r"remastered\s*version",
    r"live(?:\s+(?:at|from|in|on)\b.*)?", r"radio\s*edit", r"radio\s*version",
    r"single\s*(?:version|edit)", r"album\s*version", r"original\s*version",
    r"deluxe(?:\s*edition)?", r"expanded(?:\s*edition)?", r"bonus\s*track",
    r"mono(?:\s*version)?", r"stereo(?:\s*version)?", r"explicit(?:\s*version)?",
    r"clean(?:\s*version)?", r"re-?recorded(?:\s*version)?", r"anniversary(?:\s*edition)?",
    r"digital\s*remaster",
    r"\d{4}\s*(?:remaster(?:ed)?|mix|version|edition|digital\s*remaster)",
    r"(?:remaster(?:ed)?|mix|version|edition|remix)\s*\d{4}",
    r"feat\.?.*", r"ft\.?.*",
    r"featuring.*", r"with\s+[^)\]]+", r"\d{4}", r"from\s+[^)\]]+soundtrack",
    r"taken\s+from[^)\]]*",
)
_NOISE_RE = re.compile(r"^(?:" + "|".join(_NOISE_WORDS) + r")$", re.IGNORECASE)

_BRACKETED = re.compile(r"[\(\[]([^\(\)\[\]]*)[\)\]]")
_DASH_SUFFIX = re.compile(r"\s+[-–—]\s+([^-–—]+)$")
_FEAT_INLINE = re.compile(r"\s*(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
# Only splits on explicit collaboration markers. Sync stores Spotify's
# artists[0] as the primary artist, so a single name reaches us intact --
# and splitting on ",", "&" or "/" would maim "Earth, Wind & Fire",
# "Simon & Garfunkel" and "AC/DC".
_ARTIST_SPLIT = re.compile(
    r"\s*(?:\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b)\s*", re.IGNORECASE
)
_COLLAPSE = re.compile(r"\s{2,}")


def clean_title(title: str) -> str:
    """Strip remaster/live/edit/feat./bare-year noise from a track title."""
    if not title:
        return ""
    text = title

    # Drop noisy parentheticals and brackets, keep meaningful ones (e.g. remix).
    def _strip_bracket(match: re.Match[str]) -> str:
        return "" if _NOISE_RE.match(match.group(1).strip()) else match.group(0)

    for _ in range(3):  # titles can stack two or three of these
        new = _BRACKETED.sub(_strip_bracket, text)
        if new == text:
            break
        text = new

    # Drop noisy " - Remastered 2011" style suffixes, repeatedly.
    for _ in range(3):
        match = _DASH_SUFFIX.search(text)
        if not match or not _NOISE_RE.match(match.group(1).strip()):
            break
        text = text[: match.start()]

    text = _FEAT_INLINE.sub("", text)
    text = _COLLAPSE.sub(" ", text).strip(" -–—,")
    return text or title.strip()


def primary_artist(artist: str) -> str:
    """First named artist only: ``'Kanye West, Jay-Z'`` -> ``'Kanye West'``."""
    if not artist:
        return ""
    first = _ARTIST_SPLIT.split(artist.strip(), maxsplit=1)[0]
    return first.strip(" ,;&/-") or artist.strip()


def fold(text: str) -> str:
    """Loose comparison form used for fuzzy matching and same-artist checks."""
    text = (text or "").lower()
    text = re.sub(r"[‘’“”']", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()
