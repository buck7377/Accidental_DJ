# Accidental DJ

Finds pairs of songs in your Spotify Liked Songs that would mix into each
other — same key, near-identical tempo — regardless of whether they have any
business being played together. A 1968 country record sitting 0.4% away from a
2017 trap song at double time is the point, not a bug.

The matcher is deliberately blind to genre, mood, era, popularity and every
other notion of similarity. The only things it looks at are the Camelot key
and the BPM.

Tempo, key and time signature data provided by
**[GetSongBPM](https://getsongbpm.com)**.

## Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then run everything with `.venv/bin/python` in place of `python`, or activate the
venv first with `source .venv/bin/activate`. A venv is required on distros that
mark the system Python as externally managed (Arch, Debian, Fedora — PEP 668);
it is good practice everywhere else.

Spotify credentials — create an app at
[developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and
add a redirect URI:

```bash
export SPOTIPY_CLIENT_ID=...
export SPOTIPY_CLIENT_SECRET=...
export SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

Tempo and key come from [GetSongBPM](https://getsongbpm.com/api) (free key):

```bash
export GETSONGBPM_API_KEY=...
```

Spotify's `audio-features` and `audio-analysis` endpoints were deprecated on
2024-11-27 and return 403 to any app created after that date, so this tool
never calls them.

## Use

```bash
source .venv/bin/activate                       # or prefix each with .venv/bin/

python -m accidental_dj sync                    # pull Liked Songs into SQLite
python -m accidental_dj enrich                  # look up tempo + key (slow)
python -m accidental_dj build                   # write transitions.html
python -m accidental_dj playlist --dry-run      # preview a continuous set
```

Then open `transitions.html` in a browser. It is a single file with the data
embedded — no server, no build step.

### sync

Reads every saved track via `GET /v1/me/tracks` with the `user-library-read`
scope and stores id, title, artist, album, release year, duration, popularity
and ISRC. Re-running updates in place. `--prune` drops cached tracks you have
since unliked. Local files are skipped.

### enrich

One GetSongBPM lookup per track, rate limited to ~1.3s per request
(`--delay`) — their published ceiling is 3,000 requests/hour, so this stays
comfortably inside it. Before searching, release noise is stripped from the title
(`- 2015 Remaster`, `(Live at ...)`, `(Radio Edit)`, `(feat. ...)`, bare
parenthetical years) and only the primary artist is used.

Results are committed after every track, so Ctrl-C is safe and re-running
resumes where it stopped. Failures and misses are cached too, so re-runs skip
them; `--retry-misses` tries them again. `--limit N` stops after N lookups,
which is useful for a first test.

Expect misses. A library of a few thousand tracks takes an hour or so.

### build

| flag | default | meaning |
| --- | --- | --- |
| `--tolerance` | `3.0` | maximum tempo drift, as a percentage |
| `--max-per-track` | `6` | cap on pairs one track can appear in (`0` = uncapped) |
| `--allow-same-artist` | off | include pairs sharing a primary artist |
| `--no-half-double` | off | exclude half-time and double-time matches |
| `--out` | `transitions.html` | output path |

The page's drift slider filters *within* the tolerance you built with, so
building at `--tolerance 6` and sliding down is more flexible than rebuilding.

### playlist

Treats the transitions as a graph and walks it for the longest run where every
consecutive track is a real key-and-tempo match, then creates that as a Spotify
playlist in order — so it plays as one continuous set rather than a list of
disconnected pairs.

```bash
python -m accidental_dj playlist --tolerance 6 --dry-run     # print it, write nothing
python -m accidental_dj playlist --tolerance 6 --name "Set 1" # create it
```

Takes the same matching flags as `build`, plus `--name`, `--limit`, `--public`
(private by default), `--dry-run`, `--yes` to skip the confirmation, and
`--search-budget`.

Longest-simple-path is NP-hard, so the search is a bounded depth-first walk
rather than a proof of optimality. The budget matters: on a 107-track library
300k steps found a 33-track chain and 2M found 40, so the default is 2M
(about half a second). Raise it for a big library.

This is the only command that writes anything. It needs the
`playlist-modify-private` scope on top of the read-only one, so the first run
re-opens the browser for authorization; afterwards the broader token covers
`sync` too.

## The matching logic

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

## The page

Two tabs — Transitions and All tracks — both sortable by clicking a column
header (click again to reverse). Above the transitions table: a search box
covering title, artist and year; a live maximum-drift slider; toggles for
half/double time and same-key-only.

Camelot codes are colored by wheel position — 12 numbers to 12 hues, with
lightness separating the A and B rings — so compatible clusters are visible at
a glance.

## Tests

```bash
python -m unittest discover -s tests
```

No network or API key required; the GetSongBPM client is tested against a
mocked HTTP layer.

## Attribution

Tempo and key data come from [GetSongBPM](https://getsongbpm.com). Their API
terms require a visible backlink wherever the data is displayed — it is in the
generated page's footer. Please leave it there.
