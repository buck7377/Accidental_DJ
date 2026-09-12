#!/usr/bin/env bash
# One-time setup: installs what the tool needs and saves your logins.
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  Accidental DJ — setup"
echo "  ---------------------"
echo

# --- Python -----------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "  Python 3 isn't installed. Install it, then run this again:"
  echo "    Arch/EndeavourOS  sudo pacman -S python"
  echo "    Ubuntu/Debian     sudo apt install python3 python3-venv"
  echo "    macOS             brew install python"
  exit 1
fi

version=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "  Python $version is too old. This needs 3.9 or newer."
  exit 1
fi

echo "  [1/3] Python $version — ok"

# --- Dependencies -----------------------------------------------------
echo "  [2/3] Installing what it needs (about 15 seconds)..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
echo "        done"

# --- Credentials ------------------------------------------------------
if [ -f env.sh ] && grep -qE '^export SPOTIPY_CLIENT_ID=.+' env.sh; then
  echo "  [3/3] Your logins are already saved in env.sh"
  echo
  read -r -p "        Replace them? [y/N] " replace
  if [ "${replace,,}" != "y" ]; then
    echo
    echo "  All set. Try:  ./dj sync"
    echo
    exit 0
  fi
fi

cat <<'INSTRUCTIONS'
  [3/3] Now your logins. You need two, both free.

  SPOTIFY — so it can read your liked songs and make playlists
    1. Go to  https://developer.spotify.com/dashboard  and log in
    2. Click "Create app". Name it anything.
    3. In "Redirect URI" type exactly:  http://127.0.0.1:8888/callback
       then click Add. (It must be 127.0.0.1, not "localhost".)
    4. Tick "Web API", agree to the terms, and save
    5. Open the app's Settings — copy the Client ID, then click
       "View client secret" and copy that too

  GETSONGBPM — where the tempo and key of each song comes from
    6. Go to  https://getsongbpm.com/api  and fill in the short form.
       It asks for a website to link back to; a personal site or a
       GitHub page is fine. The key arrives by email.

  Paste each one below. Nothing is shown as you type the secrets.

INSTRUCTIONS

read -r -p "  Spotify Client ID:     " client_id
read -r -s -p "  Spotify Client Secret: " client_secret; echo
read -r -s -p "  GetSongBPM API Key:    " songbpm_key; echo

if [ -z "$client_id" ] || [ -z "$client_secret" ] || [ -z "$songbpm_key" ]; then
  echo
  echo "  One of those was empty. Run ./setup.sh again when you have all three."
  exit 1
fi

umask 077
cat > env.sh <<CREDENTIALS
# Your logins. Private to this computer -- never uploaded anywhere.
export SPOTIPY_CLIENT_ID=$client_id
export SPOTIPY_CLIENT_SECRET=$client_secret
export SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
export GETSONGBPM_API_KEY=$songbpm_key
CREDENTIALS
chmod 600 env.sh

echo
echo "  Saved. Everything is ready."
echo
echo "  Next:  ./dj sync      reads your liked songs"
echo "         ./dj enrich    looks up every song's tempo and key"
echo "         ./dj playlist  builds the playlist"
echo
