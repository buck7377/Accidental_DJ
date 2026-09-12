# How it works

Notes on the internals, for anyone changing the code.
The [README](README.md) is the one you want for using it.

## Matching

**Keys** are converted to [Camelot](https://mixedinkey.com/harmonic-mixing-guide/)
notation (number = wheel position, letter = ring; `A` minor, `B` major). Two
tracks are key-compatible if they:

- share the same Camelot code, or
- share the number but differ in letter (relative major/minor), or
- sit one step apart on the same letter ring, wrapping between 12 and 1.

**Tempos** are compatible if the two BPMs are within the tolerance of each
other, or if one is within tolerance of double or half the other. Half and
double time matches are included and labeled — they are how a 78 BPM ballad
mixes into a 155 BPM trap record.

**Ranking** is tempo drift ascending, then same-key pairs first. Nothing else.
No scoring, no weighting, no filtering by genre, mood, era or similarity.

Pairs sharing a primary artist are excluded by default (an artist mixing with
themselves is not a surprise), and each track appears in at most
`--max-per-track` pairs so one song in a crowded key and tempo cannot flood
the list.

## Tests

```bash
python -m unittest discover -s tests
```

No network or API key required; the GetSongBPM client is tested against a
mocked HTTP layer.

## Layout

| file | does |
| --- | --- |
| `camelot.py` | key parsing, Camelot conversion, compatibility rules |
| `matching.py` | pairing, ranking, the per-track cap, the chain search |
| `textnorm.py` | title cleaning, primary artist |
| `getsongbpm.py` | GetSongBPM client, rate limiting, match confidence |
| `spotify.py` | liked songs, playlist creation |
| `db.py` | SQLite cache |
| `display.py` / `export.py` | terminal output, file formats |
| `cli.py` | subcommands |

Spotify's February 2026 migration retired `POST /users/{id}/playlists` and
renamed `POST /playlists/{id}/tracks` to `.../items`; spotipy still calls the
retired paths, so `spotify.py` calls the current ones directly. Its
audio-features and audio-analysis endpoints were deprecated on 2024-11-27 and
are never used.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

No network or API key needed; the API clients are tested against mocks.
