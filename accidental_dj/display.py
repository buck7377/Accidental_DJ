"""Terminal rendering for transitions and sets."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional, Sequence

from .camelot import split_camelot
from .matching import Pair, ratio_label

# One 256-colour code per Camelot number, walking the wheel the way the old
# HTML page walked hues. The A ring prints plain, the B ring bold, so the two
# rings stay distinguishable without inventing 24 colours.
_WHEEL = {1: 196, 2: 202, 3: 214, 4: 220, 5: 190, 6: 118,
          7: 48, 8: 45, 9: 39, 10: 63, 11: 135, 12: 205}


def use_color(flag: Optional[bool] = None) -> bool:
    """Colour when talking to a terminal, unless NO_COLOR or --no-color says not to."""
    if flag is False:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if flag is True:
        return True
    return sys.stdout.isatty()


def camelot(code: Optional[str], color: bool = True) -> str:
    text = f"{code or '-':>3}"
    parts = split_camelot(code or "")
    if not color or parts is None:
        return text
    number, letter = parts
    bold = "1;" if letter == "B" else ""
    return f"\033[{bold}38;5;{_WHEEL[number]}m{text}\033[0m"


def dim(text: str, color: bool = True) -> str:
    return f"\033[2m{text}\033[0m" if color else text


def width() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def trim(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def transition_line(pair: Pair, tracks: Sequence[dict], color: bool = True) -> str:
    """One ranked transition, sized to the terminal."""
    a, b = tracks[pair.a], tracks[pair.b]
    label = ratio_label(pair.ratio)
    # Fixed columns: drift, keys, bpms, label. The rest goes to the titles.
    head = (f"{pair.drift:5.2f}%  {camelot(a['camelot'], color)}"
            f"{dim('→', color)}{camelot(b['camelot'], color)}  "
            f"{float(a['bpm']):5.1f}{dim('→', color)}{float(b['bpm']):<5.1f} "
            f"{label:<11}")
    years_text = f"({a['release_year'] or '?'}→{b['release_year'] or '?'})"
    # Measure the fixed part rather than estimating it, so no width is wasted.
    fixed = len(f"{pair.drift:5.2f}%  ---→---  {float(a['bpm']):5.1f}→"
                f"{float(b['bpm']):<5.1f} {label:<11}") + len(years_text) + 5
    # Split what is left between the two sides, giving the title about three
    # fifths of each side and the artist the rest.
    side = max(20, (width() - fixed) // 2)
    title_room = max(12, side * 3 // 5)
    artist_room = max(8, side - title_room - 3)
    left = f"{trim(a['title'], title_room)} — {trim(a['primary_artist'], artist_room)}"
    right = f"{trim(b['title'], title_room)} — {trim(b['primary_artist'], artist_room)}"
    return f"{head} {left:<{side}} {dim('→', color)} {right} {dim(years_text, color)}"


def set_line(position: int, track: dict, color: bool = True) -> str:
    return (f"{position:>3}. {float(track['bpm']):6.1f} {camelot(track['camelot'], color)}  "
            f"{trim(track['title'], 42)} — {trim(track['primary_artist'], 24)}")
