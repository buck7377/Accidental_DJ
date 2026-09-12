"""Pair tracks by key and tempo compatibility -- and by nothing else.

This module is deliberately blind to genre, mood, era, popularity and any
notion of "similar music". A 1968 country record sitting a fraction of a BPM
from a 2017 trap song is exactly the result we want, so the only inputs to
ranking are tempo drift and whether the keys are identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .camelot import compatible_codes, keys_compatible
from .textnorm import fold

DEFAULT_TOLERANCE = 3.0
DEFAULT_MAX_PER_TRACK = 6


@dataclass(frozen=True)
class Candidate:
    """The only facts the matcher is allowed to see about a track."""

    index: int
    track_id: str
    bpm: float
    camelot: str
    artist_key: str


@dataclass(frozen=True)
class Pair:
    a: int
    b: int
    drift: float
    ratio: float  # multiplier applied to B's tempo to reach A's
    same_key: bool


def drift_percent(a: float, b: float) -> float:
    """Symmetric difference between two tempos, as a percentage of their mean."""
    if a <= 0 or b <= 0:
        return float("inf")
    return abs(a - b) / ((a + b) / 2.0) * 100.0


def tempo_match(bpm_a: float, bpm_b: float, tolerance: float,
                allow_half_double: bool = True) -> Optional[tuple[float, float]]:
    """Best (drift, ratio) for two tempos, or None if they never line up.

    ``ratio`` is the multiplier applied to B to land on A, so ``bpm_a`` is
    close to ``bpm_b * ratio``: 1.0 is a straight mix, 2.0 means B runs at
    half A's tempo, 0.5 means B runs at double. A direct match always wins
    over a half/double reading of the same two numbers.
    """
    if bpm_a <= 0 or bpm_b <= 0:
        return None

    options = [(drift_percent(bpm_a, bpm_b), 1.0)]
    if allow_half_double:
        options.append((drift_percent(bpm_a, bpm_b * 2.0), 2.0))
        options.append((drift_percent(bpm_a, bpm_b / 2.0), 0.5))

    viable = [opt for opt in options if opt[0] <= tolerance]
    if not viable:
        return None
    # Sort puts the tightest first; ratio 1.0 breaks a tie in favour of a
    # straight mix over a half/double read of the same two numbers.
    viable.sort(key=lambda opt: (opt[0], abs(opt[1] - 1.0)))
    return viable[0]


def ratio_label(ratio: float) -> str:
    """Human label for a half/double-time match, empty for a straight one."""
    if ratio == 2.0:
        return "half-time"
    if ratio == 0.5:
        return "double-time"
    return ""


def build_pairs(
    candidates: Sequence[Candidate],
    tolerance: float = DEFAULT_TOLERANCE,
    max_per_track: int = DEFAULT_MAX_PER_TRACK,
    allow_same_artist: bool = False,
    allow_half_double: bool = True,
) -> list[Pair]:
    """Every key- and tempo-compatible pair, ranked and capped.

    Ranking is drift ascending, then same-key pairs ahead of merely compatible
    ones. Nothing else feeds in. The cap is applied greedily down that ranking
    so one song in a crowded key and tempo cannot flood the list.
    """
    by_code: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_code.setdefault(candidate.camelot, []).append(candidate)

    pairs: list[Pair] = []
    seen: set[tuple[int, int]] = set()

    for candidate in candidates:
        # Only four Camelot codes can ever match, so we never look at the rest.
        for code in compatible_codes(candidate.camelot):
            for other in by_code.get(code, ()):
                if other.index == candidate.index:
                    continue
                key = (min(candidate.index, other.index), max(candidate.index, other.index))
                if key in seen:
                    continue
                if not allow_same_artist and candidate.artist_key and \
                        candidate.artist_key == other.artist_key:
                    seen.add(key)
                    continue
                match = tempo_match(candidate.bpm, other.bpm, tolerance, allow_half_double)
                seen.add(key)
                if match is None:
                    continue
                drift, ratio = match
                # a is always the outer-loop track, which is the orientation
                # `ratio` was measured in. Dedupe keeps each pair to one
                # orientation; an accidental transition reads either way.
                pairs.append(
                    Pair(
                        a=candidate.index,
                        b=other.index,
                        drift=round(drift, 4),
                        ratio=ratio,
                        same_key=candidate.camelot == other.camelot,
                    )
                )

    pairs.sort(key=lambda pair: (pair.drift, not pair.same_key, pair.a, pair.b))

    if max_per_track and max_per_track > 0:
        appearances: dict[int, int] = {}
        capped: list[Pair] = []
        for pair in pairs:
            if appearances.get(pair.a, 0) >= max_per_track:
                continue
            if appearances.get(pair.b, 0) >= max_per_track:
                continue
            capped.append(pair)
            appearances[pair.a] = appearances.get(pair.a, 0) + 1
            appearances[pair.b] = appearances.get(pair.b, 0) + 1
        pairs = capped

    return pairs


def candidates_from_rows(rows: Iterable[dict]) -> list[Candidate]:
    """Keep only rows that carry both a readable tempo and a Camelot code."""
    out: list[Candidate] = []
    for row in rows:
        bpm, camelot = row.get("bpm"), row.get("camelot")
        if not bpm or not camelot:
            continue
        try:
            bpm_value = float(bpm)
        except (TypeError, ValueError):
            continue
        if bpm_value <= 0:
            continue
        out.append(
            Candidate(
                index=row["index"],
                track_id=row["id"],
                bpm=bpm_value,
                camelot=camelot,
                artist_key=fold(row.get("primary_artist") or row.get("artist") or ""),
            )
        )
    return out
