# Accidental DJ

Finds songs in your Spotify library that would mix into each other — same key,
almost identical tempo — no matter how little business they have being played
together, and turns the best run of them into a playlist.

It has no taste and no opinion about genre. It only knows key and tempo, which
is how you end up with a 1951 Hank Williams record flowing into brutal death
metal, or Marvin Gaye into 90s eurodance.

Real examples from one 207-song library:

```
Everything I Am — Kanye West  (2007, 80 BPM)   →  Glitcher — Frontierer (2018, 160 BPM)
Lost Highway — Hank Williams  (1951, 132 BPM)  →  Shoot — BlocBoy JB (2017, 135 BPM)
Distant Lover — Marvin Gaye   (1973, 136 BPM)  →  Be My Lover — La Bouche (1995, 136 BPM)
I'm Broken — Pantera          (1994, 144 BPM)  →  Speechless — Lady Gaga (2009, 144 BPM)
```

## Getting started

You need a Mac or Linux computer and about ten minutes, most of it waiting for
a free API key.

**1. Download it**

```bash
git clone https://github.com/buck7377/Accidental_DJ.git
cd Accidental_DJ
```

**2. Run the setup**

```bash
./setup.sh
```

It installs what it needs and walks you through getting two free logins: one
from Spotify, so it can read your liked songs and make playlists, and one from
GetSongBPM, which is where the tempo and key of each song comes from. It tells
you exactly where to click. Your logins are saved on your own computer and
never leave it.

**3. Check it worked**

```bash
./dj check
```

Should say `ok` twice. If not, it tells you what to fix.

## Using it

**Read your liked songs.** Takes a few seconds.

```bash
./dj sync
```

The first time, a browser window asks you to approve access to your own
Spotify account. Approve it, and you'll land on a page that fails to load —
that's normal, you can close it. Run this again any time you want to pick up
songs you have liked since.

**Look up every song's tempo and key.** This is the slow one.

```bash
./dj enrich
```

About a minute for every 45 songs, so a 1,000 song library takes roughly
twenty minutes. It shows progress and how long is left. You can stop it at any
time with Ctrl-C and pick up where you left off by running it again.

Expect some songs to come back empty — the tempo database does not have
everything, and it thins out on very obscure or very new music. Those songs
are simply left out.

**See what it found.**

```bash
./dj transitions
```

Lists the pairs, closest tempo match first. Some ways to narrow it down:

```bash
./dj transitions --limit 100          # show more than the default 40
./dj transitions --same-key-only      # only the smoothest matches
./dj transitions --search "1974"      # only pairs involving a year, artist or song
```

**Make the playlist.**

```bash
./dj playlist
```

It finds the longest run of songs where each one flows into the next, shows
you the running order, and asks before creating anything. Say yes and it
appears in your Spotify within a few seconds. Nothing is created unless you
say yes.

To name it:

```bash
./dj playlist --name "Saturday Night"
```

## Adjusting it

**Not enough results?** Loosen how close the tempos must be. The default is 3%,
meaning a 120 BPM song matches roughly 117–123.

```bash
./dj transitions --tolerance 6
./dj playlist --tolerance 6
```

**Too loose?** Tighten it the same way with `--tolerance 1.5`. You will get
fewer, better matches.

**Other options**

```bash
./dj playlist --limit 20            # a shorter playlist
./dj playlist --public              # anyone can find it (private by default)
./dj playlist --dry-run             # show it, create nothing
./dj transitions --allow-same-artist    # let an artist match themselves
```

## Saving a playlist to a file instead

If you would rather not let it touch your Spotify account:

```bash
./dj playlist --export set.txt --export-only
```

Open `set.txt`, select everything, copy, and paste it into an empty playlist in
the Spotify desktop app — the songs appear in order. Use `set.csv` instead if
you want a spreadsheet, or to move the playlist to Apple Music or YouTube Music
through a service like Soundiiz.

## Questions

**Does it change anything in my Spotify?** Only if you ask. It reads your liked
songs, and creates a playlist when you run `./dj playlist` and answer yes. It
never edits or deletes anything.

**Will the playlist have duplicates?** No.

**Where are my logins stored?** In a file called `env.sh` in this folder, on
your computer only. It is excluded from uploads.

**Some songs are missing from the results.** Either the tempo database does not
have them, or they have no key or tempo that matches anything else in your
library. Run `./dj enrich --retry-misses` to try the missing ones again.

**Can I run it on a different computer?** Yes. Copy the folder, or clone it
again and run `./setup.sh` with the same two logins.

## Credit

Tempo and key data comes from [GetSongBPM](https://getsongbpm.com), free for
this kind of use as long as they get a link. Please leave this one here.

Curious how the matching works? See [HOW-IT-WORKS.md](HOW-IT-WORKS.md).
