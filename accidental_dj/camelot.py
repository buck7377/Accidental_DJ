"""Key parsing and Camelot wheel arithmetic.

Camelot notation puts the 24 keys on a clock face: the number (1-12) is the
position, the letter is the ring (A = minor, B = major). Anything that is one
step around the same ring, or straight across the rings at the same number,
mixes cleanly.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Pitch class of every spelling we are likely to see from GetSongBPM.
_PITCH = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "E#": 5, "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
    "A#": 10, "BB": 10, "B": 11, "CB": 11, "B#": 0,
}

# Camelot number for each pitch class, per ring.
_MAJOR_NUMBER = {0: 8, 1: 3, 2: 10, 3: 5, 4: 12, 5: 7, 6: 2, 7: 9, 8: 4, 9: 11, 10: 6, 11: 1}
_MINOR_NUMBER = {0: 5, 1: 12, 2: 7, 3: 2, 4: 9, 5: 4, 6: 11, 7: 6, 8: 1, 9: 8, 10: 3, 11: 10}

_MINOR_MARKERS = ("MINOR", "MIN", "M")


def parse_key(raw: Optional[str]) -> Optional[Tuple[int, bool]]:
    """Parse a key string into (pitch_class, is_minor). None if unparseable.

    Handles ``Am``, ``A min``, ``A minor``, ``F#m``, ``Db``, ``D♭ Major``,
    ``Bbm``, and the unicode accidentals GetSongBPM sometimes returns.
    """
    if not raw:
        return None
    text = str(raw).strip().upper()
    text = text.replace("♭", "B").replace("♯", "#")
    text = text.replace("-FLAT", "B").replace("-SHARP", "#")
    text = text.replace(" FLAT", "B").replace(" SHARP", "#")
    if not text:
        return None

    root = text[0]
    if root not in "ABCDEFG":
        return None
    rest = text[1:].strip()

    # Accidental, if any, belongs to the root.
    if rest[:1] in ("#", "B"):
        root += rest[:1]
        rest = rest[1:].strip()

    pitch = _PITCH.get(root)
    if pitch is None:
        return None

    rest = rest.lstrip(" -/")
    is_minor = False
    if rest:
        if rest.startswith("MAJ") or rest.startswith("MAJOR"):
            is_minor = False
        elif any(rest.startswith(marker) for marker in _MINOR_MARKERS):
            is_minor = True
    return pitch, is_minor


def to_camelot(raw: Optional[str]) -> Optional[str]:
    """``'F#m'`` -> ``'11A'``. Returns None when the key cannot be read."""
    parsed = parse_key(raw)
    if parsed is None:
        return None
    pitch, is_minor = parsed
    if is_minor:
        return f"{_MINOR_NUMBER[pitch]}A"
    return f"{_MAJOR_NUMBER[pitch]}B"


def split_camelot(code: Optional[str]) -> Optional[Tuple[int, str]]:
    """``'11A'`` -> ``(11, 'A')``."""
    if not code or len(code) < 2:
        return None
    number, letter = code[:-1], code[-1].upper()
    if letter not in ("A", "B") or not number.isdigit():
        return None
    value = int(number)
    if not 1 <= value <= 12:
        return None
    return value, letter


def compatible_codes(code: str) -> list[str]:
    """Every Camelot code that mixes with ``code``, including itself."""
    parts = split_camelot(code)
    if parts is None:
        return []
    number, letter = parts
    other = "B" if letter == "A" else "A"
    up = number % 12 + 1
    down = (number - 2) % 12 + 1
    return [f"{number}{letter}", f"{number}{other}", f"{up}{letter}", f"{down}{letter}"]


def keys_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """Same code, relative major/minor, or one step around the same ring."""
    if not a or not b:
        return False
    return b in compatible_codes(a)


def camelot_relation(a: str, b: str) -> str:
    """Label for how two compatible codes relate."""
    if a == b:
        return "same key"
    pa, pb = split_camelot(a), split_camelot(b)
    if pa is None or pb is None:
        return "unrelated"
    if pa[0] == pb[0]:
        return "relative"
    if pa[1] == pb[1]:
        return "neighbour"
    return "unrelated"
