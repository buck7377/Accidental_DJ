# Accidental DJ

Accidental DJ finds songs in your Spotify library that mix into each other. It matches them on key and tempo, then builds a playlist from the longest run of songs it can chain together.

The tool ignores genre, mood, and era. It compares key and tempo, nothing else. You get pairings like these:

| It pairs this | With this |
| --- | --- |
| Kanye West, "Everything I Am" (2007, 80 BPM) | Frontierer, "Glitcher" (2018, 160 BPM) |
| Hank Williams, "Lost Highway" (1951, 132 BPM) | BlocBoy JB, "Shoot" (2017, 135 BPM) |
| Marvin Gaye, "Distant Lover" (1973, 136 BPM) | La Bouche, "Be My Lover" (1995, 136 BPM) |
| Pantera, "I'm Broken" (1994, 144 BPM) | Lady Gaga, "Speechless" (2009, 144 BPM) |

Every pair shares a compatible key and sits within a few BPM of its partner, so one track slides into the next.

## What you need

- A Mac or Linux computer.
- A Spotify account, free or paid.
- About ten minutes.

Both logins cost nothing, and the setup script tells you where to click.

## Setup

```bash
git clone https://github.com/buck7377/Accidental_DJ.git
cd Accidental_DJ
./setup.sh
```

The script installs what the tool needs, then asks for two logins and saves them on your computer.

Spotify covers reading your liked songs and creating playlists. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard), add `http://127.0.0.1:8888/callback` as the redirect URI, tick Web API, then copy the Client ID and Client Secret.

GetSongBPM supplies the tempo and key of each song. Request a free key at [getsongbpm.com/api](https://getsongbpm.com/api). The key arrives by email.

Confirm both logins work:

```bash
./dj check
```

You want `ok` twice. If one fails, the message names the fix.

## Using it

Four commands, run in order.

### Read your liked songs

```bash
./dj sync
```

A browser opens and asks you to approve access to your own account. Approve it. You land on a page that fails to load, which is normal, so close it.

Run the same command again whenever you like new songs. It picks up only what changed.

### Look up tempo and key

```bash
./dj enrich
```

The slow step. Each song takes one lookup, at roughly 45 songs per minute, so a 1,000 song library finishes in about 22 minutes. The screen shows progress and time remaining.

Press Ctrl-C to stop at any point. Run the command again to pick up where you left off.

Some songs come back empty because the tempo database doesn't list them. The tool skips those and moves on.

### See what it found

```bash
./dj transitions
```

The closest tempo matches come first. Narrow the list:

```bash
./dj transitions --limit 100        # show more than the default 40
./dj transitions --same-key-only    # only the smoothest matches
./dj transitions --search "1974"    # only pairs involving a year, artist, or song
```

### Build the playlist

```bash
./dj playlist
```

The tool finds the longest run of songs where each one flows into the next. It prints the running order and asks before it creates anything. Answer yes, and the playlist appears in Spotify within seconds.

Name it yourself:

```bash
./dj playlist --name "Saturday Night"
```

## Adjusting the results

Tempo tolerance drives everything. The default allows 3%, so a 120 BPM song matches anything from 117 to 123.

Loosen it for more matches:

```bash
./dj playlist --tolerance 6
```

Tighten it for fewer, closer ones:

```bash
./dj playlist --tolerance 1.5
```

Other options:

- `--limit 20` caps how many songs land in the playlist.
- `--public` lists the playlist on your profile. Playlists start private.
- `--dry-run` prints the set and creates nothing.
- `--allow-same-artist` lets an artist match themselves.

Every flag works with `./dj transitions` and `./dj playlist` alike.

## Save to a file

Skip Spotify access entirely:

```bash
./dj playlist --export set.txt --export-only
```

Open `set.txt`, select everything, and copy it. Paste it into an empty playlist in the Spotify desktop app, and the songs land in order.

Export `set.csv` for a spreadsheet, or to move the list into Apple Music or YouTube Music through a service like Soundiiz.

## Questions

**Does it change my Spotify account?**
Only when you ask. It reads your liked songs. It creates a playlist when you run `./dj playlist` and answer yes. It never edits or deletes anything.

**Will the playlist repeat songs?**
No.

**Is the playlist private?**
Yes, unless you add `--public`. Private on Spotify means the playlist skips your profile, though anyone you hand the link to can open it.

**Where do my logins live?**
In a file called `env.sh` inside this folder, on your computer. Git excludes the file, so it never uploads anywhere.

**Why are some songs missing?**
The tempo database doesn't cover everything, and coverage thins out on obscure or brand new music. Run `./dj enrich --retry-misses` to try the skipped songs again.

**Can I run it on another computer?**
Yes. Clone the repo again and run `./setup.sh` with the same two logins.

## Credit

GetSongBPM supplies the tempo and key data. They ask for a link in return, so please keep this one: [getsongbpm.com](https://getsongbpm.com).

Curious about the matching rules? Read [HOW-IT-WORKS.md](HOW-IT-WORKS.md).
